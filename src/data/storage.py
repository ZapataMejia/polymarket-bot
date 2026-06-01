from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger("trading.data.storage")


class ParquetStorage:
    """Read/write DataFrames to Parquet files organized by symbol and timeframe."""

    def __init__(self, base_path: str = "data/raw"):
        self.base = Path(base_path)
        self.base.mkdir(parents=True, exist_ok=True)

    def _path(self, symbol: str, timeframe: str) -> Path:
        safe_symbol = symbol.replace("/", "_")
        return self.base / f"{safe_symbol}_{timeframe}.parquet"

    def save(self, df: pd.DataFrame, symbol: str, timeframe: str) -> Path:
        if df.empty:
            logger.warning("Empty DataFrame, skipping save for %s %s", symbol, timeframe)
            return self._path(symbol, timeframe)

        path = self._path(symbol, timeframe)
        existing = self.load(symbol, timeframe)

        if not existing.empty:
            combined = pd.concat([existing, df])
            combined = combined[~combined.index.duplicated(keep="last")]
            combined = combined.sort_index()
        else:
            combined = df

        combined.to_parquet(path, engine="pyarrow")
        logger.info("Saved %d rows to %s", len(combined), path)
        return path

    def load(self, symbol: str, timeframe: str) -> pd.DataFrame:
        path = self._path(symbol, timeframe)
        if not path.exists():
            return pd.DataFrame()
        df = pd.read_parquet(path, engine="pyarrow")
        logger.debug("Loaded %d rows from %s", len(df), path)
        return df

    def list_files(self) -> list[Path]:
        return sorted(self.base.glob("*.parquet"))
