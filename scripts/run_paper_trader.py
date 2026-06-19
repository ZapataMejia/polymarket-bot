"""Entrypoint for the live Polymarket paper-trading daemon.

Usage (local):
    python scripts/run_paper_trader.py
    python scripts/run_paper_trader.py --bankroll 100 --position-size 2 --threshold 0.05

Stops gracefully on SIGINT/SIGTERM (Ctrl-C). State is persisted in
data/paper_trading/state.json on every event.
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
# aiodns (c-ares, dependencia de ccxt) a veces no detecta los servidores DNS
# en Windows y lanza "Could not contact DNS servers". Forzamos el
# ThreadedResolver de aiohttp, que usa getaddrinfo del sistema operativo
# (el mismo DNS que ya funciona para git/pip/navegador).
import aiohttp.connector as _aiohttp_connector
from aiohttp.resolver import ThreadedResolver as _ThreadedResolver

_aiohttp_connector.DefaultResolver = _ThreadedResolver
# -------------------------------------------------------------------------

import socket as _socket
import zlib as _zlib

from src.core.config import Config
from src.core.logger import setup_logger
from src.data.exchange import ExchangeClient
from src.polymarket.binance_klines import BinanceKlineCache
from src.polymarket.paper_trader import PaperConfig, PaperTrader

# Mantiene vivo el socket-candado durante toda la vida del proceso.
_LOCK_SOCK: "_socket.socket | None" = None


def _acquire_single_instance_lock(state_path: str, label: str, log: logging.Logger) -> bool:
    """Candado de instancia única por state-path.

    Evita que arranquen DOS bots con el mismo archivo de estado (que en LIVE
    significa doble orden real con la misma billetera). Usa un socket local
    exclusivo: si ya hay otra instancia con el mismo state-path, el bind falla.
    """
    global _LOCK_SOCK
    key = (state_path or label).encode("utf-8")
    port = 49152 + (_zlib.crc32(key) % 15000)  # rango efímero, estable entre procesos
    sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        sock.listen(1)
    except OSError:
        sock.close()
        log.error(
            "[%s] YA HAY OTRA INSTANCIA corriendo con state-path=%s (lock puerto %d). "
            "Abortando para no duplicar ordenes.", label, state_path, port,
        )
        return False
    _LOCK_SOCK = sock
    log.info("[%s] lock de instancia unica OK (puerto %d)", label, port)
    return True


async def amain() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/default.yaml")
    ap.add_argument("--bankroll", type=float, default=100.0)
    ap.add_argument("--sizing-mode", choices=("fixed", "kelly"), default="kelly")
    ap.add_argument("--position-size", type=float, default=2.0,
                    help="Tamaño fijo por trade (solo si sizing-mode=fixed)")
    ap.add_argument("--kelly-fraction", type=float, default=0.25,
                    help="Fracción de Kelly (0.25 = quarter Kelly)")
    ap.add_argument("--max-pct-per-trade", type=float, default=0.10,
                    help="Cap por trade como fracción del bankroll")
    ap.add_argument("--max-concurrent", type=int, default=4,
                    help="Máximo de posiciones abiertas en simultáneo")
    ap.add_argument("--bankroll-floor", type=float, default=30.0,
                    help="Si el bankroll cae por debajo, pausa entradas")
    ap.add_argument("--threshold", type=float, default=0.05)
    ap.add_argument("--min-seconds-to-resolution", type=int, default=60,
                    help="Ignora mercados con menos de N segundos para cerrar")
    ap.add_argument("--max-seconds-to-resolution", type=int, default=3300,
                    help="Ignora mercados con mas de N segundos para cerrar (V4 Endgame usa 300)")
    ap.add_argument("--half-spread-cents", type=float, default=1.5)
    ap.add_argument("--fee-rate-pct", type=float, default=2.0)
    ap.add_argument("--poll-sec", type=int, default=30)
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
    ap.add_argument(
        "--state-path",
        default="data/paper_trading/state.json",
    )
    # ---- V2-style filters (off by default; live V1 keeps current behavior) ----
    ap.add_argument("--skip-hours-utc", nargs="*", type=int, default=[],
                    help="Skip entries on these UTC hours, e.g. --skip-hours-utc 21 23")
    ap.add_argument("--skip-weekdays", nargs="*", default=[],
                    help="Skip entries on these weekdays, e.g. --skip-weekdays Saturday")
    ap.add_argument("--min-volume", type=float, default=0.0,
                    help="Skip markets with volume below this USD (e.g. 5000)")
    ap.add_argument("--instance-label", default="V1",
                    help="Tag for logs and Telegram messages (e.g. V1, V2B)")
    # ---- Telegram override (use a different bot for this instance) ----
    ap.add_argument("--telegram-token", default=None,
                    help="Override TELEGRAM_BOT_TOKEN from config (use a separate bot for V2)")
    ap.add_argument("--telegram-chat-id", default=None,
                    help="Override TELEGRAM_CHAT_ID from config")
    ap.add_argument("--disable-telegram", action="store_true",
                    help="Run without Telegram (only file log). Used by V4B when no token is set.")
    ap.add_argument("--log-file", default="logs/paper_trader.log")
    ap.add_argument("--live", action="store_true",
                    help="Place real Polymarket orders (requires POLYMARKET_* in .env)")
    ap.add_argument("--max-position-usd", type=float, default=25.0,
                    help="Hard cap per live order in USD")
    ap.add_argument("--max-fill-price", type=float, default=0.99,
                    help="Tope de precio de compra (0.99 = apagado; palanca opcional)")
    ap.add_argument("--min-poly-price", type=float, default=0.05,
                    help="No comprar longshots por debajo de este precio (modelo poco confiable)")
    ap.add_argument("--max-settle-attempts", type=int, default=10,
                    help="Reintentos esperando la resolución real de Polymarket antes de liquidar")
    args = ap.parse_args()

    cfg = Config.from_yaml(args.config)
    setup_logger("trading", cfg.log_level, args.log_file)
    log = logging.getLogger("trading.polymarket.paper")
    mode = "LIVE" if args.live else "PAPER"
    log.info("[%s] starting %s trader, bankroll=$%.2f", args.instance_label, mode, args.bankroll)

    if not _acquire_single_instance_lock(args.state_path, args.instance_label, log):
        sys.exit(1)

    paper_cfg = PaperConfig(
        initial_bankroll_usd=args.bankroll,
        sizing_mode=args.sizing_mode,
        position_size_usd=args.position_size,
        kelly_fraction=args.kelly_fraction,
        max_pct_per_trade=args.max_pct_per_trade,
        max_concurrent_positions=args.max_concurrent,
        bankroll_floor_usd=args.bankroll_floor,
        entry_threshold=args.threshold,
        min_seconds_to_resolution=args.min_seconds_to_resolution,
        max_seconds_to_resolution=args.max_seconds_to_resolution,
        half_spread_cents=args.half_spread_cents,
        fee_rate_pct=args.fee_rate_pct,
        poll_interval_sec=args.poll_sec,
        series_slugs=tuple(args.series),
        state_path=args.state_path,
        skip_hours_utc=tuple(args.skip_hours_utc),
        skip_weekdays=tuple(args.skip_weekdays),
        min_volume_usd=args.min_volume,
        instance_label=args.instance_label,
        live_mode=args.live,
        max_position_usd=args.max_position_usd,
        max_fill_price=args.max_fill_price,
        min_poly_price=args.min_poly_price,
        max_settle_attempts=args.max_settle_attempts,
    )

    if args.disable_telegram:
        telegram_token = None
        telegram_chat_id = None
        log.info("[%s] Telegram disabled (running with file log only)", args.instance_label)
    else:
        telegram_token = args.telegram_token or cfg.telegram_token
        telegram_chat_id = args.telegram_chat_id or cfg.telegram_chat_id

    client = ExchangeClient(cfg.exchange)
    cache = BinanceKlineCache(client, cache_dir="data/poly_klines_live")
    trader = PaperTrader(
        config=paper_cfg,
        binance_cache=cache,
        telegram_token=telegram_token,
        telegram_chat_id=telegram_chat_id,
    )

    stop_event = asyncio.Event()

    def _signal(*_: object) -> None:
        log.info("signal received, stopping...")
        trader.stop()
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal)
        except NotImplementedError:
            pass

    try:
        await trader.run()
    finally:
        await client.close()
        log.info("paper trader stopped")


if __name__ == "__main__":
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass
