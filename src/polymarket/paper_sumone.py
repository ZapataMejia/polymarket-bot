"""V3 SumOne — sum-to-one arbitrage paper trader for Polymarket Up/Down markets.

Estrategia:
  En cada mercado binario (Up + Down), si UP_price + DOWN_price < $1, comprar
  AMBOS lados. Como exactamente uno de los dos paga $1 al cierre, ganás
  $1 - costo_total = profit garantizado.

  Los Up/Down deben sumar exactamente $1 en equilibrio (no-arb). Por delays /
  imbalances de flow, a veces suman 0.95-0.99. Eso es plata gratis ajustada
  por fees y spread.

Diseño:
  - Cliente Gamma / CLOB compartidos vía aiohttp.ClientSession.
  - Estado propio (SumOneState) en JSON. Independiente de V1/V2B.
  - Polling cada `poll_interval_sec` (default 15s — más rápido que V1/V2B).
  - Settlement automático cuando vence el mercado.
"""
from __future__ import annotations

import asyncio
import json
import logging
import ssl
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiohttp
import certifi

from src.polymarket.clob import ClobClient
from src.polymarket.gamma import GammaClient, UpDownMarket, _parse_market
from src.polymarket.paper_trader import _Notifier

logger = logging.getLogger("trading.polymarket.sumone")


# ---------------------------------------------------------------------------
# Config & state
# ---------------------------------------------------------------------------


@dataclass
class SumOneConfig:
    initial_bankroll_usd: float = 100.0
    max_pct_per_arb: float = 0.10        # cap por arb al 10% del bankroll
    max_position_usd: float = 200.0      # tope absoluto (orderbook depth)
    min_position_usd: float = 1.0
    bankroll_floor_usd: float = 20.0
    margin_required: float = 0.005       # profit/par minimo despues de fees
    half_spread_cents: float = 1.5
    flat_fee_cents: float = 0.5
    fee_rate_pct: float = 2.0
    poll_interval_sec: int = 15
    min_seconds_to_resolution: int = 60
    max_seconds_to_resolution: int = 3300
    max_concurrent_positions: int = 8
    series_slugs: tuple[str, ...] = (
        "btc-up-or-down-hourly",
        "eth-up-or-down-hourly",
        "solana-up-or-down-hourly",
        "xrp-up-or-down-hourly",
    )
    state_path: str = "data/paper_trading_v3/state.json"
    instance_label: str = "V3"

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
class ArbPosition:
    """1 par de contratos (UP + DOWN) en un mercado binario."""
    market_id: str
    slug: str
    asset: str
    window_start_utc: str
    window_end_utc: str
    sum_at_entry: float
    cost_per_pair: float
    contracts: float
    position_usd: float
    expected_profit_per_pair: float
    expected_profit_total: float
    opened_at_utc: str
    settled_at_utc: str | None = None
    realized_profit: float | None = None


@dataclass
class SumOneState:
    bankroll: float
    started_at_utc: str
    last_summary_date: str | None = None
    last_telegram_update_id: int = 0
    open_positions: dict[str, ArbPosition] = field(default_factory=dict)
    closed_positions: list[ArbPosition] = field(default_factory=list)
    seen_markets: set[str] = field(default_factory=set)
    opportunities_seen: int = 0  # cuántas veces vimos sum<1 (incluso no rentable)
    opportunities_taken: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "bankroll": self.bankroll,
            "started_at_utc": self.started_at_utc,
            "last_summary_date": self.last_summary_date,
            "last_telegram_update_id": self.last_telegram_update_id,
            "open_positions": {k: asdict(v) for k, v in self.open_positions.items()},
            "closed_positions": [asdict(p) for p in self.closed_positions],
            "seen_markets": sorted(self.seen_markets),
            "opportunities_seen": self.opportunities_seen,
            "opportunities_taken": self.opportunities_taken,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "SumOneState":
        return cls(
            bankroll=float(data.get("bankroll", 0.0)),
            started_at_utc=data.get("started_at_utc",
                                    datetime.now(timezone.utc).isoformat()),
            last_summary_date=data.get("last_summary_date"),
            last_telegram_update_id=int(data.get("last_telegram_update_id", 0)),
            open_positions={
                k: ArbPosition(**v) for k, v in data.get("open_positions", {}).items()
            },
            closed_positions=[ArbPosition(**p) for p in data.get("closed_positions", [])],
            seen_markets=set(data.get("seen_markets", [])),
            opportunities_seen=int(data.get("opportunities_seen", 0)),
            opportunities_taken=int(data.get("opportunities_taken", 0)),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).isoformat()


def _save_state(path: Path, state: SumOneState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state.to_json(), indent=2, default=str))
    tmp.replace(path)


def _load_state(path: Path, initial_bankroll: float) -> SumOneState:
    if not path.exists():
        return SumOneState(bankroll=initial_bankroll,
                           started_at_utc=_iso(_now_utc()))
    try:
        return SumOneState.from_json(json.loads(path.read_text()))
    except Exception as exc:
        logger.error("Could not load state, starting fresh: %s", exc)
        return SumOneState(bankroll=initial_bankroll,
                           started_at_utc=_iso(_now_utc()))


# ---------------------------------------------------------------------------
# Trader
# ---------------------------------------------------------------------------


class SumOneTrader:
    """V3 — sum-to-one arb daemon."""

    def __init__(
        self,
        config: SumOneConfig,
        telegram_token: str | None,
        telegram_chat_id: str | None,
    ):
        self.cfg = config
        self.notify = _Notifier(telegram_token, telegram_chat_id)
        self.state: SumOneState = _load_state(
            Path(config.state_path), config.initial_bankroll_usd
        )
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    async def run(self) -> None:
        await self.notify.set_commands([
            ("start",     "Mensaje de bienvenida"),
            ("help",      "Lista de comandos"),
            ("status",    "Estado general"),
            ("balance",   "Bankroll actual"),
            ("posiciones", "Arbs abiertos"),
            ("pnl",       "PnL acumulado"),
            ("historia",  "Últimos arbs cerrados"),
            ("stats",     "Stats globales"),
            ("config",    "Configuración"),
        ])
        await self._send_welcome()

        ctx = ssl.create_default_context(cafile=certifi.where())
        connector = aiohttp.TCPConnector(ssl=ctx, limit=20)
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            gamma = GammaClient(session=session)
            clob = ClobClient(session=session)
            await asyncio.gather(
                self._trading_loop(gamma, clob),
                self._command_loop(),
            )

    async def _trading_loop(self, gamma: GammaClient, clob: ClobClient) -> None:
        while not self._stop:
            try:
                await self._tick(gamma, clob)
            except Exception as exc:
                logger.exception("[%s] tick failed: %s",
                                 self.cfg.instance_label, exc)
            await asyncio.sleep(self.cfg.poll_interval_sec)

    async def _command_loop(self) -> None:
        """Escucha comandos de Telegram y responde con estado."""
        while not self._stop:
            updates = await self.notify.get_updates(
                offset=self.state.last_telegram_update_id + 1,
                timeout=25,
            )
            for upd in updates:
                self.state.last_telegram_update_id = max(
                    self.state.last_telegram_update_id,
                    int(upd.get("update_id", 0)),
                )
                msg = upd.get("message") or {}
                text = (msg.get("text") or "").strip()
                chat = msg.get("chat") or {}
                if str(chat.get("id")) != self.notify.chat_id:
                    continue
                if not text.startswith("/"):
                    continue
                cmd = text.split()[0].split("@")[0].lstrip("/").lower()
                try:
                    reply = self._dispatch_command(cmd)
                except Exception as exc:
                    logger.exception("[%s] command failed: %s",
                                     self.cfg.instance_label, cmd)
                    reply = f"⚠️ Error procesando /{cmd}: {exc}"
                if reply:
                    await self.notify.send(reply)
            if updates:
                _save_state(Path(self.cfg.state_path), self.state)
            await asyncio.sleep(0.5)

    # ---- Command handlers --------------------------------------------------

    def _dispatch_command(self, cmd: str) -> str:
        handlers = {
            "start":      self._cmd_start,
            "help":       self._cmd_help,
            "status":     self._cmd_status,
            "balance":    self._cmd_balance,
            "posiciones": self._cmd_positions,
            "positions":  self._cmd_positions,
            "open":       self._cmd_positions,
            "pnl":        self._cmd_pnl,
            "profit":     self._cmd_pnl,
            "historia":   self._cmd_history,
            "history":    self._cmd_history,
            "trades":     self._cmd_history,
            "stats":      self._cmd_stats,
            "config":     self._cmd_config,
        }
        h = handlers.get(cmd)
        if not h:
            return f"🤔 No conozco /{cmd}. Probá /help"
        return h()

    def _cmd_start(self) -> str:
        return (
            "👋 <b>Hola!</b> Soy el bot V3 SumOne.\n\n"
            f"Bankroll actual: <code>${self.state.bankroll:.2f}</code>\n"
            "Mi trabajo: detectar cuando UP + DOWN suman menos de $1 y "
            "comprar ambos para garantizar profit.\n\n"
            "Tocá el menú azul abajo a la izquierda 📋 o mandame /help."
        )

    def _cmd_help(self) -> str:
        return (
            "📋 <b>Comandos disponibles (V3 SumOne)</b>\n\n"
            "🔹 /status — estado general\n"
            "🔹 /balance — solo el bankroll\n"
            "🔹 /posiciones — arbs abiertos ahora\n"
            "🔹 /pnl — PnL total acumulado\n"
            "🔹 /historia — últimos 10 arbs cerrados\n"
            "🔹 /stats — estadísticas globales\n"
            "🔹 /config — cómo está configurado"
        )

    def _cmd_status(self) -> str:
        closed = self.state.closed_positions
        wins = sum(1 for p in closed if (p.realized_profit or 0) > 0)
        total_pnl = self.state.bankroll - self.cfg.initial_bankroll_usd
        roi = total_pnl / self.cfg.initial_bankroll_usd * 100
        emoji = "📈" if total_pnl >= 0 else "📉"
        wr_str = f"{wins/len(closed)*100:.0f}%" if closed else "—"
        return (
            f"{emoji} <b>Estado V3 SumOne</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Bankroll: <code>${self.state.bankroll:.2f}</code> "
            f"(<b>{roi:+.1f}%</b>)\n"
            f"📊 PnL: <code>${total_pnl:+.4f}</code>\n"
            f"🔓 Arbs abiertos: <code>{len(self.state.open_positions)}</code>\n"
            f"🔒 Cerrados: <code>{len(closed)}</code> (WR {wr_str})\n"
            f"👀 Oportunidades vistas: <code>{self.state.opportunities_seen}</code>\n"
            f"🎯 Tomadas: <code>{self.state.opportunities_taken}</code>\n"
            f"🕒 Activo desde: <code>{self.state.started_at_utc[:16].replace('T',' ')} UTC</code>"
        )

    def _cmd_balance(self) -> str:
        total_pnl = self.state.bankroll - self.cfg.initial_bankroll_usd
        roi = total_pnl / self.cfg.initial_bankroll_usd * 100
        emoji = "📈" if total_pnl >= 0 else "📉"
        return (
            f"💰 <b>Bankroll V3</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Actual: <code>${self.state.bankroll:.2f}</code>\n"
            f"Inicial: <code>${self.cfg.initial_bankroll_usd:.2f}</code>\n"
            f"PnL: {emoji} <code>${total_pnl:+.4f}</code> "
            f"(<b>{roi:+.2f}%</b>)"
        )

    def _cmd_positions(self) -> str:
        if not self.state.open_positions:
            return "🪹 <b>Sin arbs abiertos</b>\nEsperando próxima oportunidad."
        lines = ["🎯 <b>Arbs abiertos V3</b>", "━━━━━━━━━━━━━━━━━━━━"]
        now = _now_utc()
        for p in sorted(
            self.state.open_positions.values(),
            key=lambda x: x.window_end_utc,
        ):
            end = datetime.fromisoformat(p.window_end_utc.replace("Z", "+00:00"))
            mins_left = max(0, int((end - now).total_seconds() // 60))
            gap_cents = (1 - p.sum_at_entry) * 100
            lines.append(
                f"💎 <b>{p.asset.upper()}</b>\n"
                f"   Sum entrada: <code>${p.sum_at_entry:.4f}</code> "
                f"(gap {gap_cents:.2f}¢)\n"
                f"   Contracts: <code>{p.contracts:.2f}</code>  "
                f"Stake: <code>${p.position_usd:.2f}</code>\n"
                f"   Profit esperado: <code>${p.expected_profit_total:+.4f}</code>\n"
                f"   ⏱ Cierra en <b>{mins_left}m</b>"
            )
        total_stake = sum(p.position_usd for p in self.state.open_positions.values())
        total_exp = sum(p.expected_profit_total for p in self.state.open_positions.values())
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append(
            f"💵 Capital comprometido: <code>${total_stake:.2f}</code>\n"
            f"🎁 Profit esperado total: <code>${total_exp:+.4f}</code>"
        )
        return "\n".join(lines)

    def _cmd_pnl(self) -> str:
        total_pnl = self.state.bankroll - self.cfg.initial_bankroll_usd
        roi = total_pnl / self.cfg.initial_bankroll_usd * 100
        closed = self.state.closed_positions
        if not closed:
            return (
                f"📊 <b>PnL V3</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Total: <code>${total_pnl:+.4f}</code> ({roi:+.2f}%)\n"
                f"<i>Aún sin arbs cerrados.</i>"
            )
        wins = [p for p in closed if (p.realized_profit or 0) > 0]
        avg_pnl = sum(p.realized_profit or 0 for p in closed) / len(closed)
        best = max((p.realized_profit or 0) for p in closed)
        worst = min((p.realized_profit or 0) for p in closed)
        return (
            f"📊 <b>PnL V3 SumOne</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Total: <code>${total_pnl:+.4f}</code> ({roi:+.2f}%)\n"
            f"Arbs cerrados: <code>{len(closed)}</code>\n"
            f"Ganados: <code>{len(wins)}</code> "
            f"({len(wins)/len(closed)*100:.0f}%)\n"
            f"Promedio: <code>${avg_pnl:+.4f}</code>\n"
            f"Mejor: <code>${best:+.4f}</code>\n"
            f"Peor: <code>${worst:+.4f}</code>"
        )

    def _cmd_history(self) -> str:
        closed = self.state.closed_positions
        if not closed:
            return "🪹 <b>Sin arbs cerrados</b> aún."
        recent = closed[-10:][::-1]
        lines = [f"🗂 <b>Últimos {len(recent)} arbs</b>", "━━━━━━━━━━━━━━━━━━━━"]
        for p in recent:
            icon = "✅" if (p.realized_profit or 0) > 0 else "❌"
            gap_cents = (1 - p.sum_at_entry) * 100
            settled = (p.settled_at_utc or "")[:16].replace("T", " ")
            lines.append(
                f"{icon} <code>{p.asset.upper()}</code>  "
                f"gap <code>{gap_cents:.2f}¢</code>  "
                f"PnL <code>${p.realized_profit or 0:+.4f}</code>\n"
                f"   <i>{settled} UTC</i>"
            )
        return "\n".join(lines)

    def _cmd_stats(self) -> str:
        closed = self.state.closed_positions
        if not closed:
            return (
                "📊 <b>Stats V3</b>\n"
                f"Oportunidades vistas: <code>{self.state.opportunities_seen}</code>\n"
                f"Oportunidades tomadas: <code>{self.state.opportunities_taken}</code>\n"
                f"<i>Aún sin arbs cerrados.</i>"
            )
        wins = sum(1 for p in closed if (p.realized_profit or 0) > 0)
        wr = wins / len(closed) * 100
        total_profit = sum(p.realized_profit or 0 for p in closed)
        total_stake = sum(p.position_usd for p in closed)
        roi_per_dollar = (total_profit / total_stake * 100) if total_stake else 0
        avg_gap = sum((1 - p.sum_at_entry) * 100 for p in closed) / len(closed)
        take_rate = (
            self.state.opportunities_taken / self.state.opportunities_seen * 100
            if self.state.opportunities_seen else 0
        )
        return (
            f"📊 <b>Stats V3 SumOne</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Arbs cerrados: <code>{len(closed)}</code>\n"
            f"WR: <b>{wr:.1f}%</b>\n"
            f"Profit total: <code>${total_profit:+.4f}</code>\n"
            f"ROI sobre stake: <b>{roi_per_dollar:.2f}%</b>\n"
            f"Gap promedio de entrada: <code>{avg_gap:.2f}¢</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Oportunidades vistas: <code>{self.state.opportunities_seen}</code>\n"
            f"Tomadas: <code>{self.state.opportunities_taken}</code> "
            f"({take_rate:.0f}%)"
        )

    def _cmd_config(self) -> str:
        return (
            f"⚙️ <b>Config V3 SumOne</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Bankroll inicial: <code>${self.cfg.initial_bankroll_usd:.2f}</code>\n"
            f"Margen mínimo: <code>{self.cfg.margin_required*100:.2f}¢</code>\n"
            f"Max % por arb: <code>{self.cfg.max_pct_per_arb*100:.0f}%</code>\n"
            f"Tope absoluto: <code>${self.cfg.max_position_usd:.0f}</code>\n"
            f"Bankroll floor: <code>${self.cfg.bankroll_floor_usd:.2f}</code>\n"
            f"Max concurrentes: <code>{self.cfg.max_concurrent_positions}</code>\n"
            f"Poll: <code>{self.cfg.poll_interval_sec}s</code>\n"
            f"Costos: <code>{self.cfg.half_spread_cents}¢ spread + "
            f"{self.cfg.flat_fee_cents}¢ flat + "
            f"{self.cfg.fee_rate_pct}% prop fee</code>\n"
            f"Assets: <code>{', '.join(s.split('-')[0].upper() for s in self.cfg.series_slugs)}</code>"
        )

    async def _send_welcome(self) -> None:
        msg = (
            f"🤖 <b>{self.cfg.instance_label} SumOne</b> encendido\n"
            f"Bankroll: <code>${self.state.bankroll:.2f}</code>\n"
            f"Estrategia: sum-to-one arbitrage (UP + DOWN &lt; $1)\n"
            f"Margen mínimo: <code>{self.cfg.margin_required*100:.2f}¢</code> por par\n"
            f"Poll: <code>{self.cfg.poll_interval_sec}s</code> · "
            f"Max abiertas: <code>{self.cfg.max_concurrent_positions}</code>\n\n"
            f"Cuando UP + DOWN suman &lt; $1, compro AMBOS lados.\n"
            f"<b>Risk-free</b>: uno de los dos siempre paga $1.\n\n"
            f"Usá /help para ver comandos disponibles."
        )
        await self.notify.send(msg)

    async def _tick(self, gamma: GammaClient, clob: ClobClient) -> None:
        now = _now_utc()
        await self._settle_expired(now)
        markets = await self._discover_open_markets(gamma, now)
        new_opps = 0
        for m in markets:
            if self._stop:
                break
            if m.market_id in self.state.open_positions:
                continue
            if len(self.state.open_positions) >= self.cfg.max_concurrent_positions:
                break
            if (m.window_end_utc - now).total_seconds() < self.cfg.min_seconds_to_resolution:
                continue
            opened = await self._evaluate_market(m, clob, now)
            if opened:
                new_opps += 1
        logger.info(
            "[%s] tick %s: markets=%d open=%d closed=%d seen_opps=%d taken=%d new=%d",
            self.cfg.instance_label, now.strftime("%H:%M:%S"),
            len(markets), len(self.state.open_positions),
            len(self.state.closed_positions),
            self.state.opportunities_seen,
            self.state.opportunities_taken,
            new_opps,
        )
        _save_state(Path(self.cfg.state_path), self.state)

    async def _discover_open_markets(
        self, gamma: GammaClient, now: datetime,
    ) -> list[UpDownMarket]:
        max_end = now + timedelta(seconds=self.cfg.max_seconds_to_resolution + 600)
        out: list[UpDownMarket] = []
        seen: set[str] = set()
        for slug in self.cfg.series_slugs:
            for page in range(8):
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
                    logger.debug("[%s] gamma err: %s", self.cfg.instance_label, exc)
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

    async def _evaluate_market(
        self, m: UpDownMarket, clob: ClobClient, now: datetime,
    ) -> bool:
        try:
            up_df = await clob.fetch_price_history(
                m.token_id_up, now - timedelta(minutes=3),
                now + timedelta(minutes=1), fidelity_min=1,
            )
            down_df = await clob.fetch_price_history(
                m.token_id_down, now - timedelta(minutes=3),
                now + timedelta(minutes=1), fidelity_min=1,
            )
        except Exception as exc:
            logger.debug("[%s] price fetch failed for %s: %s",
                         self.cfg.instance_label, m.slug, exc)
            return False
        if up_df.empty or down_df.empty:
            return False
        up_mid = float(up_df["price"].iloc[-1])
        down_mid = float(down_df["price"].iloc[-1])
        total = up_mid + down_mid

        if total < 1.0:
            self.state.opportunities_seen += 1
            logger.info(
                "[%s] OPP sum=%.4f gap=%.2f¢  %s",
                self.cfg.instance_label, total, (1 - total) * 100, m.slug,
            )

        # Costo total por par
        gross = total + 2 * self.cfg.half_spread
        prop_fee = gross * self.cfg.fee_rate
        cost_per_pair = gross + 2 * self.cfg.flat_fee + prop_fee
        expected_profit_per_pair = 1.0 - cost_per_pair

        if expected_profit_per_pair < self.cfg.margin_required:
            return False

        if self.state.bankroll <= self.cfg.bankroll_floor_usd:
            return False

        budget = min(
            self.state.bankroll * self.cfg.max_pct_per_arb,
            self.cfg.max_position_usd,
        )
        contracts = budget / cost_per_pair
        position_usd = contracts * cost_per_pair
        if position_usd < self.cfg.min_position_usd:
            return False

        pos = ArbPosition(
            market_id=m.market_id,
            slug=m.slug,
            asset=m.asset,
            window_start_utc=_iso(m.window_start_utc),
            window_end_utc=_iso(m.window_end_utc),
            sum_at_entry=total,
            cost_per_pair=cost_per_pair,
            contracts=contracts,
            position_usd=position_usd,
            expected_profit_per_pair=expected_profit_per_pair,
            expected_profit_total=contracts * expected_profit_per_pair,
            opened_at_utc=_iso(now),
        )
        self.state.bankroll -= position_usd
        self.state.open_positions[m.market_id] = pos
        self.state.opportunities_taken += 1
        logger.info(
            "[%s] OPEN arb %s  sum=%.4f cost=%.4f profit/pair=%.4f contracts=%.2f "
            "stake=$%.2f exp_profit=$%.4f",
            self.cfg.instance_label, m.slug, total, cost_per_pair,
            expected_profit_per_pair, contracts, position_usd,
            pos.expected_profit_total,
        )
        await self.notify.send(
            f"🎯 <b>{self.cfg.instance_label} ARB OPEN</b>\n"
            f"<code>{m.slug}</code>\n"
            f"UP+DOWN = ${total:.4f} (gap = {(1-total)*100:.2f}¢)\n"
            f"Costo/par: ${cost_per_pair:.4f}\n"
            f"Contracts: {contracts:.2f}\n"
            f"Stake: ${position_usd:.2f}\n"
            f"Profit esperado: <b>${pos.expected_profit_total:+.4f}</b>\n"
            f"Bankroll: ${self.state.bankroll:.2f}"
        )
        return True

    async def _settle_expired(self, now: datetime) -> None:
        to_close = []
        for mid, p in self.state.open_positions.items():
            end = datetime.fromisoformat(p.window_end_utc.replace("Z", "+00:00"))
            if now >= end + timedelta(seconds=30):
                to_close.append(mid)
        for mid in to_close:
            p = self.state.open_positions.pop(mid)
            payoff = p.contracts * 1.0
            self.state.bankroll += payoff
            realized = payoff - p.position_usd
            p.settled_at_utc = _iso(now)
            p.realized_profit = realized
            self.state.closed_positions.append(p)
            logger.info(
                "[%s] SETTLED %s payoff=$%.2f realized=$%+.4f bankroll=$%.2f",
                self.cfg.instance_label, p.slug, payoff, realized,
                self.state.bankroll,
            )
            icon = "✅" if realized > 0 else "❌"
            await self.notify.send(
                f"{icon} <b>{self.cfg.instance_label} settled</b>\n"
                f"<code>{p.slug}</code>\n"
                f"Realized: <b>${realized:+.4f}</b>\n"
                f"Esperado: ${p.expected_profit_total:+.4f}\n"
                f"Bankroll: ${self.state.bankroll:.2f}"
            )
