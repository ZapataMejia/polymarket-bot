"""Dashboard Telegram bot — agregador de bots Polymarket (modo foco V4).

Vive en el mismo VPS donde corren los bots y lee directamente los state.json
de cada uno. NO se conecta a internet salvo para Telegram.

Comandos:
    /all      → resumen de bots activos (bankroll, PnL, posiciones)
    /today    → PnL del día por bot + total
    /week     → PnL semanal + ranking
    /month    → PnL mensual
    /best     → mejor / peor bot del día
    /trades   → últimos 10 trades de todos los bots
    /help     → lista de comandos
"""
from __future__ import annotations

import json
import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger("polymarket.dashboard_bot")


# ---------------------------------------------------------------------------
# Bot registry — los 5 bots que vamos a leer
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BotInfo:
    label: str            # corto: "V1", "V2B", etc.
    name: str             # legible: "Alerts"
    emoji: str
    state_path: Path
    threshold_pp: int     # edge mínimo, para describir
    description: str


BOTS: list[BotInfo] = [
    BotInfo(
        label="V4A",
        name="Endgame 30pp (demo)",
        emoji="⏱",
        state_path=Path("data/paper_trading_v4/state.json"),
        threshold_pp=30,
        description="edge ≥ 30pp, últimos 5 min — paper",
    ),
    BotInfo(
        label="DEMO",
        name="V4B demo",
        emoji="📝",
        state_path=Path("data/paper_trading_v4b/state.json"),
        threshold_pp=40,
        description="edge ≥ 40pp, últimos 5 min — paper",
    ),
    BotInfo(
        label="LIVE",
        name="V4A Live",
        emoji="🟢",
        state_path=Path("data/live_trading_v4a/state.json"),
        threshold_pp=30,
        description="edge ≥ 30pp, últimos 5 min — USDC real",
    ),
]


# ---------------------------------------------------------------------------
# Modelo: métricas calculadas para cada bot en un período
# ---------------------------------------------------------------------------
@dataclass
class BotMetrics:
    label: str
    emoji: str
    bankroll: float
    initial_bankroll: float
    open_positions: int
    closed_total: int
    pnl_total: float          # PnL acumulado total
    pnl_today: float          # PnL del día (UTC)
    pnl_week: float           # PnL últimos 7 días
    pnl_month: float          # PnL últimos 30 días
    win_rate_total: float     # WR sobre todos los closed
    win_rate_week: float
    trades_today: int
    trades_week: int
    recent_trades: list[dict]  # últimos N trades cerrados

    @property
    def pct_change_total(self) -> float:
        if self.initial_bankroll <= 0:
            return 0.0
        return (self.bankroll - self.initial_bankroll) / self.initial_bankroll * 100

    @property
    def is_alive(self) -> bool:
        return self.closed_total >= 0  # placeholder: si pudimos leer el state, está alive


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def load_bot_metrics(bot: BotInfo, root: Path) -> BotMetrics | None:
    """Lee el state.json del bot y calcula todas las métricas para distintos períodos."""
    path = root / bot.state_path
    if not path.exists():
        logger.warning("State file not found for %s: %s", bot.label, path)
        return None
    try:
        data = json.loads(path.read_text())
    except Exception as exc:
        logger.error("Failed to load state for %s: %s", bot.label, exc)
        return None

    bankroll = float(data.get("bankroll", 100.0))
    open_positions = data.get("open_positions", {}) or {}
    closed_positions = data.get("closed_positions", []) or []

    now = _now_utc()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=7)
    month_start = now - timedelta(days=30)

    pnl_total = 0.0
    pnl_today = 0.0
    pnl_week = 0.0
    pnl_month = 0.0
    wins_total = 0
    losses_total = 0
    wins_week = 0
    losses_week = 0
    trades_today = 0
    trades_week = 0

    for pos in closed_positions:
        pnl = float(pos.get("pnl") or 0.0)
        pnl_total += pnl
        correct = pos.get("correct")
        settled = _parse_iso(pos.get("settled_at_utc"))
        if settled is None:
            continue
        if settled >= today_start:
            pnl_today += pnl
            trades_today += 1
        if settled >= week_start:
            pnl_week += pnl
            trades_week += 1
            if correct is True:
                wins_week += 1
            elif correct is False:
                losses_week += 1
        if settled >= month_start:
            pnl_month += pnl
        if correct is True:
            wins_total += 1
        elif correct is False:
            losses_total += 1

    decided_total = wins_total + losses_total
    decided_week = wins_week + losses_week
    wr_total = (wins_total / decided_total) if decided_total > 0 else 0.0
    wr_week = (wins_week / decided_week) if decided_week > 0 else 0.0

    # asumimos initial bankroll = 100 (es la config de todos los bots)
    initial_bankroll = 100.0

    # ordenar trades cerrados por fecha desc y tomar los últimos 10
    sorted_closed = sorted(
        closed_positions,
        key=lambda p: p.get("settled_at_utc") or "",
        reverse=True,
    )
    recent = []
    for pos in sorted_closed[:10]:
        recent.append({
            "bot": bot.label,
            "asset": pos.get("asset", "?").upper()[:3],
            "direction": pos.get("direction", "?"),
            "result": "✅" if pos.get("correct") is True else ("❌" if pos.get("correct") is False else "⏳"),
            "pnl": float(pos.get("pnl") or 0.0),
            "settled_at_utc": pos.get("settled_at_utc", ""),
        })

    return BotMetrics(
        label=bot.label,
        emoji=bot.emoji,
        bankroll=bankroll,
        initial_bankroll=initial_bankroll,
        open_positions=len(open_positions),
        closed_total=len(closed_positions),
        pnl_total=pnl_total,
        pnl_today=pnl_today,
        pnl_week=pnl_week,
        pnl_month=pnl_month,
        win_rate_total=wr_total,
        win_rate_week=wr_week,
        trades_today=trades_today,
        trades_week=trades_week,
        recent_trades=recent,
    )


def load_all_metrics(root: Path) -> list[BotMetrics]:
    """Carga las métricas de los 5 bots. Si alguno falla, lo omite."""
    results: list[BotMetrics] = []
    for bot in BOTS:
        m = load_bot_metrics(bot, root)
        if m is not None:
            results.append(m)
    return results


# ---------------------------------------------------------------------------
# Formateadores de mensajes Telegram (HTML simple)
# ---------------------------------------------------------------------------
def _fmt_money(v: float, signed: bool = True) -> str:
    if v == 0:
        return "$0.00"
    sign = "-" if v < 0 else ("+" if signed else "")
    return f"{sign}${abs(v):,.2f}"


def _arrow(v: float) -> str:
    if v > 0.001:
        return "▲"
    if v < -0.001:
        return "▼"
    return "─"


def _fmt_pct(v: float, signed: bool = True) -> str:
    sign = "-" if v < 0 else ("+" if signed else "")
    return f"{sign}{abs(v):.2f}%"


def format_all(metrics: list[BotMetrics]) -> str:
    if not metrics:
        return "⚠️ No se pudieron leer los datos de ningún bot.\nVerifica que los archivos state.json existan en data/."

    now = _now_utc().strftime("%d %b %H:%M UTC")
    lines: list[str] = [
        f"<b>📊 POLYMARKET BOTS</b>",
        f"<i>{now}</i>",
        "",
    ]

    total_bankroll = 0.0
    total_pnl_today = 0.0
    total_open = 0
    total_trades_today = 0
    wins_today = 0
    losses_today = 0

    for m in metrics:
        total_bankroll += m.bankroll
        total_pnl_today += m.pnl_today
        total_open += m.open_positions
        total_trades_today += m.trades_today

        pct = m.pct_change_total
        arrow = _arrow(pct)
        bal = f"${m.bankroll:>7.2f}"
        pct_str = _fmt_pct(pct)
        lines.append(
            f"<b>{m.label:<3}</b> {m.emoji} "
            f"<code>{bal}</code>  "
            f"{pct_str:>8} {arrow}  "
            f"🔓 {m.open_positions}"
        )

    initial_total = sum(m.initial_bankroll for m in metrics) or 1.0
    total_pct = (total_bankroll - initial_total) / initial_total * 100
    lines.append("─" * 32)
    lines.append(
        f"<b>TOTAL</b>     <code>${total_bankroll:>7.2f}</code>  "
        f"{_fmt_pct(total_pct):>8} {_arrow(total_pct)}"
    )
    lines.append("")

    # Sumario del día
    lines.append(
        f"📈 <b>Hoy:</b> {total_trades_today} trades · "
        f"PnL {_fmt_money(total_pnl_today)}"
    )

    # Mejor / peor del día
    if metrics:
        by_today = sorted(metrics, key=lambda x: x.pnl_today, reverse=True)
        best, worst = by_today[0], by_today[-1]
        if best.pnl_today > 0:
            lines.append(f"🏆 Mejor: <b>{best.label}</b> ({_fmt_money(best.pnl_today)})")
        if worst.pnl_today < 0:
            lines.append(f"📉 Peor: <b>{worst.label}</b> ({_fmt_money(worst.pnl_today)})")

    lines.append("")
    lines.append("💡 <i>/trades para últimos trades · /help comandos</i>")
    return "\n".join(lines)


def format_today(metrics: list[BotMetrics]) -> str:
    now = _now_utc().strftime("%d %b %H:%M UTC")
    lines = [f"<b>📅 HOY · {now}</b>", ""]

    total_pnl = 0.0
    total_trades = 0
    for m in metrics:
        total_pnl += m.pnl_today
        total_trades += m.trades_today
        arrow = _arrow(m.pnl_today)
        lines.append(
            f"<b>{m.label:<3}</b>  "
            f"{m.trades_today:>2} trades  "
            f"<code>{_fmt_money(m.pnl_today):>9}</code> {arrow}"
        )
    lines.append("─" * 32)
    lines.append(
        f"<b>TOTAL</b>   {total_trades:>2} trades  "
        f"<code>{_fmt_money(total_pnl):>9}</code> {_arrow(total_pnl)}"
    )
    return "\n".join(lines)


def format_week(metrics: list[BotMetrics]) -> str:
    lines = ["<b>📅 ÚLTIMOS 7 DÍAS</b>", ""]
    total_pnl = 0.0
    total_trades = 0
    # Ordenar por PnL semanal (mejor arriba)
    sorted_m = sorted(metrics, key=lambda x: x.pnl_week, reverse=True)
    for i, m in enumerate(sorted_m, 1):
        total_pnl += m.pnl_week
        total_trades += m.trades_week
        arrow = _arrow(m.pnl_week)
        wr = m.win_rate_week * 100
        medal = ["🥇", "🥈", "🥉", "  ", "  "][min(i - 1, 4)]
        lines.append(
            f"{medal} <b>{m.label:<3}</b>  "
            f"{m.trades_week:>3}t  "
            f"WR {wr:>4.1f}%  "
            f"<code>{_fmt_money(m.pnl_week):>9}</code> {arrow}"
        )
    lines.append("─" * 36)
    lines.append(
        f"   <b>TOTAL</b> {total_trades:>3}t  "
        f"           <code>{_fmt_money(total_pnl):>9}</code> {_arrow(total_pnl)}"
    )
    return "\n".join(lines)


def format_month(metrics: list[BotMetrics]) -> str:
    lines = ["<b>📅 ÚLTIMOS 30 DÍAS</b>", ""]
    total_pnl = 0.0
    sorted_m = sorted(metrics, key=lambda x: x.pnl_month, reverse=True)
    for i, m in enumerate(sorted_m, 1):
        total_pnl += m.pnl_month
        arrow = _arrow(m.pnl_month)
        medal = ["🥇", "🥈", "🥉", "  ", "  "][min(i - 1, 4)]
        lines.append(
            f"{medal} <b>{m.label:<3}</b>  "
            f"<code>{_fmt_money(m.pnl_month):>10}</code> {arrow}"
        )
    lines.append("─" * 30)
    lines.append(
        f"   <b>TOTAL</b>     <code>{_fmt_money(total_pnl):>10}</code> {_arrow(total_pnl)}"
    )
    return "\n".join(lines)


def format_best(metrics: list[BotMetrics]) -> str:
    if not metrics:
        return "⚠️ Sin datos."

    by_today = sorted(metrics, key=lambda x: x.pnl_today, reverse=True)
    by_week = sorted(metrics, key=lambda x: x.pnl_week, reverse=True)
    by_total = sorted(metrics, key=lambda x: x.pct_change_total, reverse=True)

    def line(label: str, m: BotMetrics, pnl: float) -> str:
        return f"  <b>{m.label:<3}</b> {m.emoji}  {_fmt_money(pnl)}"

    lines = ["<b>🏆 RANKING</b>", "", "<b>Hoy</b>"]
    for m in by_today[:3]:
        lines.append(line("hoy", m, m.pnl_today))
    lines.extend(["", "<b>Esta semana</b>"])
    for m in by_week[:3]:
        lines.append(line("week", m, m.pnl_week))
    lines.extend(["", "<b>Total acumulado</b>"])
    for m in by_total[:3]:
        lines.append(line("total", m, m.bankroll - m.initial_bankroll))
    return "\n".join(lines)


def format_trades(metrics: list[BotMetrics], limit: int = 10) -> str:
    # juntar trades de todos los bots y ordenar por fecha desc
    all_trades: list[dict] = []
    for m in metrics:
        all_trades.extend(m.recent_trades)
    all_trades.sort(key=lambda t: t.get("settled_at_utc", ""), reverse=True)
    trades = all_trades[:limit]

    if not trades:
        return "📭 No hay trades cerrados todavía."

    lines = [f"<b>📋 ÚLTIMOS {len(trades)} TRADES</b>", ""]
    now = _now_utc()
    for t in trades:
        ts = _parse_iso(t.get("settled_at_utc"))
        ago = ""
        if ts:
            delta = now - ts
            if delta.days > 0:
                ago = f"{delta.days}d"
            elif delta.seconds >= 3600:
                ago = f"{delta.seconds // 3600}h"
            else:
                ago = f"{max(1, delta.seconds // 60)}m"
        arrow = "↑" if t["direction"] == "UP" else "↓"
        pnl_str = _fmt_money(t["pnl"])
        lines.append(
            f"<b>{t['bot']:<3}</b> · {t['asset']}{arrow} {t['result']} "
            f"<code>{pnl_str:>8}</code> · <i>{ago}</i>"
        )
    return "\n".join(lines)


def format_help() -> str:
    return (
        "<b>📚 COMANDOS DISPONIBLES</b>\n\n"
        "/all      → Resumen de los 5 bots\n"
        "/today    → PnL del día por bot\n"
        "/week     → Ranking semanal\n"
        "/month    → PnL mensual\n"
        "/best     → Mejores / peores por periodo\n"
        "/trades   → Últimos 10 trades cerrados\n"
        "/help     → Esta lista\n\n"
        "<i>Tip: /all es el mejor para ver todo de un vistazo.</i>"
    )


# ---------------------------------------------------------------------------
# Telegram long-polling loop
# ---------------------------------------------------------------------------
def send_message(token: str, chat_id: int | str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(
            url,
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=10,
        )
        if not r.ok:
            logger.error("Telegram sendMessage failed: %s %s", r.status_code, r.text)
    except Exception as exc:
        logger.error("Telegram sendMessage exception: %s", exc)


def get_updates(token: str, offset: int) -> list[dict]:
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        r = requests.get(url, params={"offset": offset, "timeout": 25}, timeout=30)
        if not r.ok:
            return []
        return r.json().get("result", [])
    except Exception as exc:
        logger.error("Telegram getUpdates exception: %s", exc)
        return []


def handle_command(cmd: str, root: Path) -> str:
    metrics = load_all_metrics(root)
    cmd = cmd.lower().strip()
    if cmd in ("/start", "/help"):
        return format_help()
    if cmd in ("/all", "/status", "/dashboard"):
        return format_all(metrics)
    if cmd == "/today":
        return format_today(metrics)
    if cmd == "/week":
        return format_week(metrics)
    if cmd == "/month":
        return format_month(metrics)
    if cmd == "/best":
        return format_best(metrics)
    if cmd in ("/trades", "/recent"):
        return format_trades(metrics, limit=10)
    return f"❓ Comando no reconocido: <code>{cmd}</code>\n\nUsá /help para ver los comandos disponibles."


def run(token: str, root: Path) -> None:
    """Loop principal del bot dashboard. Long-polling de Telegram."""
    logger.info("Dashboard bot starting. Root=%s", root)
    offset = 0

    # Aviso inicial: leer last_update_id de algún state para no procesar viejos
    # Mejor: limpiar updates pendientes al arrancar
    pending = get_updates(token, 0)
    if pending:
        offset = pending[-1]["update_id"] + 1
        logger.info("Skipped %d pending updates", len(pending))

    while True:
        try:
            updates = get_updates(token, offset)
            for upd in updates:
                offset = upd["update_id"] + 1
                msg = upd.get("message") or upd.get("edited_message")
                if not msg:
                    continue
                text = (msg.get("text") or "").strip()
                if not text.startswith("/"):
                    continue
                chat_id = msg["chat"]["id"]
                cmd = text.split()[0].split("@")[0]  # /all@bot_username -> /all
                logger.info("Received command %s from chat %s", cmd, chat_id)
                try:
                    response = handle_command(cmd, root)
                except Exception as exc:
                    logger.exception("Error handling command %s", cmd)
                    response = f"⚠️ Error procesando comando: <code>{exc}</code>"
                send_message(token, chat_id, response)
        except KeyboardInterrupt:
            logger.info("Dashboard bot stopped by user")
            return
        except Exception as exc:
            logger.exception("Unexpected error in main loop: %s", exc)
            time.sleep(5)
