"""Smoke-test Polymarket live credentials (balance only, no orders).

Usage on VPS:
    python scripts/test_live_clob.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from src.polymarket.live_clob import LiveClobExecutor, load_live_config


async def main() -> None:
    cfg = load_live_config()
    live = LiveClobExecutor(cfg)
    print("Updating allowance...")
    await live.ensure_allowance()
    raw = await live.get_balance_raw()
    print(f"API raw: {raw}")
    bal = await live.get_usdc_balance()
    print(f"USDC balance: ${bal:.2f}")
    print(f"Funder: {cfg.funder_address[:10]}...{cfg.funder_address[-6:]}")
    print(f"Signature type: {cfg.signature_type}")
    if bal < 1:
        print("WARNING: balance very low — credentials or funder address may be wrong.")
        print("Run: python scripts/diagnose_live_clob.py")
    else:
        print("OK — credentials work.")


if __name__ == "__main__":
    asyncio.run(main())
