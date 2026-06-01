from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import ccxt.async_support as ccxt
import pandas as pd

from src.core.config import ExchangeConfig

logger = logging.getLogger("trading.data.exchange")

EXCHANGE_CLASSES: dict[str, type] = {
    "binance": ccxt.binance,
    "bybit": ccxt.bybit,
}


class ExchangeClient:
    """Async wrapper around ccxt for market data and order execution."""

    def __init__(self, config: ExchangeConfig):
        cls = EXCHANGE_CLASSES.get(config.name)
        if cls is None:
            raise ValueError(f"Exchange '{config.name}' not supported. Use: {list(EXCHANGE_CLASSES)}")

        opts: dict[str, Any] = {
            "enableRateLimit": True,
            "rateLimit": config.rate_limit,
            "timeout": config.timeout,
        }
        if config.api_key:
            opts["apiKey"] = config.api_key
            opts["secret"] = config.secret

        self.exchange: ccxt.Exchange = cls(opts)
        if config.sandbox and hasattr(self.exchange, "set_sandbox_mode"):
            self.exchange.set_sandbox_mode(True)

        self._name = config.name
        logger.info("Exchange client created: %s (sandbox=%s)", config.name, config.sandbox)

    async def close(self) -> None:
        await self.exchange.close()

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1m",
        since: int | None = None,
        limit: int = 1000,
    ) -> pd.DataFrame:
        raw = await self.exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.set_index("timestamp")
        return df

    async def fetch_full_history(
        self,
        symbol: str,
        timeframe: str = "1h",
        days: int = 365,
        batch_size: int = 1000,
    ) -> pd.DataFrame:
        """Fetch full OHLCV history going back `days`, paginating automatically."""
        tf_ms = self.exchange.parse_timeframe(timeframe) * 1000
        now = int(datetime.now(timezone.utc).timestamp() * 1000)
        start = now - (days * 24 * 60 * 60 * 1000)

        all_frames: list[pd.DataFrame] = []
        cursor = start

        while cursor < now:
            df = await self.fetch_ohlcv(symbol, timeframe, since=cursor, limit=batch_size)
            if df.empty:
                break
            all_frames.append(df)
            last_ts = int(df.index[-1].timestamp() * 1000)
            cursor = last_ts + tf_ms
            logger.debug(
                "Fetched %d candles for %s %s (up to %s)",
                len(df), symbol, timeframe, df.index[-1],
            )
            await asyncio.sleep(self.exchange.rateLimit / 1000)

        if not all_frames:
            return pd.DataFrame()

        result = pd.concat(all_frames)
        result = result[~result.index.duplicated(keep="last")]
        result = result.sort_index()
        logger.info(
            "Full history: %s %s — %d candles (%s → %s)",
            symbol, timeframe, len(result), result.index[0], result.index[-1],
        )
        return result

    async def fetch_orderbook(self, symbol: str, limit: int = 20) -> dict[str, Any]:
        ob = await self.exchange.fetch_order_book(symbol, limit=limit)
        return {
            "symbol": symbol,
            "bids": ob["bids"][:limit],
            "asks": ob["asks"][:limit],
            "timestamp": ob.get("timestamp"),
            "bid_depth": sum(b[1] for b in ob["bids"][:limit]),
            "ask_depth": sum(a[1] for a in ob["asks"][:limit]),
        }

    async def fetch_ticker(self, symbol: str) -> dict[str, Any]:
        return await self.exchange.fetch_ticker(symbol)

    async def fetch_recent_trades(self, symbol: str, limit: int = 100) -> pd.DataFrame:
        raw = await self.exchange.fetch_trades(symbol, limit=limit)
        trades = [
            {
                "timestamp": t["timestamp"],
                "price": t["price"],
                "amount": t["amount"],
                "side": t["side"],
            }
            for t in raw
        ]
        df = pd.DataFrame(trades)
        if not df.empty:
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            df = df.set_index("timestamp")
        return df
