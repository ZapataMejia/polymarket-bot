"""Live paper-trading daemon for Polymarket Up/Down crypto markets.

Loop overview (every ~30s):
  1. DISCOVER  — query Gamma for OPEN BTC/ETH hourly markets resolving soon.
  2. SIGNAL    — for each monitored market, fetch live CLOB price + Binance spot,
                 compute fair_prob, and OPEN a virtual position when edge crosses
                 the threshold. Notify Telegram on entry.
  3. SETTLE    — for each open position whose window has expired, fetch the close
                 price and book PnL. Notify Telegram on exit.
  4. SUMMARY   — once per UTC day, send a digest with stats.

State persists in a JSON file so the daemon can restart safely.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import ssl
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiohttp
import certifi
import pandas as pd

from src.polymarket.binance_klines import BinanceKlineCache
from src.polymarket.clob import ClobClient
from src.polymarket.gamma import GammaClient, UpDownMarket, _parse_market, _parse_outcome
from src.polymarket.pricing import estimate_sigma_per_sec, fair_prob_up

logger = logging.getLogger("trading.polymarket.paper")


# ---------------------------------------------------------------------------
# Config & state
# ---------------------------------------------------------------------------
@dataclass
class PaperConfig:
    initial_bankroll_usd: float = 100.0
    # 'fixed': flat dollar amount per trade  /  'kelly': fraction of bankroll × edge.
    sizing_mode: str = "kelly"
    position_size_usd: float = 2.0  # used only when sizing_mode == 'fixed'
    kelly_fraction: float = 0.25     # quarter Kelly is the responsible default
    max_pct_per_trade: float = 0.10  # cap any single bet at 10% of bankroll
    min_position_usd: float = 1.0    # Polymarket min order size
    max_concurrent_positions: int = 4  # don't go all-in across many markets at once
    bankroll_floor_usd: float = 30.0   # stop trading if we fall below this
    entry_threshold: float = 0.05  # 5pp edge minimum
    min_seconds_to_resolution: int = 60
    max_seconds_to_resolution: int = 3300  # ignore markets > 55 min away
    half_spread_cents: float = 1.5
    flat_fee_cents: float = 0.5
    fee_rate_pct: float = 2.0
    vol_lookback_min: int = 60
    poll_interval_sec: int = 30
    series_slugs: tuple[str, ...] = (
        "btc-up-or-down-hourly",
        "eth-up-or-down-hourly",
        "solana-up-or-down-hourly",
        "xrp-up-or-down-hourly",
    )
    state_path: str = "data/paper_trading/state.json"
    # ---- V2-style optional filters (default = off / no filtering) -----
    # Skip entries when the UTC hour-of-day matches any of these (e.g. (21, 23)).
    skip_hours_utc: tuple[int, ...] = ()
    # Skip entries on these weekdays (Python: "Monday".."Sunday").
    skip_weekdays: tuple[str, ...] = ()
    # Minimum total volume on the Polymarket market (USD); 0 disables.
    min_volume_usd: float = 0.0
    # Optional human-readable label for logs / Telegram messages.
    instance_label: str = "V1"
    # Live trading: place real CLOB orders (requires POLYMARKET_* in .env).
    live_mode: bool = False
    max_position_usd: float = 25.0  # hard cap per order (liquidity / safety)
    # Tope de precio de compra. 0.99 = prácticamente apagado (NO capeamos
    # favoritos: el backtest sobre trades reales mostró que capearlos empeora).
    # Queda como palanca opcional por si se quiere endurecer vía CLI.
    max_fill_price: float = 0.99
    # No comprar longshots: si el lado que compramos cotiza por debajo de esto,
    # el modelo es puro ruido (caso Solana 1¢ → "+$4950" falso). ESTE SÍ va.
    min_poly_price: float = 0.05
    # Reintentos esperando la resolución REAL de Polymarket antes de liquidar.
    max_settle_attempts: int = 10

    @property
    def half_spread(self) -> float:
        return self.half_spread_cents / 100.0

    @property
    def flat_fee(self) -> float:
        return self.flat_fee_cents / 100.0

    @property
    def fee_rate(self) -> float:
        return self.fee_rate_pct / 100.0


@dataclass
class PaperPosition:
    market_id: str
    slug: str
    asset: str
    binance_symbol: str
    window_start_utc: str  # ISO
    window_end_utc: str
    strike: float
    direction: str  # 'UP' or 'DOWN'
    p_poly_entry: float
    p_fair_entry: float
    edge_entry: float
    fill_price: float
    position_usd: float
    contracts: float
    cost_paid: float  # what we "spent": contracts*fill + contracts*propfee + flat_fee
    opened_at_utc: str
    token_id: str = ""  # CLOB token actually bought (para resolver con Polymarket en LIVE)
    # filled later:
    settled_at_utc: str | None = None
    outcome: str | None = None  # 'UP' / 'DOWN'
    correct: bool | None = None
    payoff: float | None = None
    pnl: float | None = None
    live_order_id: str | None = None
    settle_attempts: int = 0  # reintentos esperando resolución de Polymarket (LIVE)


@dataclass
class PaperState:
    bankroll: float
    started_at_utc: str
    last_summary_date: str | None = None  # 'YYYY-MM-DD'
    last_telegram_update_id: int = 0
    open_positions: dict[str, PaperPosition] = field(default_factory=dict)
    closed_positions: list[PaperPosition] = field(default_factory=list)
    skipped_markets: set[str] = field(default_factory=set)

    def to_json(self) -> dict[str, Any]:
        return {
            "bankroll": self.bankroll,
            "started_at_utc": self.started_at_utc,
            "last_summary_date": self.last_summary_date,
            "last_telegram_update_id": self.last_telegram_update_id,
            "open_positions": {k: asdict(v) for k, v in self.open_positions.items()},
            "closed_positions": [asdict(p) for p in self.closed_positions],
            "skipped_markets": sorted(self.skipped_markets),
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "PaperState":
        return cls(
            bankroll=float(data.get("bankroll", 0.0)),
            started_at_utc=data.get("started_at_utc", datetime.now(timezone.utc).isoformat()),
            last_summary_date=data.get("last_summary_date"),
            last_telegram_update_id=int(data.get("last_telegram_update_id", 0)),
            open_positions={
                k: PaperPosition(**v) for k, v in data.get("open_positions", {}).items()
            },
            closed_positions=[PaperPosition(**p) for p in data.get("closed_positions", [])],
            skipped_markets=set(data.get("skipped_markets", [])),
        )


# ---------------------------------------------------------------------------
# Telegram notifier + command listener
# ---------------------------------------------------------------------------
class _Notifier:
    BASE = "https://api.telegram.org/bot{token}"

    def __init__(self, token: str | None, chat_id: str | None):
        self.token = token
        self.chat_id = str(chat_id) if chat_id else None
        self.enabled = bool(token and chat_id)

    def _ssl_ctx(self) -> ssl.SSLContext:
        return ssl.create_default_context(cafile=certifi.where())

    async def send(self, text: str) -> bool:
        if not self.enabled:
            logger.info("[TELEGRAM disabled] %s", text.replace("\n", " | ")[:200])
            return False
        url = self.BASE.format(token=self.token) + "/sendMessage"
        payload = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
                   "disable_web_page_preview": True}
        try:
            conn = aiohttp.TCPConnector(ssl=self._ssl_ctx())
            async with aiohttp.ClientSession(connector=conn) as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        return True
                    logger.error("Telegram send %d: %s", resp.status, (await resp.text())[:200])
                    return False
        except Exception as exc:
            logger.warning("Telegram send failed: %s", exc)
            return False

    async def get_updates(self, offset: int, timeout: int = 25) -> list[dict[str, Any]]:
        """Long-poll Telegram for new messages. Returns list of update objects."""
        if not self.enabled:
            return []
        url = self.BASE.format(token=self.token) + "/getUpdates"
        params = {"offset": offset, "timeout": timeout, "allowed_updates": '["message"]'}
        try:
            conn = aiohttp.TCPConnector(ssl=self._ssl_ctx())
            async with aiohttp.ClientSession(connector=conn) as session:
                async with session.get(url, params=params,
                                       timeout=aiohttp.ClientTimeout(total=timeout + 10)) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
                    if not data.get("ok"):
                        return []
                    return data.get("result", [])
        except asyncio.TimeoutError:
            return []
        except Exception as exc:
            logger.warning("Telegram getUpdates failed: %s", exc)
            return []

    async def set_commands(self, commands: list[tuple[str, str]]) -> bool:
        """Register the slash-command menu so Telegram shows clickable buttons."""
        if not self.enabled:
            return False
        url = self.BASE.format(token=self.token) + "/setMyCommands"
        payload = {"commands": [{"command": c, "description": d} for c, d in commands]}
        try:
            conn = aiohttp.TCPConnector(ssl=self._ssl_ctx())
            async with aiohttp.ClientSession(connector=conn) as session:
                async with session.post(url, json=payload,
                                        timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    return resp.status == 200
        except Exception as exc:
            logger.warning("Telegram setMyCommands failed: %s", exc)
            return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).isoformat()


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def _save_state(path: Path, state: PaperState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state.to_json(), indent=2, default=str))
    tmp.replace(path)


def _load_state(path: Path, initial_bankroll: float) -> PaperState:
    if not path.exists():
        return PaperState(bankroll=initial_bankroll, started_at_utc=_iso(_now_utc()))
    try:
        return PaperState.from_json(json.loads(path.read_text()))
    except Exception as exc:
        logger.error("Could not load state, starting fresh: %s", exc)
        return PaperState(bankroll=initial_bankroll, started_at_utc=_iso(_now_utc()))


# ---------------------------------------------------------------------------
# Daemon
# ---------------------------------------------------------------------------
class PaperTrader:
    """Polls Polymarket+Binance, opens virtual positions on edge, alerts Telegram."""

    def __init__(
        self,
        config: PaperConfig,
        binance_cache: BinanceKlineCache,
        telegram_token: str | None,
        telegram_chat_id: str | None,
    ):
        self.cfg = config
        self.binance = binance_cache
        self.notifier = _Notifier(telegram_token, telegram_chat_id)
        self.state = _load_state(Path(config.state_path), config.initial_bankroll_usd)
        self._gamma_session: aiohttp.ClientSession | None = None
        self._clob_session: aiohttp.ClientSession | None = None
        self._stop = False
        self._last_tick_error_msg = ""
        self._last_tick_error_at: float = 0.0
        self.live = None
        if config.live_mode:
            from src.polymarket.live_clob import LiveClobExecutor, load_live_config
            self.live = LiveClobExecutor(load_live_config())

    # --- public API ---------------------------------------------------------
    async def run(self) -> None:
        # Register slash-command menu so Telegram shows clickable buttons.
        await self.notifier.set_commands([
            ("start",     "Mensaje de bienvenida"),
            ("help",      "Lista de comandos"),
            ("status",    "Estado general del bot"),
            ("balance",   "Bankroll actual"),
            ("posiciones","Posiciones abiertas"),
            ("dia",       "Resumen del día"),
            ("pnl",       "PnL total desde el inicio"),
            ("historia",  "Últimos trades cerrados"),
            ("stats",     "Estadísticas globales"),
            ("config",    "Configuración del bot"),
        ])

        sizing_str = (
            f"Half/Quarter Kelly ({self.cfg.kelly_fraction*100:.0f}%K), "
            f"cap {self.cfg.max_pct_per_trade*100:.0f}%"
        ) if self.cfg.sizing_mode == "kelly" else f"Fijo ${self.cfg.position_size_usd:.0f}/trade"
        assets = ", ".join(s.split("-")[0].upper() for s in self.cfg.series_slugs)
        mode_str = "🟢 <b>LIVE</b> (órdenes reales)" if self.cfg.live_mode else "📝 Paper"
        if self.live:
            try:
                await self.live.ensure_allowance()
                bal = await self.live.get_usdc_balance()
                if bal < 1.0:
                    raw = await self.live.get_balance_raw()
                    await self.notifier.send(
                        "⚠️ <b>LIVE: balance $0 en API</b>\n"
                        "Tu USDC en Polymarket <b>no se perdió</b> — el bot no pudo leerlo.\n"
                        f"Respuesta API: <code>{str(raw)[:200]}</code>\n"
                        "Revisá POLYMARKET_FUNDER_ADDRESS y SIGNATURE_TYPE "
                        "(cuentas Google nuevas = 3).\n"
                        "Corré: <code>python scripts/test_live_clob.py</code>"
                    )
                    logger.warning("live balance read $0, raw=%s", raw)
                else:
                    self.state.bankroll = bal
                    _save_state(Path(self.cfg.state_path), self.state)
            except Exception as exc:
                logger.exception("live startup balance sync failed")
                await self.notifier.send(f"⚠️ LIVE: no pude leer balance: {exc}")
        await self.notifier.send(
            f"🤖 <b>Bot encendido</b> — {mode_str}\n"
            f"Bankroll: <code>${self.state.bankroll:.2f}</code>\n"
            f"Sizing: <code>{sizing_str}</code>\n"
            f"Threshold edge: <code>{self.cfg.entry_threshold*100:.0f}pp</code>\n"
            f"Assets: <code>{assets}</code>\n"
            f"Costos asumidos: {self.cfg.half_spread_cents}¢ + {self.cfg.fee_rate_pct}% fee\n"
            + (f"Stop loss: pausa bajo <code>${self.cfg.bankroll_floor_usd:.0f}</code>\n"
               if self.cfg.live_mode and self.cfg.bankroll_floor_usd > 0 else "")
            + ("Sin floor de bankroll — opera hasta que quede margen mínimo.\n"
               if self.cfg.live_mode and self.cfg.bankroll_floor_usd <= 0 else "")
            + "\nUsá /help para ver comandos disponibles."
        )
        ctx = ssl.create_default_context(cafile=certifi.where())
        self._gamma_session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=ctx),
            timeout=aiohttp.ClientTimeout(total=30),
        )
        self._clob_session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=ctx),
            timeout=aiohttp.ClientTimeout(total=30),
        )
        try:
            await asyncio.gather(
                self._trading_loop(),
                self._command_loop(),
            )
        finally:
            await self._gamma_session.close()
            await self._clob_session.close()

    def stop(self) -> None:
        self._stop = True

    # --- main loops ---------------------------------------------------------
    async def _trading_loop(self) -> None:
        while not self._stop:
            try:
                await self._tick()
            except Exception as exc:
                logger.exception("Tick failed")
                msg = f"{type(exc).__name__}: {exc}"
                now_mono = asyncio.get_event_loop().time()
                if (
                    msg != self._last_tick_error_msg
                    or now_mono - self._last_tick_error_at > 300
                ):
                    self._last_tick_error_msg = msg
                    self._last_tick_error_at = now_mono
                    err_body = (
                        "⚠️ <b>Error en tick</b> (el bot sigue; reintenta en 30s)\n"
                        f"<code>{msg[:250]}</code>"
                    )
                    if self.cfg.live_mode:
                        err_body += "\nLog: <code>logs/live_v4b.log</code>"
                    await self.notifier.send(err_body)
            await asyncio.sleep(self.cfg.poll_interval_sec)

    async def _command_loop(self) -> None:
        """Listen for Telegram commands and reply with formatted state."""
        while not self._stop:
            updates = await self.notifier.get_updates(
                offset=self.state.last_telegram_update_id + 1,
                timeout=25,
            )
            for upd in updates:
                self.state.last_telegram_update_id = max(
                    self.state.last_telegram_update_id, int(upd.get("update_id", 0)),
                )
                msg = upd.get("message") or {}
                text = (msg.get("text") or "").strip()
                chat = msg.get("chat") or {}
                # Security: only respond to the configured chat_id.
                if str(chat.get("id")) != self.notifier.chat_id:
                    continue
                if not text.startswith("/"):
                    continue
                cmd = text.split()[0].split("@")[0].lstrip("/").lower()
                try:
                    reply = await self._dispatch_command(cmd)
                except Exception as exc:
                    logger.exception("command failed: %s", cmd)
                    reply = f"⚠️ Error procesando /{cmd}: {exc}"
                if reply:
                    await self.notifier.send(reply)
            if updates:
                _save_state(Path(self.cfg.state_path), self.state)
            await asyncio.sleep(0.5)

    # --- internals ----------------------------------------------------------
    async def _sync_live_bankroll(self) -> None:
        """Refresh bankroll from Polymarket API (LIVE only)."""
        if not self.cfg.live_mode or not self.live:
            return
        try:
            bal = await self.live.get_usdc_balance()
            if bal >= 1.0:
                self.state.bankroll = bal
        except Exception as exc:
            logger.warning("live balance sync (command): %s", exc)

    async def _tick(self) -> None:
        now = _now_utc()
        if self.cfg.live_mode and self.live:
            try:
                bal = await self.live.get_usdc_balance()
                if bal >= 1.0:
                    self.state.bankroll = bal
            except Exception as exc:
                logger.warning("live balance sync: %s", exc)
        async with GammaClient(session=self._gamma_session) as gamma, ClobClient(session=self._clob_session) as clob:
            # 1) Discover open markets (next 0..max_seconds_to_resolution).
            open_markets = await self._discover_open_markets(gamma, now)
            logger.info(
                "tick %s: %d open markets, %d in book, %d closed",
                now.strftime("%H:%M:%S"),
                len(open_markets), len(self.state.open_positions),
                len(self.state.closed_positions),
            )
            # 2) Try to take new signals.
            for mkt in open_markets:
                if mkt.market_id in self.state.open_positions:
                    continue
                if mkt.market_id in self.state.skipped_markets:
                    continue
                try:
                    await self._maybe_open(mkt, clob, now)
                except Exception:
                    logger.exception("maybe_open failed %s", mkt.slug)

            # 3) Settle any position whose window has expired.
            await self._settle_expired(now)

        # 4) Daily summary at first tick of new UTC day.
        today = now.strftime("%Y-%m-%d")
        if self.state.last_summary_date != today:
            # Only send once we have at least one closed trade or we explicitly
            # cross midnight UTC.
            if self.state.last_summary_date is not None:
                await self._send_daily_summary(prev_date=self.state.last_summary_date)
            self.state.last_summary_date = today
            _save_state(Path(self.cfg.state_path), self.state)

    async def _discover_open_markets(
        self, gamma: GammaClient, now: datetime,
    ) -> list[UpDownMarket]:
        """Pull active/upcoming Up-or-Down markets from Gamma."""
        max_end = now + timedelta(seconds=self.cfg.max_seconds_to_resolution + 600)
        out: list[UpDownMarket] = []
        seen: set[str] = set()
        for slug in self.cfg.series_slugs:
            for page in range(8):  # ~800 events ahead; plenty.
                params = {
                    "limit": 100,
                    "offset": page * 100,
                    "closed": "false",
                    "active": "true",
                    "order": "endDate",
                    "ascending": "true",
                    "series_slug": slug,
                    "end_date_min": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "end_date_max": max_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
                try:
                    data = await gamma._get("/events", params)  # noqa: SLF001
                except RuntimeError as exc:
                    logger.debug("gamma /events err: %s", exc)
                    break
                if not isinstance(data, list) or not data:
                    break
                for ev in data:
                    for mkt in ev.get("markets", []) or []:
                        parsed = _parse_market(mkt, now.year)
                        if parsed is None:
                            continue
                        if parsed.window_seconds <= 0 or parsed.window_seconds > 3 * 3600:
                            continue
                        # Must currently be in [open .. end_utc] and end inside lookahead.
                        end_in = parsed.window_end_utc - now
                        if end_in.total_seconds() <= 0:
                            continue
                        if end_in.total_seconds() > self.cfg.max_seconds_to_resolution + 60:
                            continue
                        if parsed.market_id in seen:
                            continue
                        seen.add(parsed.market_id)
                        out.append(parsed)
                if len(data) < 100:
                    break
        return out

    async def _maybe_open(self, mkt: UpDownMarket, clob: ClobClient, now: datetime) -> None:
        seconds_remaining = (mkt.window_end_utc - now).total_seconds()
        if seconds_remaining < self.cfg.min_seconds_to_resolution:
            self.state.skipped_markets.add(mkt.market_id)
            return
        if self.cfg.skip_hours_utc and mkt.window_start_utc.hour in self.cfg.skip_hours_utc:
            self.state.skipped_markets.add(mkt.market_id)
            return
        if self.cfg.skip_weekdays:
            wday = mkt.window_start_utc.strftime("%A")
            if wday in self.cfg.skip_weekdays:
                self.state.skipped_markets.add(mkt.market_id)
                return
        if self.cfg.min_volume_usd > 0 and (mkt.volume_usd or 0) < self.cfg.min_volume_usd:
            self.state.skipped_markets.add(mkt.market_id)
            return
        # Need Binance data around `now` and from window_start - vol_lookback.
        binance_start = mkt.window_start_utc - timedelta(minutes=self.cfg.vol_lookback_min + 2)
        binance_end = now + timedelta(minutes=1)
        try:
            bdf = await self.binance.fetch_klines(mkt.binance_symbol, binance_start, binance_end)
        except Exception as exc:
            logger.warning("binance fetch failed for %s: %s", mkt.binance_symbol, exc)
            return
        if bdf.empty or "close" not in bdf.columns:
            return

        # Strike = spot at window_start (the open of that 1m bar).
        floored_start = mkt.window_start_utc.replace(second=0, microsecond=0)
        if floored_start not in bdf.index:
            return
        strike = float(bdf.loc[floored_start, "open"])

        # Sigma from minutes BEFORE window_start.
        pre = bdf.loc[bdf.index < mkt.window_start_utc].tail(self.cfg.vol_lookback_min)
        if len(pre) < 5:
            return
        log_rets = pre["close"].apply(math.log).diff().dropna().tolist()
        sigma_min = estimate_sigma_per_sec(log_rets, samples_per_sec=1.0)
        sigma_per_sec = sigma_min / math.sqrt(60.0)
        sigma_per_sec = max(5e-5, min(0.001, sigma_per_sec))

        # Current Binance spot (latest 1m bar's close).
        latest_bar = bdf.iloc[-1]
        s_now = float(latest_bar["close"])

        # Current Polymarket Up mid price.
        poly_df = await clob.fetch_price_history(
            mkt.token_id_up,
            now - timedelta(minutes=5),
            now + timedelta(minutes=1),
            fidelity_min=1,
        )
        if poly_df.empty:
            return
        p_poly_up = float(poly_df["price"].iloc[-1])
        p_fair_up = fair_prob_up(s_now, strike, sigma_per_sec, int(seconds_remaining))
        edge = p_fair_up - p_poly_up

        if abs(edge) < self.cfg.entry_threshold:
            return

        direction = "UP" if edge > 0 else "DOWN"
        if direction == "UP":
            naive_fill = p_poly_up
        else:
            naive_fill = 1.0 - p_poly_up
        # Longshot guard: el lado que compraríamos cotiza casi a cero → el modelo
        # log-normal no es confiable en los últimos minutos. Evitamos comprar a 1¢.
        if naive_fill < self.cfg.min_poly_price:
            self.state.skipped_markets.add(mkt.market_id)
            logger.info(
                "skip longshot %s %s naive_fill=%.3f < %.3f",
                mkt.slug, direction, naive_fill, self.cfg.min_poly_price,
            )
            return
        fill = min(1.0, naive_fill + self.cfg.half_spread)
        # Tope de precio de compra. El precio == win rate de equilibrio:
        # comprar a 0.74 exige ganar 74% para empatar, y solo ganamos ~57%.
        # 2 semanas en vivo (12-26 jun) confirmaron que los fills caros (>0.70)
        # dieron neto NEGATIVO pese a 71% de aciertos, y los baratos (<0.50)
        # neto positivo. El bot LIVE corre con --max-fill-price 0.55.
        if fill > self.cfg.max_fill_price:
            self.state.skipped_markets.add(mkt.market_id)
            logger.info(
                "skip expensive %s %s fill=%.3f > max_fill=%.3f",
                mkt.slug, direction, fill, self.cfg.max_fill_price,
            )
            return

        # ---- RISK CONTROLS ------------------------------------------------
        if self.cfg.bankroll_floor_usd > 0 and self.state.bankroll < self.cfg.bankroll_floor_usd:
            logger.warning(
                "Bankroll $%.2f below floor $%.2f — pausing entries",
                self.state.bankroll, self.cfg.bankroll_floor_usd,
            )
            return
        if len(self.state.open_positions) >= self.cfg.max_concurrent_positions:
            logger.info(
                "Max concurrent positions (%d) reached, skipping %s",
                self.cfg.max_concurrent_positions, mkt.slug,
            )
            return

        # ---- SIZING -------------------------------------------------------
        if self.cfg.sizing_mode == "kelly":
            # Kelly for a binary bet: f* = (p_model - fill_total) / (1 - fill_total).
            # We ignore the flat fee here (only 0.5¢, negligible vs the proportional fee).
            p_model = p_fair_up if direction == "UP" else 1.0 - p_fair_up
            fill_total = fill * (1.0 + self.cfg.fee_rate)
            edge_after = p_model - fill_total
            if edge_after <= 0:
                self.state.skipped_markets.add(mkt.market_id)
                return
            f_kelly = edge_after / max(1e-6, 1.0 - fill_total)
            f = max(0.0, min(f_kelly * self.cfg.kelly_fraction, self.cfg.max_pct_per_trade))
            position_usd = self.state.bankroll * f
        else:
            position_usd = self.cfg.position_size_usd

        position_usd = min(position_usd, self.state.bankroll * 0.95)
        if self.cfg.live_mode:
            position_usd = min(position_usd, self.cfg.max_position_usd)
        if position_usd < self.cfg.min_position_usd:
            logger.info("Position size $%.2f below min, skipping %s", position_usd, mkt.slug)
            return

        contracts = position_usd / fill
        prop_fee = contracts * fill * self.cfg.fee_rate
        cost_paid = contracts * fill + prop_fee + self.cfg.flat_fee
        if cost_paid > self.state.bankroll:
            return

        live_order_id: str | None = None
        token_id = mkt.token_id_up if direction == "UP" else mkt.token_id_down
        if self.cfg.live_mode and self.live:
            slip = self.live.cfg.max_slippage_cents / 100.0
            # Tope duro LIVE: nunca pagar más que max_fill_price (55¢).
            # Antes el techo subía con reintentos hasta ~84¢ → ganabas $2 pagando $10.
            max_price = min(
                self.cfg.max_fill_price,
                fill + slip,
            )
            result = await self.live.buy_fok(
                token_id, position_usd, max_price=max_price, hard_ceiling=max_price,
            )
            if result.ok and result.fill_price > self.cfg.max_fill_price + 1e-6:
                await self.notifier.send(
                    f"⚠️ <b>LIVE pagó caro</b> — {mkt.asset.upper()} {direction}\n"
                    f"Fill real: <code>{result.fill_price*100:.1f}¢</code> "
                    f"(tope objetivo: <code>{self.cfg.max_fill_price*100:.0f}¢</code>).\n"
                    f"PnL será bajo aunque gane — revisar liquidez del libro."
                )
                logger.warning(
                    "live fill expensive %.3f > max %.3f",
                    result.fill_price, self.cfg.max_fill_price,
                )
            if not result.ok:
                err = result.error or ""
                hint = ""
                low = err.lower()
                if any(k in low for k in (
                    "fully filled", "killed", "not enough", "no match",
                    "no orders found", "liquidity",
                )):
                    hint = (
                        "\n<i>Libro vacío en endgame — reintentó stake más chico "
                        "y techo de precio más alto. Si sigue fallando: subí "
                        "POLYMARKET_MAX_SLIPPAGE_CENTS o entrá más temprano "
                        "(min-seconds-to-resolution).</i>"
                    )
                await self.notifier.send(
                    f"🔴 <b>LIVE orden falló</b> — {mkt.asset.upper()} {direction}\n"
                    f"<i>{mkt.question}</i>\n"
                    f"Stake: <code>${position_usd:.2f}</code>  max_price: <code>{max_price:.2f}</code>\n"
                    f"Error: <code>{err[:200]}</code>{hint}"
                )
                logger.warning(
                    "live order failed %s stake=%.2f max_price=%.3f: %s",
                    mkt.slug, position_usd, max_price, err,
                )
                return
            fill = result.fill_price
            contracts = result.contracts
            cost_paid = result.cost_paid
            live_order_id = result.order_id or None
            try:
                bal = await self.live.get_usdc_balance()
                if bal >= 1.0:
                    self.state.bankroll = bal
                else:
                    self.state.bankroll -= cost_paid
            except Exception:
                self.state.bankroll -= cost_paid
        else:
            self.state.bankroll -= cost_paid

        pos = PaperPosition(
            market_id=mkt.market_id,
            slug=mkt.slug,
            asset=mkt.asset,
            binance_symbol=mkt.binance_symbol,
            window_start_utc=_iso(mkt.window_start_utc),
            window_end_utc=_iso(mkt.window_end_utc),
            strike=strike,
            direction=direction,
            p_poly_entry=p_poly_up,
            p_fair_entry=p_fair_up,
            edge_entry=edge,
            fill_price=fill,
            position_usd=position_usd,
            contracts=contracts,
            cost_paid=cost_paid,
            opened_at_utc=_iso(now),
            token_id=token_id,
            live_order_id=live_order_id,
        )
        self.state.open_positions[mkt.market_id] = pos
        _save_state(Path(self.cfg.state_path), self.state)

        secs_left = int(seconds_remaining)
        if self.cfg.live_mode:
            mode_tag = "🟢 LIVE"
        elif "DEMO" in self.cfg.instance_label.upper() or self.cfg.instance_label.upper().startswith("V4A"):
            mode_tag = "📝 DEMO"
        else:
            mode_tag = "🎯"
        await self.notifier.send(
            f"{mode_tag} <b>SEÑAL {direction}</b> — {mkt.asset.upper()}\n"
            f"<i>{mkt.question}</i>\n"
            f"Strike: <code>{strike:,.2f}</code> | Spot: <code>{s_now:,.2f}</code>\n"
            f"p_poly={p_poly_up*100:.1f}% · p_fair={p_fair_up*100:.1f}% · "
            f"<b>edge={edge*100:+.1f}pp</b>\n"
            f"Fill: <code>{fill*100:.1f}¢</code> | Contratos: <code>{contracts:.2f}</code>\n"
            f"Apostado: <code>${cost_paid:.2f}</code> de bankroll <code>${self.state.bankroll:.2f}</code>\n"
            f"Resolución en {secs_left//60}m{secs_left%60:02d}s"
        )
        logger.info(
            "OPEN %s %s @ %.3f fill=%.3f contracts=%.2f cost=%.2f",
            mkt.slug, direction, p_poly_up, fill, contracts, cost_paid,
        )

    async def _settle_expired(self, now: datetime) -> None:
        to_settle = [
            (mid, p) for mid, p in self.state.open_positions.items()
            if _parse_iso(p.window_end_utc) <= now - timedelta(seconds=15)
        ]
        for mid, pos in to_settle:
            end_utc = _parse_iso(pos.window_end_utc)
            try:
                bdf = await self.binance.fetch_klines(
                    pos.binance_symbol,
                    end_utc - timedelta(minutes=2),
                    end_utc + timedelta(minutes=3),
                )
            except Exception as exc:
                logger.warning("settlement binance fetch failed: %s", exc)
                continue
            floored_end = end_utc.replace(second=0, microsecond=0)
            if floored_end not in bdf.index:
                # Bar may not be out yet; try next tick.
                continue
            end_price = float(bdf.loc[floored_end, "open"])
            outcome = "UP" if end_price >= pos.strike else "DOWN"
            correct = (outcome == pos.direction)
            resolution_src = "Binance"

            # En LIVE: la verdad la dice Polymarket, no Binance. En empates
            # (close == strike) o por oráculo distinto difieren — fue lo que
            # cantó un "+$4950 WIN" falso sobre una pérdida real.
            if self.cfg.live_mode and self.live:
                poly_correct = await self._resolve_live_outcome(pos)
                if poly_correct is None:
                    # Polymarket todavía no resolvió: esperar y reintentar.
                    pos.settle_attempts += 1
                    if pos.settle_attempts < self.cfg.max_settle_attempts:
                        _save_state(Path(self.cfg.state_path), self.state)
                        logger.info(
                            "settle wait %s (attempt %d/%d): Polymarket sin resolver",
                            pos.slug, pos.settle_attempts, self.cfg.max_settle_attempts,
                        )
                        continue
                    logger.warning(
                        "settle %s: Polymarket no resolvió tras %d intentos; uso Binance",
                        pos.slug, pos.settle_attempts,
                    )
                else:
                    correct = poly_correct
                    outcome = pos.direction if correct else ("DOWN" if pos.direction == "UP" else "UP")
                    resolution_src = "Polymarket"

            payoff = pos.contracts if correct else 0.0
            pnl = payoff - pos.cost_paid
            if self.cfg.live_mode and self.live:
                try:
                    await asyncio.sleep(3)
                    bal = await self.live.get_usdc_balance()
                    if bal >= 1.0:
                        self.state.bankroll = bal
                    else:
                        self.state.bankroll += payoff
                except Exception as exc:
                    logger.warning("live balance sync after settle: %s", exc)
                    self.state.bankroll += payoff
            else:
                self.state.bankroll += payoff
            pos.settled_at_utc = _iso(now)
            pos.outcome = outcome
            pos.correct = correct
            pos.payoff = payoff
            pos.pnl = pnl
            del self.state.open_positions[mid]
            self.state.closed_positions.append(pos)
            _save_state(Path(self.cfg.state_path), self.state)

            emoji = "✅" if correct else "❌"
            src_note = (
                f"\n<i>Resuelto por {resolution_src}</i>"
                if (self.cfg.live_mode and self.live)
                else ""
            )
            await self.notifier.send(
                f"{emoji} <b>{'WIN' if correct else 'LOSS'}</b> — {pos.asset.upper()} {pos.direction}\n"
                f"Strike: <code>{pos.strike:,.2f}</code> → Close: <code>{end_price:,.2f}</code> ({outcome})\n"
                f"Costo: <code>${pos.cost_paid:.2f}</code> | Cobro: <code>${payoff:.2f}</code>\n"
                f"<b>PnL: ${pnl:+.2f}</b>\n"
                f"Bankroll: <code>${self.state.bankroll:.2f}</code>{src_note}"
            )
            logger.info("SETTLE %s %s → %s (%s) pnl=%+.2f bankroll=%.2f",
                        pos.slug, pos.direction, outcome, resolution_src, pnl, self.state.bankroll)

    async def _resolve_live_outcome(self, pos: PaperPosition) -> bool | None:
        """Resuelve ganó/perdió con la resolución REAL de Polymarket (Gamma).

        Devuelve True (ganó), False (perdió) o None si Polymarket todavía no
        resolvió el mercado (hay que reintentar más tarde).
        """
        if self._gamma_session is None:
            return None
        try:
            gamma = GammaClient(session=self._gamma_session)
            data = await gamma._get(f"/markets/{pos.market_id}", {})  # noqa: SLF001
        except Exception as exc:
            logger.warning("resolve live outcome %s falló: %s", pos.slug, exc)
            return None
        market = data[0] if isinstance(data, list) and data else data
        if not isinstance(market, dict):
            return None
        outcome = _parse_outcome(market.get("outcomePrices"))
        if outcome not in ("UP", "DOWN"):
            return None  # aún no resuelto en Polymarket
        return outcome == pos.direction

    # --- command handlers ---------------------------------------------------
    async def _dispatch_command(self, cmd: str) -> str:
        if cmd in ("balance", "status", "pnl", "start", "stats", "dia", "today", "daily"):
            await self._sync_live_bankroll()
        handlers = {
            "start": self._cmd_start,
            "help": self._cmd_help,
            "status": self._cmd_status,
            "balance": self._cmd_balance,
            "posiciones": self._cmd_positions,
            "positions": self._cmd_positions,
            "open": self._cmd_positions,
            "dia": self._cmd_today,
            "today": self._cmd_today,
            "daily": self._cmd_today,
            "pnl": self._cmd_pnl,
            "profit": self._cmd_pnl,
            "historia": self._cmd_history,
            "history": self._cmd_history,
            "trades": self._cmd_history,
            "closed": self._cmd_history,
            "stats": self._cmd_stats,
            "config": self._cmd_config,
        }
        h = handlers.get(cmd)
        if not h:
            return f"🤔 No conozco /{cmd}. Probá /help"
        return h()

    def _cmd_start(self) -> str:
        return (
            "👋 <b>Hola!</b> Soy tu bot de Polymarket paper trading.\n\n"
            f"Bankroll actual: <code>${self.state.bankroll:.2f}</code>\n"
            "Estoy buscando oportunidades en BTC, ETH, SOL y XRP hourly.\n\n"
            "Tocá el menú azul abajo a la izquierda 📋 para ver los comandos, "
            "o mandame /help."
        )

    def _cmd_help(self) -> str:
        return (
            "📋 <b>Comandos disponibles</b>\n\n"
            "🔹 /status — estado general (resumen rápido)\n"
            "🔹 /balance — solo el bankroll\n"
            "🔹 /posiciones — qué tengo abierto ahora\n"
            "🔹 /dia — resultado del día de hoy\n"
            "🔹 /pnl — PnL total acumulado\n"
            "🔹 /historia — últimos 10 trades cerrados\n"
            "🔹 /stats — estadísticas globales (win rate, etc)\n"
            "🔹 /config — cómo está configurado el bot\n"
        )

    def _cmd_status(self) -> str:
        closed = self.state.closed_positions
        wins = sum(1 for p in closed if p.correct)
        total_pnl = self.state.bankroll - self.cfg.initial_bankroll_usd
        roi = total_pnl / self.cfg.initial_bankroll_usd * 100
        emoji = "📈" if total_pnl >= 0 else "📉"
        wr_str = f"{wins/len(closed)*100:.0f}%" if closed else "—"
        return (
            f"{emoji} <b>Estado del bot</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Bankroll: <code>${self.state.bankroll:.2f}</code> "
            f"(<b>{roi:+.1f}%</b> desde inicio)\n"
            f"📊 PnL total: <code>${total_pnl:+.2f}</code>\n"
            f"🔓 Abiertas: <code>{len(self.state.open_positions)}</code>\n"
            f"🔒 Cerradas: <code>{len(closed)}</code>  (win rate {wr_str})\n"
            f"🕒 Activo desde: <code>{self.state.started_at_utc[:16].replace('T', ' ')} UTC</code>"
        )

    def _cmd_balance(self) -> str:
        total_pnl = self.state.bankroll - self.cfg.initial_bankroll_usd
        roi = total_pnl / self.cfg.initial_bankroll_usd * 100
        emoji = "📈" if total_pnl >= 0 else "📉"
        return (
            f"💰 <b>Bankroll</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Actual: <code>${self.state.bankroll:.2f}</code>\n"
            f"Inicial: <code>${self.cfg.initial_bankroll_usd:.2f}</code>\n"
            f"PnL: {emoji} <code>${total_pnl:+.2f}</code> (<b>{roi:+.1f}%</b>)"
        )

    def _cmd_positions(self) -> str:
        if not self.state.open_positions:
            return "🪹 <b>Sin posiciones abiertas</b>\nEsperando el próximo edge."
        lines = ["🎯 <b>Posiciones abiertas</b>", "━━━━━━━━━━━━━━━━━━━━"]
        now = _now_utc()
        for p in sorted(
            self.state.open_positions.values(),
            key=lambda x: x.window_end_utc,
        ):
            end = _parse_iso(p.window_end_utc)
            mins_left = max(0, int((end - now).total_seconds() // 60))
            arrow = "🟢" if p.direction == "UP" else "🔴"
            edge_pp = p.edge_entry * 100
            lines.append(
                f"{arrow} <b>{p.asset.upper()} {p.direction}</b>\n"
                f"   Strike: <code>{p.strike:,.2f}</code>  Edge entrada: <code>{edge_pp:+.1f}pp</code>\n"
                f"   Fill: <code>{p.fill_price*100:.1f}¢</code>  "
                f"Costo: <code>${p.cost_paid:.2f}</code>  "
                f"Payoff potencial: <code>${p.contracts:.2f}</code>\n"
                f"   ⏱ Cierra en <b>{mins_left}m</b>"
            )
        total_at_risk = sum(p.cost_paid for p in self.state.open_positions.values())
        total_potential = sum(p.contracts for p in self.state.open_positions.values())
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append(
            f"💵 Capital en riesgo: <code>${total_at_risk:.2f}</code>\n"
            f"🎁 Si todo gana: <code>+${total_potential - total_at_risk:.2f}</code>"
        )
        return "\n".join(lines)

    def _cmd_today(self) -> str:
        today = _now_utc().strftime("%Y-%m-%d")
        closed_today = [
            p for p in self.state.closed_positions
            if p.settled_at_utc and p.settled_at_utc[:10] == today
        ]
        if not closed_today:
            return (
                f"📅 <b>Hoy ({today})</b>\n"
                "Sin trades cerrados todavía.\n"
                f"Abiertas en curso: <code>{len(self.state.open_positions)}</code>"
            )
        n = len(closed_today)
        wins = sum(1 for p in closed_today if p.correct)
        pnl = sum((p.pnl or 0.0) for p in closed_today)
        emoji = "📈" if pnl >= 0 else "📉"
        lines = [
            f"{emoji} <b>Resumen de hoy ({today})</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"Trades cerrados: <code>{n}</code>",
            f"Aciertos: <code>{wins}/{n}</code> ({wins/n*100:.0f}%)",
            f"PnL del día: <code>${pnl:+.2f}</code>",
            f"Bankroll ahora: <code>${self.state.bankroll:.2f}</code>",
            "",
            "<b>Detalle:</b>",
        ]
        for p in closed_today[-10:]:
            mark = "✅" if p.correct else "❌"
            lines.append(
                f"{mark} {p.asset[:3].upper()} {p.direction}  "
                f"edge={p.edge_entry*100:+.1f}pp  "
                f"PnL <code>${(p.pnl or 0):+.2f}</code>"
            )
        return "\n".join(lines)

    def _cmd_pnl(self) -> str:
        total_pnl = self.state.bankroll - self.cfg.initial_bankroll_usd
        roi = total_pnl / self.cfg.initial_bankroll_usd * 100
        closed = self.state.closed_positions
        realized = sum((p.pnl or 0) for p in closed)
        unrealized = total_pnl - realized
        # Estimate days running for an annualized projection.
        start = _parse_iso(self.state.started_at_utc)
        days = max(0.01, (_now_utc() - start).total_seconds() / 86400)
        daily_avg = realized / days
        annualized_pct = (1 + roi/100) ** (365/days) - 1 if days > 0.5 else None
        emoji = "📈" if total_pnl >= 0 else "📉"
        lines = [
            f"{emoji} <b>PnL acumulado</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"Total: <code>${total_pnl:+.2f}</code> (<b>{roi:+.1f}%</b>)",
            f"  · Realizado: <code>${realized:+.2f}</code>",
            f"  · No realizado (abiertas): <code>${unrealized:+.2f}</code>",
            "",
            f"⏱ Corriendo hace {days:.1f} días",
            f"📊 Promedio diario: <code>${daily_avg:+.2f}</code>",
        ]
        if annualized_pct is not None and days >= 1:
            lines.append(f"🔮 Proyección anualizada: <code>{annualized_pct*100:+.0f}%</code>")
        return "\n".join(lines)

    def _cmd_history(self) -> str:
        closed = self.state.closed_positions
        if not closed:
            return "📭 <b>Sin trades cerrados todavía</b>"
        recent = closed[-10:][::-1]
        lines = ["📜 <b>Últimos trades cerrados</b>", "━━━━━━━━━━━━━━━━━━━━"]
        for p in recent:
            mark = "✅" if p.correct else "❌"
            when = (p.settled_at_utc or "")[5:16].replace("T", " ")
            lines.append(
                f"{mark} <code>{when}</code> {p.asset[:3].upper()} {p.direction}  "
                f"PnL <code>${(p.pnl or 0):+.2f}</code>"
            )
        total_pnl = sum((p.pnl or 0) for p in closed)
        wins = sum(1 for p in closed if p.correct)
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append(
            f"Total: <code>{len(closed)}</code> trades, "
            f"win rate <b>{wins/len(closed)*100:.0f}%</b>, "
            f"PnL <code>${total_pnl:+.2f}</code>"
        )
        return "\n".join(lines)

    def _cmd_stats(self) -> str:
        closed = self.state.closed_positions
        if not closed:
            return "📊 <b>Sin estadísticas aún</b>\nAbrí algunas posiciones primero."
        n = len(closed)
        wins = sum(1 for p in closed if p.correct)
        win_rate = wins / n * 100
        total_pnl = sum((p.pnl or 0) for p in closed)
        avg_pnl = total_pnl / n
        avg_edge = sum(p.edge_entry for p in closed) / n * 100
        best = max(closed, key=lambda p: p.pnl or 0)
        worst = min(closed, key=lambda p: p.pnl or 0)
        # Per-asset breakdown
        by_asset: dict[str, list] = {}
        for p in closed:
            by_asset.setdefault(p.asset, []).append(p)
        lines = [
            "📊 <b>Estadísticas globales</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"Trades: <code>{n}</code>",
            f"Win rate: <b>{win_rate:.1f}%</b> ({wins}W / {n-wins}L)",
            f"PnL total: <code>${total_pnl:+.2f}</code>",
            f"PnL promedio/trade: <code>${avg_pnl:+.2f}</code>",
            f"Edge promedio en entrada: <code>{avg_edge:+.1f}pp</code>",
            "",
            f"🏆 Mejor: {best.asset[:3].upper()} {best.direction} "
            f"<code>${(best.pnl or 0):+.2f}</code>",
            f"💀 Peor: {worst.asset[:3].upper()} {worst.direction} "
            f"<code>${(worst.pnl or 0):+.2f}</code>",
            "",
            "<b>Por asset:</b>",
        ]
        for asset, plist in sorted(by_asset.items()):
            wr = sum(1 for x in plist if x.correct) / len(plist) * 100
            pnl_a = sum((x.pnl or 0) for x in plist)
            lines.append(
                f"  {asset[:3].upper()}: {len(plist)} trades, "
                f"WR {wr:.0f}%, PnL <code>${pnl_a:+.2f}</code>"
            )
        return "\n".join(lines)

    def _cmd_config(self) -> str:
        sizing = (
            f"Kelly {self.cfg.kelly_fraction*100:.0f}% (cap {self.cfg.max_pct_per_trade*100:.0f}%)"
            if self.cfg.sizing_mode == "kelly"
            else f"Fijo ${self.cfg.position_size_usd:.2f}"
        )
        assets = ", ".join(
            s.split("-up-or-down-")[0].upper() for s in self.cfg.series_slugs
        )
        return (
            "⚙️ <b>Configuración del bot</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"Bankroll inicial: <code>${self.cfg.initial_bankroll_usd:.2f}</code>\n"
            f"Sizing: <code>{sizing}</code>\n"
            f"Threshold edge: <code>{self.cfg.entry_threshold*100:.0f}pp</code>\n"
            f"Floor (pausa): <code>{'sin floor' if self.cfg.bankroll_floor_usd <= 0 else f'${self.cfg.bankroll_floor_usd:.2f}'}</code>\n"
            f"Max abiertas a la vez: <code>{self.cfg.max_concurrent_positions}</code>\n"
            f"Assets monitoreados: <code>{assets}</code>\n"
            f"Polling cada: <code>{self.cfg.poll_interval_sec}s</code>\n"
            f"Costos asumidos: <code>{self.cfg.half_spread_cents}¢ spread + "
            f"{self.cfg.fee_rate_pct}% fee</code>"
        )

    async def _send_daily_summary(self, prev_date: str) -> None:
        target = pd.to_datetime(prev_date).date()
        closed_today = [
            p for p in self.state.closed_positions
            if p.settled_at_utc and pd.to_datetime(p.settled_at_utc).date() == target
        ]
        if not closed_today:
            return
        n = len(closed_today)
        wins = sum(1 for p in closed_today if p.correct)
        pnl = sum((p.pnl or 0.0) for p in closed_today)
        total_pnl = self.state.bankroll - self.cfg.initial_bankroll_usd
        emoji = "📈" if pnl >= 0 else "📉"
        roi_total = total_pnl / self.cfg.initial_bankroll_usd * 100
        await self.notifier.send(
            f"{emoji} <b>Resumen {prev_date}</b>\n"
            f"Trades: <code>{n}</code> | Wins: <code>{wins}</code> ({wins/n*100:.0f}%)\n"
            f"PnL del día: <code>${pnl:+.2f}</code>\n"
            f"Bankroll: <code>${self.state.bankroll:.2f}</code> "
            f"(<code>{roi_total:+.1f}%</code> desde el inicio)\n"
            f"Total trades: {len(self.state.closed_positions)}"
        )
