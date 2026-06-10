"""Polymarket Gamma API client — discovers resolved Up/Down crypto markets.

Reference: https://gamma-api.polymarket.com
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiohttp

logger = logging.getLogger("trading.polymarket.gamma")

GAMMA_BASE = "https://gamma-api.polymarket.com"
_ONE_DAY = timedelta(days=1)

# Maps the asset name in the market question to a Binance ccxt symbol.
ASSET_TO_BINANCE = {
    "bitcoin": "BTC/USDT",
    "ethereum": "ETH/USDT",
    "solana": "SOL/USDT",
}


@dataclass
class UpDownMarket:
    """A resolved 'X Up or Down - <date>, <time> ET' market.

    The market resolves UP if `asset_price(window_end) >= asset_price(window_start)`,
    DOWN otherwise. token_id_up is the CLOB token whose price equals P(UP).
    """

    market_id: str
    question: str
    slug: str
    asset: str  # 'bitcoin' / 'ethereum' / 'solana'
    binance_symbol: str
    window_start_utc: datetime
    window_end_utc: datetime
    token_id_up: str
    token_id_down: str
    outcome: str  # 'UP' / 'DOWN' / 'UNKNOWN'
    volume_usd: float

    @property
    def window_seconds(self) -> int:
        return int((self.window_end_utc - self.window_start_utc).total_seconds())


# Examples we want to parse:
#   "Bitcoin Up or Down - May 25, 7:55PM-8:00PM ET"     (5m / 15m markets)
#   "Ethereum Up or Down - May 24, 11:50AM-12:00PM ET"
#   "Bitcoin Up or Down - May 27, 4PM ET"               (hourly markets)
#   "Solana Up or Down - May 27, 4PM ET"
_WINDOW_RE = re.compile(
    r"^(?P<asset>Bitcoin|Ethereum|Solana|XRP|BNB|Dogecoin|Hyperliquid)\s+Up\s+or\s+Down\s*-\s*"
    r"(?P<month>[A-Z][a-z]+)\s+(?P<day>\d{1,2}),\s+"
    r"(?P<sh>\d{1,2})(?::(?P<sm>\d{2}))?(?P<sap>AM|PM)\s*-\s*"
    r"(?P<eh>\d{1,2})(?::(?P<em>\d{2}))?(?P<eap>AM|PM)\s+ET\s*$",
    re.IGNORECASE,
)
_HOURLY_RE = re.compile(
    r"^(?P<asset>Bitcoin|Ethereum|Solana|XRP|BNB|Dogecoin|Hyperliquid)\s+Up\s+or\s+Down\s*-\s*"
    r"(?P<month>[A-Z][a-z]+)\s+(?P<day>\d{1,2}),\s+"
    r"(?P<sh>\d{1,2})(?P<sap>AM|PM)\s+ET\s*$",
    re.IGNORECASE,
)

ASSET_NORMALIZE = {
    "bitcoin": "bitcoin", "btc": "bitcoin",
    "ethereum": "ethereum", "eth": "ethereum",
    "solana": "solana", "sol": "solana",
    "xrp": "xrp",
    "bnb": "bnb",
    "dogecoin": "dogecoin", "doge": "dogecoin",
    "hyperliquid": "hyperliquid", "hype": "hyperliquid",
}

ASSET_TO_BINANCE_EXT = {
    "bitcoin": "BTC/USDT",
    "ethereum": "ETH/USDT",
    "solana": "SOL/USDT",
    "xrp": "XRP/USDT",
    "bnb": "BNB/USDT",
    "dogecoin": "DOGE/USDT",
    "hyperliquid": "HYPE/USDT",
}

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def _et_to_utc(year: int, month_name: str, day: int, hour12: int, minute: int, am_pm: str) -> datetime:
    """Convert ET wall-clock to a tz-aware UTC datetime using a manual DST heuristic.

    Avoids a zoneinfo/tzdata dependency. EDT = UTC-4 from ~2nd Sunday of March
    through ~1st Sunday of November; otherwise EST = UTC-5. Accuracy is fine for
    crypto markets that don't fall exactly on DST transition Sundays.
    """
    m = _MONTHS[month_name.lower()]
    h24 = (hour12 % 12) + (12 if am_pm.upper() == "PM" else 0)

    if 3 <= m <= 11:
        offset_h = 4
        if m == 3 and day < 8:
            offset_h = 5
        if m == 11 and day > 7:
            offset_h = 5
    else:
        offset_h = 5

    naive_et = datetime(year, m, day, h24, minute)
    return (naive_et + timedelta(hours=offset_h)).replace(tzinfo=timezone.utc)


def _parse_outcome(outcome_prices_raw: Any) -> str:
    """outcomePrices is a JSON-encoded list like '["1", "0"]' for ['Up','Down']."""
    if not outcome_prices_raw:
        return "UNKNOWN"
    try:
        prices = (
            json.loads(outcome_prices_raw)
            if isinstance(outcome_prices_raw, str)
            else outcome_prices_raw
        )
        up_p = float(prices[0])
        down_p = float(prices[1])
        if up_p > 0.99 and down_p < 0.01:
            return "UP"
        if down_p > 0.99 and up_p < 0.01:
            return "DOWN"
        return "UNKNOWN"
    except (ValueError, IndexError, json.JSONDecodeError, TypeError):
        return "UNKNOWN"


def _parse_market(m: dict, fallback_year: int) -> UpDownMarket | None:
    question = (m.get("question") or "").strip()
    if not question:
        return None

    asset_raw: str | None = None
    start_utc = end_utc = None

    end_date_raw = m.get("endDate") or ""
    try:
        year = int(end_date_raw[:4])
    except (ValueError, TypeError):
        year = fallback_year

    # Try 5m/15m windowed format first.
    match = _WINDOW_RE.match(question)
    if match:
        asset_raw = match["asset"]
        try:
            start_utc = _et_to_utc(
                year, match["month"], int(match["day"]),
                int(match["sh"]), int(match["sm"] or 0), match["sap"],
            )
            end_utc = _et_to_utc(
                year, match["month"], int(match["day"]),
                int(match["eh"]), int(match["em"] or 0), match["eap"],
            )
        except (ValueError, KeyError):
            return None
        if end_utc <= start_utc:
            end_utc = end_utc + _ONE_DAY
    else:
        # Try hourly format. Window = [named hour, named hour + 1h] ET.
        match = _HOURLY_RE.match(question)
        if not match:
            return None
        asset_raw = match["asset"]
        try:
            hour12 = int(match["sh"])
            start_utc = _et_to_utc(
                year, match["month"], int(match["day"]),
                hour12, 0, match["sap"],
            )
        except (ValueError, KeyError):
            return None
        end_utc = start_utc + timedelta(hours=1)

    asset = ASSET_NORMALIZE.get((asset_raw or "").lower())
    if not asset:
        return None
    binance_symbol = ASSET_TO_BINANCE_EXT.get(asset)
    if not binance_symbol:
        return None

    assert start_utc is not None and end_utc is not None

    token_ids_raw = m.get("clobTokenIds")
    if not token_ids_raw:
        return None
    try:
        token_ids = (
            json.loads(token_ids_raw) if isinstance(token_ids_raw, str) else token_ids_raw
        )
        if len(token_ids) < 2:
            return None
        token_id_up = str(token_ids[0])
        token_id_down = str(token_ids[1])
    except (json.JSONDecodeError, TypeError):
        return None

    return UpDownMarket(
        market_id=str(m.get("id", "")),
        question=question,
        slug=m.get("slug", ""),
        asset=asset,
        binance_symbol=binance_symbol,
        window_start_utc=start_utc,
        window_end_utc=end_utc,
        token_id_up=token_id_up,
        token_id_down=token_id_down,
        outcome=_parse_outcome(m.get("outcomePrices")),
        volume_usd=float(m.get("volume") or 0),
    )


class GammaClient:
    """Async client for the Polymarket Gamma metadata API."""

    def __init__(self, session: aiohttp.ClientSession | None = None, timeout: int = 30):
        self._owns_session = session is None
        self._session = session or aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout)
        )

    async def close(self) -> None:
        if self._owns_session:
            await self._session.close()

    async def __aenter__(self) -> "GammaClient":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.close()

    async def _get(self, path: str, params: dict[str, Any]) -> Any:
        url = f"{GAMMA_BASE}{path}"
        last_exc: Exception | None = None
        for attempt in range(4):
            try:
                async with self._session.get(url, params=params) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    if resp.status >= 500 and attempt < 3:
                        await asyncio.sleep(0.5 * (2 ** attempt))
                        continue
                    text = await resp.text()
                    raise RuntimeError(f"Gamma GET {url} -> {resp.status}: {text[:200]}")
            except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
                last_exc = exc
                if attempt < 3:
                    logger.warning("Gamma GET retry %d/3 %s: %s", attempt + 1, path, exc)
                    await asyncio.sleep(0.5 * (2 ** attempt))
                    continue
                raise
        if last_exc:
            raise last_exc
        return None

    async def list_events_by_series(
        self,
        series_slug: str,
        start_utc: datetime,
        end_utc: datetime,
        page_size: int = 100,
        max_pages: int = 600,
        cache_dir: Path | str | None = None,
    ) -> list[UpDownMarket]:
        """Iterate /events for a given series_slug, parsing nested markets.

        Much faster than /markets/keyset when the slug is known (e.g. one slug per
        asset+horizon, so the year of BTC hourly = ~8.7k events vs ~10M total markets).
        """
        cache_root = Path(cache_dir) if cache_dir else None
        if cache_root:
            cache_root.mkdir(parents=True, exist_ok=True)
            tag = f"{series_slug}_{start_utc.strftime('%Y%m%dT%H%M')}_{end_utc.strftime('%Y%m%dT%H%M')}"

        out: list[UpDownMarket] = []
        seen_ids: set[str] = set()
        for page in range(max_pages):
            params: dict[str, Any] = {
                "limit": page_size,
                "offset": page * page_size,
                "closed": "true",
                "order": "endDate",
                "ascending": "false",
                "series_slug": series_slug,
                "end_date_min": start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end_date_max": end_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            cache_file = (
                cache_root / f"events_{tag}_p{page:04d}.json"
                if cache_root else None
            )
            data = None
            if cache_file and cache_file.exists():
                try:
                    data = json.loads(cache_file.read_text())
                except (json.JSONDecodeError, OSError):
                    data = None
            if data is None:
                try:
                    data = await self._get("/events", params)
                except RuntimeError as exc:
                    msg = str(exc)
                    if "offset too large" in msg and page > 0:
                        break
                    raise
                if cache_file and isinstance(data, list):
                    try:
                        cache_file.write_text(json.dumps(data))
                    except OSError as exc:
                        logger.debug("Gamma events cache write failed: %s", exc)

            if not isinstance(data, list) or not data:
                break

            new_in_page = 0
            for ev in data:
                for mkt in ev.get("markets", []) or []:
                    parsed = _parse_market(mkt, end_utc.year)
                    if parsed is None:
                        continue
                    if parsed.window_seconds <= 0 or parsed.window_seconds > 3 * 3600:
                        continue
                    if parsed.market_id in seen_ids:
                        continue
                    seen_ids.add(parsed.market_id)
                    out.append(parsed)
                    new_in_page += 1

            logger.debug(
                "Events page %d (%s): %d events, %d new markets",
                page, series_slug, len(data), new_in_page,
            )
            if len(data) < page_size:
                break

        logger.info(
            "Gamma series %s: %d markets in %s..%s",
            series_slug, len(out), start_utc.date(), end_utc.date(),
        )
        return out

    async def list_up_down_markets(
        self,
        start_utc: datetime,
        end_utc: datetime,
        assets: tuple[str, ...] = ("bitcoin", "ethereum", "solana"),
        page_size: int = 100,
        max_pages: int = 500,
        cache_dir: Path | str | None = None,
    ) -> list[UpDownMarket]:
        """List resolved Up/Down markets ending in [start_utc, end_utc].

        If `cache_dir` is given, the raw paginated payloads are cached to JSON
        per (start, end, page) tuple so reruns avoid re-paginating the full year.
        """
        assets_set = {a.lower() for a in assets}
        out: list[UpDownMarket] = []
        seen_ids: set[str] = set()

        cache_root = Path(cache_dir) if cache_dir else None
        if cache_root:
            cache_root.mkdir(parents=True, exist_ok=True)
            cache_tag = (
                f"{start_utc.strftime('%Y%m%dT%H%M')}_{end_utc.strftime('%Y%m%dT%H%M')}"
            )

        cursor: str | None = None
        consecutive_empty = 0
        for page in range(max_pages):
            params: dict[str, Any] = {
                "limit": page_size,
                "closed": "true",
                "order": "endDate",
                "ascending": "false",
                "end_date_min": start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end_date_max": end_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            if cursor:
                params["cursor"] = cursor
            cache_file = (
                cache_root / f"gamma_{cache_tag}_p{page:04d}_s{page_size}.json"
                if cache_root else None
            )
            payload = None
            if cache_file and cache_file.exists():
                try:
                    payload = json.loads(cache_file.read_text())
                except (json.JSONDecodeError, OSError):
                    payload = None
            if payload is None:
                payload = await self._get("/markets/keyset", params)
                if cache_file and isinstance(payload, dict):
                    try:
                        cache_file.write_text(json.dumps(payload))
                    except OSError as exc:
                        logger.debug("Gamma cache write failed: %s", exc)

            if not isinstance(payload, dict):
                break
            markets_payload = payload.get("markets")
            if not isinstance(markets_payload, list):
                break

            new_in_page = 0
            for m in markets_payload:
                parsed = _parse_market(m, end_utc.year)
                if parsed is None:
                    continue
                if parsed.asset not in assets_set:
                    continue
                if not (start_utc - _ONE_DAY < parsed.window_end_utc <= end_utc + _ONE_DAY):
                    continue
                if parsed.window_seconds <= 0 or parsed.window_seconds > 3600:
                    continue
                if parsed.market_id in seen_ids:
                    continue
                seen_ids.add(parsed.market_id)
                out.append(parsed)
                new_in_page += 1

            logger.debug(
                "Gamma page %d (keyset): %d raw, %d new Up/Down markets",
                page, len(markets_payload), new_in_page,
            )

            # Early-stop heuristic: if we've drifted past start_utc with no new matches,
            # bail. (Gamma returns oldest-first inside our descending window.)
            if not markets_payload:
                consecutive_empty += 1
                if consecutive_empty >= 2:
                    break
            else:
                consecutive_empty = 0

            next_cursor = payload.get("next_cursor")
            if not next_cursor:
                break
            cursor = next_cursor

        logger.info(
            "Gamma: found %d Up/Down markets in %s..%s",
            len(out), start_utc.date(), end_utc.date(),
        )
        return out
