"""Polymarket CLOB API client — fetches per-token price history.

Reference: https://docs.polymarket.com (CLOB)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import aiohttp
import pandas as pd

logger = logging.getLogger("trading.polymarket.clob")

CLOB_BASE = "https://clob.polymarket.com"


class ClobClient:
    """Read-only async client for Polymarket CLOB public endpoints.

    Only `/prices-history` is needed for backtesting edge against historical
    market prices. Live trading would also need `/book`, `/midpoint`, etc.
    """

    def __init__(self, session: aiohttp.ClientSession | None = None, timeout: int = 30):
        self._owns_session = session is None
        self._session = session or aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout)
        )

    async def close(self) -> None:
        if self._owns_session:
            await self._session.close()

    async def __aenter__(self) -> "ClobClient":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.close()

    async def _get(self, path: str, params: dict[str, Any]) -> Any:
        url = f"{CLOB_BASE}{path}"
        for attempt in range(4):
            async with self._session.get(url, params=params) as resp:
                if resp.status == 200:
                    return await resp.json()
                if resp.status == 429 and attempt < 3:
                    await asyncio.sleep(1.0 + attempt)
                    continue
                if resp.status >= 500 and attempt < 3:
                    await asyncio.sleep(0.5 * (2 ** attempt))
                    continue
                text = await resp.text()
                raise RuntimeError(f"CLOB GET {url} -> {resp.status}: {text[:200]}")
        return None

    async def fetch_price_history(
        self,
        token_id: str,
        start_utc: datetime,
        end_utc: datetime,
        fidelity_min: int = 1,
    ) -> pd.DataFrame:
        """Fetch a token's mid-price history at ~fidelity_min resolution.

        Returns a DataFrame indexed by UTC timestamp with one column 'price' in [0, 1].
        Empty DataFrame if the token has no history in the window.
        """
        params = {
            "market": token_id,
            "startTs": int(start_utc.timestamp()),
            "endTs": int(end_utc.timestamp()),
            "fidelity": fidelity_min,
        }
        data = await self._get("/prices-history", params)
        history = (data or {}).get("history", []) if isinstance(data, dict) else []
        if not history:
            return pd.DataFrame(columns=["price"])

        df = pd.DataFrame(history)
        df["timestamp"] = pd.to_datetime(df["t"], unit="s", utc=True)
        df = df.rename(columns={"p": "price"})[["timestamp", "price"]]
        df = df.set_index("timestamp").sort_index()
        # Polymarket midpoints are quoted in dollars (0..1). Clip just in case.
        df["price"] = df["price"].astype(float).clip(0.0, 1.0)
        return df
