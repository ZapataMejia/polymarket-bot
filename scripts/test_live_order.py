"""Test order signing only (does NOT post) — verify SDK accepts sig_type=3.

Usage on VPS (after py-clob-client-v2 install):
    python scripts/test_live_order.py

Posts a tiny FOK buy at worst price 0.01 ($1) — cancel if no liquidity.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

# Example BTC hourly token — override with env TEST_TOKEN_ID if needed.
import os
from dotenv import load_dotenv

load_dotenv()

from src.polymarket.live_clob import LiveClobExecutor, load_live_config


async def main() -> None:
    cfg = load_live_config()
    live = LiveClobExecutor(cfg)
    token = os.getenv("TEST_TOKEN_ID", "").strip()
    if not token:
        print("Set TEST_TOKEN_ID in .env to a live market token_id, or skip this test.")
        print("Balance-only test:")
        await live.ensure_allowance()
        bal = await live.get_usdc_balance()
        print(f"Balance OK: ${bal:.2f}")
        return
    amount = float(os.getenv("TEST_ORDER_USD", "1"))
    print(f"Posting test FOK buy ${amount} on token {token[:20]}...")
    result = await live.buy_fok(token, amount, max_price=0.05)
    print(f"ok={result.ok} error={result.error!r} raw={result.raw}")


if __name__ == "__main__":
    asyncio.run(main())
