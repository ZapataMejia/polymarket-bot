from __future__ import annotations

import asyncio
import logging

from src.core.config import Config
from src.data.exchange import ExchangeClient
from src.data.storage import ParquetStorage

logger = logging.getLogger("trading.data.collector")


class DataCollector:
    """Orchestrates data collection across symbols and timeframes."""

    def __init__(self, config: Config):
        self.config = config
        self.client = ExchangeClient(config.exchange)
        self.storage = ParquetStorage(config.data_storage_path)

    async def close(self) -> None:
        await self.client.close()

    async def collect_history(self, symbol: str, timeframe: str) -> int:
        logger.info("Collecting history: %s %s (%d days)", symbol, timeframe, self.config.history_days)
        df = await self.client.fetch_full_history(
            symbol, timeframe, days=self.config.history_days
        )
        if df.empty:
            return 0
        self.storage.save(df, symbol, timeframe)
        return len(df)

    async def collect_all_history(self) -> dict[str, int]:
        results: dict[str, int] = {}
        for symbol in self.config.symbols:
            for tf in self.config.timeframes:
                key = f"{symbol}_{tf}"
                try:
                    count = await self.collect_history(symbol, tf)
                    results[key] = count
                    logger.info("✓ %s: %d candles", key, count)
                except Exception:
                    logger.exception("✗ Failed to collect %s", key)
                    results[key] = 0
        return results

    async def collect_latest(self, symbol: str, timeframe: str, limit: int = 100) -> int:
        df = await self.client.fetch_ohlcv(symbol, timeframe, limit=limit)
        if df.empty:
            return 0
        self.storage.save(df, symbol, timeframe)
        return len(df)

    async def run_continuous(self, interval_seconds: int = 60) -> None:
        """Continuously collect latest candles for all symbols/timeframes."""
        logger.info("Starting continuous collection (interval=%ds)", interval_seconds)
        while True:
            for symbol in self.config.symbols:
                for tf in self.config.timeframes:
                    try:
                        await self.collect_latest(symbol, tf, limit=5)
                    except Exception:
                        logger.exception("Error collecting %s %s", symbol, tf)
            await asyncio.sleep(interval_seconds)
