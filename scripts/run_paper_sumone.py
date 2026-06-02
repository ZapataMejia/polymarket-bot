"""Entrypoint para el daemon V3 SumOne (sum-to-one arbitrage).

Uso (local):
    python scripts/run_paper_sumone.py
    python scripts/run_paper_sumone.py --bankroll 100 --margin 0.005

Uso (VPS, junto con V1/V2B):
    python scripts/run_paper_sumone.py \
        --bankroll 100 \
        --telegram-token <TOKEN_V3> \
        --telegram-chat-id <CHAT_ID> \
        --state-path data/paper_trading_v3/state.json \
        --log-file logs/paper_sumone.log
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

# --- Fix DNS en Windows --------------------------------------------------
import aiohttp.connector as _aiohttp_connector  # noqa: E402
from aiohttp.resolver import ThreadedResolver as _ThreadedResolver  # noqa: E402

_aiohttp_connector.DefaultResolver = _ThreadedResolver
# -------------------------------------------------------------------------

from src.core.config import Config  # noqa: E402
from src.core.logger import setup_logger  # noqa: E402
from src.polymarket.paper_sumone import SumOneConfig, SumOneTrader  # noqa: E402


async def amain() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/default.yaml")
    ap.add_argument("--bankroll", type=float, default=100.0)
    ap.add_argument("--max-pct-per-arb", type=float, default=0.10)
    ap.add_argument("--max-position-usd", type=float, default=200.0)
    ap.add_argument("--bankroll-floor", type=float, default=20.0)
    ap.add_argument("--margin", type=float, default=0.005,
                    help="Profit minimo por par despues de fees (en $).")
    ap.add_argument("--half-spread-cents", type=float, default=1.5)
    ap.add_argument("--fee-rate-pct", type=float, default=2.0)
    ap.add_argument("--poll-sec", type=int, default=15)
    ap.add_argument("--max-concurrent", type=int, default=8)
    ap.add_argument(
        "--series",
        nargs="+",
        default=[
            "btc-up-or-down-hourly",
            "eth-up-or-down-hourly",
            "solana-up-or-down-hourly",
            "xrp-up-or-down-hourly",
        ],
    )
    ap.add_argument("--state-path", default="data/paper_trading_v3/state.json")
    ap.add_argument("--instance-label", default="V3")
    ap.add_argument("--telegram-token", default=None)
    ap.add_argument("--telegram-chat-id", default=None)
    ap.add_argument("--log-file", default="logs/paper_sumone.log")
    args = ap.parse_args()

    cfg = Config.from_yaml(args.config)
    setup_logger("trading", cfg.log_level, args.log_file)
    log = logging.getLogger("trading.polymarket.sumone")
    log.info("[%s] starting SumOne trader, bankroll=$%.2f",
             args.instance_label, args.bankroll)

    sumone_cfg = SumOneConfig(
        initial_bankroll_usd=args.bankroll,
        max_pct_per_arb=args.max_pct_per_arb,
        max_position_usd=args.max_position_usd,
        bankroll_floor_usd=args.bankroll_floor,
        margin_required=args.margin,
        half_spread_cents=args.half_spread_cents,
        fee_rate_pct=args.fee_rate_pct,
        poll_interval_sec=args.poll_sec,
        max_concurrent_positions=args.max_concurrent,
        series_slugs=tuple(args.series),
        state_path=args.state_path,
        instance_label=args.instance_label,
    )

    telegram_token = args.telegram_token or cfg.telegram_token
    telegram_chat_id = args.telegram_chat_id or cfg.telegram_chat_id

    trader = SumOneTrader(
        config=sumone_cfg,
        telegram_token=telegram_token,
        telegram_chat_id=telegram_chat_id,
    )

    def _signal(*_: object) -> None:
        log.info("signal received, stopping...")
        trader.stop()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal)
        except NotImplementedError:
            pass

    try:
        await trader.run()
    finally:
        log.info("SumOne trader stopped")


if __name__ == "__main__":
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass
