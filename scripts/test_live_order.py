"""Coloca una orden LIVE de prueba ($1 FOK) para verificar py-clob-client-v2.

Usage en VPS:
    python scripts/test_live_order.py
    python scripts/test_live_order.py --amount 1 --asset bitcoin
    python scripts/test_live_order.py --token-id YOUR_TOKEN_ID

Requiere POLYMARKET_* en .env y py-clob-client-v2 instalado.
"""
from __future__ import annotations

import argparse
import asyncio
import ssl
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp
import certifi

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from src.polymarket.gamma import GammaClient, _parse_market
from src.polymarket.live_clob import LiveClobExecutor, load_live_config

CLOB = "https://clob.polymarket.com"

# Mismos slugs que run_paper_trader.py (no "bitcoin-", sino "btc-").
ASSET_TO_SERIES = {
    "bitcoin": "btc-up-or-down-hourly",
    "ethereum": "eth-up-or-down-hourly",
    "solana": "solana-up-or-down-hourly",
    "xrp": "xrp-up-or-down-hourly",
}


async def _midpoint(session: aiohttp.ClientSession, token_id: str) -> float:
    async with session.get(f"{CLOB}/midpoint", params={"token_id": token_id}) as resp:
        if resp.status != 200:
            return 0.5
        data = await resp.json()
        return float(data.get("mid", 0.5))


async def _find_test_market(
    session: aiohttp.ClientSession, asset: str,
) -> tuple[str, str, float]:
    """Return (token_id, question, midpoint) for an open hourly market."""
    now = datetime.now(timezone.utc)
    max_end = now + timedelta(seconds=3600)
    slug = ASSET_TO_SERIES.get(asset, f"{asset}-up-or-down-hourly")
    params = {
        "limit": 20,
        "closed": "false",
        "active": "true",
        "order": "endDate",
        "ascending": "true",
        "series_slug": slug,
        "end_date_min": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end_date_max": max_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    async with GammaClient(session=session) as gamma:
        data = await gamma._get("/events", params)  # noqa: SLF001
    if not isinstance(data, list):
        raise RuntimeError("Gamma no devolvió eventos")

    for ev in data:
        for mkt in ev.get("markets", []) or []:
            parsed = _parse_market(mkt, now.year)
            if parsed is None:
                continue
            secs = (parsed.window_end_utc - now).total_seconds()
            if secs < 120 or secs > 3600:
                continue
            up_mid = await _midpoint(session, parsed.token_id_up)
            down_mid = await _midpoint(session, parsed.token_id_down)
            if up_mid <= down_mid:
                return parsed.token_id_up, parsed.question, up_mid
            return parsed.token_id_down, parsed.question, down_mid

    raise RuntimeError(
        f"No hay mercado hourly abierto para {asset}. "
        "Probá entre :02 y :58 de la hora, otro --asset, o pasá --token-id."
    )


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--amount", type=float, default=1.0, help="USD a comprar (default $1)")
    ap.add_argument("--asset", default="bitcoin", choices=["bitcoin", "ethereum", "solana", "xrp"])
    ap.add_argument("--token-id", default="", help="Token CLOB (salta búsqueda Gamma)")
    args = ap.parse_args()

    cfg = load_live_config()
    live = LiveClobExecutor(cfg)

    print("Balance antes...")
    print("(Si ves 'Could not create api key' está OK — usa derive)")
    await live.ensure_allowance()
    bal_before = await live.get_usdc_balance()
    print(f"  ${bal_before:.2f}")

    ctx = ssl.create_default_context(cafile=certifi.where())
    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=ctx),
        headers={"User-Agent": "polymarket-bot-test/1.0", "Accept": "application/json"},
        timeout=aiohttp.ClientTimeout(total=30),
    ) as session:
        if args.token_id:
            token_id = args.token_id.strip()
            question = "(manual token)"
            mid = await _midpoint(session, token_id)
        else:
            token_id, question, mid = await _find_test_market(session, args.asset)

        max_price = min(0.99, mid + 0.10)
        print(f"Mercado: {question}")
        print(f"Token: {token_id[:24]}...  mid≈{mid:.2f}  max_price={max_price:.2f}")
        print(f"Enviando FOK BUY ${args.amount:.2f}...")

        result = await live.buy_fok(token_id, args.amount, max_price=max_price)
        print(f"ok={result.ok}")
        if result.error:
            print(f"error={result.error}")
        if result.raw:
            print(f"raw={result.raw}")
        if result.ok:
            print(
                f"order_id={result.order_id} fill={result.fill_price:.3f} "
                f"cost=${result.cost_paid:.2f}"
            )

    await asyncio.sleep(2)
    bal_after = await live.get_usdc_balance()
    print(f"Balance después: ${bal_after:.2f} (delta ${bal_after - bal_before:+.2f})")


if __name__ == "__main__":
    asyncio.run(main())
