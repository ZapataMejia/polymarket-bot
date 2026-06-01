"""Binance 1-minute kline fetcher tuned for short windows in Polymarket edge analysis.

Wraps the existing ExchangeClient so we don't duplicate ccxt setup. Caches results in
memory and on disk (parquet) since many adjacent markets share the same windows.

Live-mode behavior: blocks whose window includes "now" are NEVER persisted to memory
or disk (they're always re-fetched). This is required for the paper-trading daemon
to see freshly-minted minute bars without stale-cache lag.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from src.data.exchange import ExchangeClient

logger = logging.getLogger("trading.polymarket.binance")


class BinanceKlineCache:
    """Async-friendly cache over ExchangeClient.fetch_ohlcv for 1-minute candles.

    Granularity: aligns requests to 6-hour blocks so the same block is downloaded once
    even if multiple markets request slightly different windows inside it.
    """

    BLOCK = timedelta(hours=6)

    def __init__(
        self,
        client: ExchangeClient,
        cache_dir: Path | str | None = None,
    ):
        self.client = client
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._mem: dict[tuple[str, datetime], pd.DataFrame] = {}
        self._locks: dict[tuple[str, datetime], asyncio.Lock] = {}

    def _block_start(self, ts: datetime) -> datetime:
        epoch = datetime(1970, 1, 1, tzinfo=ts.tzinfo)
        seconds = int((ts - epoch).total_seconds())
        block_sec = int(self.BLOCK.total_seconds())
        return epoch + timedelta(seconds=(seconds // block_sec) * block_sec)

    def _cache_path(self, symbol: str, block: datetime) -> Path | None:
        if not self.cache_dir:
            return None
        safe = symbol.replace("/", "_")
        return self.cache_dir / f"{safe}_{block.strftime('%Y%m%dT%H%M')}.parquet"

    def _is_live_block(self, block_start: datetime) -> bool:
        """A block is 'live' if its END is in the future or within the last 90s."""
        now = datetime.now(timezone.utc)
        block_end = block_start + self.BLOCK
        return block_end > now - timedelta(seconds=90)

    async def _fetch_block(self, symbol: str, block_start: datetime) -> pd.DataFrame:
        key = (symbol, block_start)
        is_live = self._is_live_block(block_start)
        # Memory + disk cache only for fully sealed (historical) blocks.
        if not is_live and key in self._mem:
            return self._mem[key]
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            if not is_live and key in self._mem:
                return self._mem[key]
            path = self._cache_path(symbol, block_start)
            if not is_live and path and path.exists():
                df = pd.read_parquet(path)
                df.index = pd.to_datetime(df.index, utc=True)
                self._mem[key] = df
                return df

            since_ms = int(block_start.timestamp() * 1000)
            # 6h * 60 = 360 candles, well under the 1000 limit.
            df = await self.client.fetch_ohlcv(
                symbol, "1m", since=since_ms, limit=400,
            )
            # Trim to the block range exactly.
            block_end = block_start + self.BLOCK
            df = df[(df.index >= block_start) & (df.index < block_end)]
            # Only persist sealed blocks; live (current) blocks stay ephemeral so we
            # always see the latest minute bar on the next call.
            if not is_live:
                self._mem[key] = df
                if path is not None and not df.empty:
                    df.to_parquet(path)
            logger.debug(
                "binance %s block %s (live=%s) -> %d rows",
                symbol, block_start.isoformat(), is_live, len(df),
            )
            return df

    async def fetch_klines(
        self,
        symbol: str,
        start_utc: datetime,
        end_utc: datetime,
    ) -> pd.DataFrame:
        """Return 1-min OHLCV for [start_utc, end_utc] inclusive (UTC tz-aware index)."""
        cursor = self._block_start(start_utc)
        frames: list[pd.DataFrame] = []
        while cursor <= end_utc:
            frames.append(await self._fetch_block(symbol, cursor))
            cursor = cursor + self.BLOCK
        if not frames:
            return pd.DataFrame()
        df = pd.concat([f for f in frames if not f.empty])
        if df.empty:
            return df
        df = df[~df.index.duplicated(keep="last")].sort_index()
        return df[(df.index >= start_utc) & (df.index <= end_utc)]
