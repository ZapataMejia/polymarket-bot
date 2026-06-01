"""Backtester de la estrategia "Agotamientos" (Cristian / TFT) — versión parametrizable.

Fiel al pseudocódigo en Estrategia_Agotamiento_Cristian.md, pero con TODOS los puntos
"a ojo" expuestos como parámetros para poder experimentar.

Mecánica (LONG; SHORT es espejo):
  1) IMPULSO: el precio rompe el último swing high (rompe estructura al alza).
  2) RETROCESO: secuencia de velas bajistas (>= min_retrace_candles) que respeta el
     módulo de arranque (el swing low previo al impulso). Línea de agotamiento =
     máximo (con mecha) de la última vela roja.
  3) ENTRADA: una vela rompe la línea de agotamiento -> compra.
     SL = mínimo del retroceso (último rebote) - buffer.  TP = entrada + ratio * riesgo.
No se cierra manual: se mantiene hasta SL o TP.

Uso:
  python scripts/agotamiento_backtest.py --tf 5m --direction both
  python scripts/agotamiento_backtest.py --tf 5m --tp-ratio 2 --swing 3 --no-session
"""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, asdict
from datetime import time
from pathlib import Path

import numpy as np
import pandas as pd

os.environ.setdefault("MPLCONFIGDIR", str(Path(__file__).resolve().parent.parent / ".mplcache"))

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "raw" / "nasdaq"
OUT = ROOT / "data" / "agotamiento"
OUT.mkdir(parents=True, exist_ok=True)


@dataclass
class Params:
    tf: str = "5m"                 # timeframe de estructura/ejecución
    symbol: str = "NQF"            # NQF (NQ=F) o QQQ
    swing: int = 2                 # velas a cada lado para definir un pivote (swing)
    min_retrace_candles: int = 2   # velas opuestas mínimas en el retroceso
    tp_ratio: float = 1.0          # take profit como múltiplo del riesgo (1 a 1 = video)
    sl_buffer_ticks: float = 1.0   # ticks de colchón bajo/sobre el último rebote
    tick: float = 0.25             # tamaño de tick del Nasdaq
    point_value: float = 2.0       # $ por punto (MNQ = $2; NQ/E-mini = $20)
    commission_rt: float = 1.24    # comisión ida y vuelta por contrato (MNQ aprox)
    slippage_ticks: float = 1.0    # slippage por lado
    direction: str = "both"        # long / short / both
    session: bool = True           # limitar entradas a la sesión de NY
    sess_start: str = "09:30"      # inicio de ventana de entradas (ET)
    sess_end: str = "15:55"        # fin de ventana de entradas (ET)
    max_hold_bars: int = 0         # 0 = sin límite (mantener hasta SL/TP)
    # --- reglas v2 (plan de Juli/Cris) ---
    max_trades_per_day: int = 0    # 0 = sin límite (Cris: 3)
    stop_after_first_loss: bool = False  # si la 1ª del día da SL -> fuera ese día
    breakeven_after_r: float = 0.0 # al alcanzar Nx riesgo a favor, sube SL a entrada (0 = off)
    force_close: str = ""          # "HH:MM" ET: cierra cualquier posición abierta a esa hora


def load(p: Params) -> pd.DataFrame:
    path = DATA / f"{p.symbol}_{p.tf}.parquet"
    df = pd.read_parquet(path)
    df = df[["open", "high", "low", "close", "volume"]].dropna()
    df = df[~df.index.duplicated(keep="first")].sort_index()
    return df


def confirmed_pivots(high: np.ndarray, low: np.ndarray, L: int):
    """Devuelve, para cada barra t, el precio del último swing high/low YA confirmado
    (un pivote en i se confirma en i+L, sin mirar al futuro)."""
    n = len(high)
    sh_at = np.full(n, np.nan)  # precio del swing high cuyo pivote ocurrió, indexado por barra-pivote
    sl_at = np.full(n, np.nan)
    for i in range(L, n - L):
        win_h = high[i - L:i + L + 1]
        win_l = low[i - L:i + L + 1]
        if high[i] == win_h.max() and (win_h == high[i]).sum() == 1:
            sh_at[i] = high[i]
        if low[i] == win_l.min() and (win_l == low[i]).sum() == 1:
            sl_at[i] = low[i]
    last_sh = np.full(n, np.nan)
    last_sl = np.full(n, np.nan)
    cur_sh = np.nan
    cur_sl = np.nan
    for t in range(n):
        src = t - L  # pivote que se confirma exactamente en t
        if src >= 0:
            if not np.isnan(sh_at[src]):
                cur_sh = sh_at[src]
            if not np.isnan(sl_at[src]):
                cur_sl = sl_at[src]
        last_sh[t] = cur_sh
        last_sl[t] = cur_sl
    return last_sh, last_sl


def in_session(ts, t0: time, t1: time) -> bool:
    tt = ts.time()
    return t0 <= tt <= t1


def run(p: Params) -> dict:
    df = load(p)
    o = df["open"].values
    h = df["high"].values
    lo = df["low"].values
    c = df["close"].values
    idx = df.index
    n = len(df)
    last_sh, last_sl = confirmed_pivots(h, lo, p.swing)

    t0 = time.fromisoformat(p.sess_start)
    t1 = time.fromisoformat(p.sess_end)
    slip = p.slippage_ticks * p.tick
    buf = p.sl_buffer_ticks * p.tick

    do_long = p.direction in ("long", "both")
    do_short = p.direction in ("short", "both")
    fclose = time.fromisoformat(p.force_close) if p.force_close else None

    trades = []
    pos = None  # dict si hay posición abierta
    cur_day = None
    trades_today = 0
    day_locked = False

    def can_enter(ts) -> bool:
        if day_locked:
            return False
        if p.max_trades_per_day and trades_today >= p.max_trades_per_day:
            return False
        return (not p.session) or in_session(ts, t0, t1)

    # estado del setup
    st_long = {"impulse": False, "module": np.nan, "exline": np.nan, "reds": 0, "rlow": np.inf}
    st_short = {"impulse": False, "module": np.nan, "exline": np.nan, "greens": 0, "rhigh": -np.inf}

    def reset(st, side):
        st["impulse"] = False
        st["module"] = np.nan
        st["exline"] = np.nan
        if side == "long":
            st["reds"] = 0
            st["rlow"] = np.inf
        else:
            st["greens"] = 0
            st["rhigh"] = -np.inf

    for t in range(p.swing + 1, n):
        ts = idx[t]

        # ---- reset diario ----
        if ts.date() != cur_day:
            cur_day = ts.date()
            trades_today = 0
            day_locked = False

        # ---- gestión de posición abierta (revisa intrabar SL/TP en la barra t) ----
        if pos is not None:
            hit_sl = lo[t] <= pos["sl"] if pos["dir"] == 1 else h[t] >= pos["sl"]
            hit_tp = h[t] >= pos["tp"] if pos["dir"] == 1 else lo[t] <= pos["tp"]
            exit_price = None
            reason = None
            # si ambos se tocan en la misma barra, asumimos lo peor (SL primero)
            if hit_sl:
                exit_price = pos["sl"]
                reason = "SL"
            elif hit_tp:
                exit_price = pos["tp"]
                reason = "TP"
            elif fclose is not None and ts.time() >= fclose:
                exit_price = c[t]
                reason = "EOD"
            elif p.max_hold_bars and (t - pos["bar"]) >= p.max_hold_bars:
                exit_price = c[t]
                reason = "TIME"
            if exit_price is not None:
                pts = (exit_price - pos["entry"]) * pos["dir"] - 2 * slip
                pnl = pts * p.point_value - p.commission_rt
                trades.append({
                    "entry_time": pos["time"], "exit_time": ts, "dir": pos["dir"],
                    "entry": pos["entry"], "exit": exit_price, "sl": pos["sl"], "tp": pos["tp"],
                    "points": pts, "pnl": pnl, "reason": reason,
                })
                if pnl <= 0 and p.stop_after_first_loss:
                    day_locked = True
                pos = None
                continue
            else:
                # breakeven: al alcanzar Nx riesgo a favor, sube SL a la entrada (para barras futuras)
                if p.breakeven_after_r > 0 and not pos.get("be_armed"):
                    if pos["dir"] == 1 and h[t] >= pos["entry"] + p.breakeven_after_r * pos["risk"]:
                        pos["sl"] = max(pos["sl"], pos["entry"])
                        pos["be_armed"] = True
                    elif pos["dir"] == -1 and lo[t] <= pos["entry"] - p.breakeven_after_r * pos["risk"]:
                        pos["sl"] = min(pos["sl"], pos["entry"])
                        pos["be_armed"] = True
                continue  # sigue en posición, no busca setups

        if pos is not None:
            continue

        sh = last_sh[t]
        sl = last_sl[t]
        bull = c[t] > o[t]
        bear = c[t] < o[t]

        # =================== LONG ===================
        if do_long:
            st = st_long
            if not st["impulse"]:
                # Paso 1: impulso rompe estructura (cierre supera el último swing high)
                if not np.isnan(sh) and c[t] > sh and not np.isnan(sl):
                    st["impulse"] = True
                    st["module"] = sl       # módulo de arranque a respetar
                    st["reds"] = 0
                    st["rlow"] = np.inf
                    st["exline"] = np.nan
            else:
                # se rompió el módulo -> cancelar
                if lo[t] < st["module"]:
                    reset(st, "long")
                else:
                    if bear:
                        st["reds"] += 1
                        st["exline"] = h[t]  # línea de agotamiento sigue la última vela roja (con mecha)
                        st["rlow"] = min(st["rlow"], lo[t])
                    elif st["reds"] >= p.min_retrace_candles and not np.isnan(st["exline"]):
                        # Paso 3: vela rompe la línea de agotamiento
                        if h[t] > st["exline"]:
                            if can_enter(ts):
                                entry = st["exline"] + slip
                                sl_px = st["rlow"] - buf   # SL en la zona de riesgo previa (Cris)
                                risk = entry - sl_px
                                if risk > 0:
                                    pos = {
                                        "dir": 1, "entry": entry, "sl": sl_px,
                                        "tp": entry + p.tp_ratio * risk, "risk": risk,
                                        "time": ts, "bar": t, "be_armed": False,
                                    }
                                    trades_today += 1
                            reset(st, "long")
                        # si sube una verde fuerte pero no rompe, mantenemos el retroceso

        # =================== SHORT (espejo) ===================
        if do_short and pos is None:
            st = st_short
            if not st["impulse"]:
                if not np.isnan(sl) and c[t] < sl and not np.isnan(sh):
                    st["impulse"] = True
                    st["module"] = sh
                    st["greens"] = 0
                    st["rhigh"] = -np.inf
                    st["exline"] = np.nan
            else:
                if h[t] > st["module"]:
                    reset(st, "short")
                else:
                    if bull:
                        st["greens"] += 1
                        st["exline"] = lo[t]
                        st["rhigh"] = max(st["rhigh"], h[t])
                    elif st["greens"] >= p.min_retrace_candles and not np.isnan(st["exline"]):
                        if lo[t] < st["exline"]:
                            if can_enter(ts):
                                entry = st["exline"] - slip
                                sl_px = st["rhigh"] + buf   # SL en la zona de riesgo previa (Cris)
                                risk = sl_px - entry
                                if risk > 0:
                                    pos = {
                                        "dir": -1, "entry": entry, "sl": sl_px,
                                        "tp": entry - p.tp_ratio * risk, "risk": risk,
                                        "time": ts, "bar": t, "be_armed": False,
                                    }
                                    trades_today += 1
                            reset(st, "short")

    return summarize(p, df, pd.DataFrame(trades))


def summarize(p: Params, df: pd.DataFrame, tr: pd.DataFrame) -> dict:
    res = {"params": asdict(p), "bars": len(df),
           "from": str(df.index.min()), "to": str(df.index.max())}
    if tr.empty:
        res["n_trades"] = 0
        print_report(res, tr)
        return res
    tr = tr.sort_values("exit_time").reset_index(drop=True)
    tr["equity"] = tr["pnl"].cumsum()
    wins = tr[tr["pnl"] > 0]
    losses = tr[tr["pnl"] <= 0]
    gross_win = wins["pnl"].sum()
    gross_loss = -losses["pnl"].sum()
    peak = tr["equity"].cummax()
    dd = (tr["equity"] - peak)
    res.update({
        "n_trades": len(tr),
        "n_long": int((tr["dir"] == 1).sum()),
        "n_short": int((tr["dir"] == -1).sum()),
        "win_rate": round(len(wins) / len(tr) * 100, 1),
        "net_pnl_usd": round(tr["pnl"].sum(), 2),
        "net_points": round(tr["points"].sum(), 2),
        "avg_trade_usd": round(tr["pnl"].mean(), 2),
        "avg_win_usd": round(wins["pnl"].mean(), 2) if len(wins) else 0.0,
        "avg_loss_usd": round(losses["pnl"].mean(), 2) if len(losses) else 0.0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else float("inf"),
        "max_drawdown_usd": round(dd.min(), 2),
        "expectancy_usd": round(tr["pnl"].mean(), 2),
        "tp_hits": int((tr["reason"] == "TP").sum()),
        "sl_hits": int((tr["reason"] == "SL").sum()),
    })
    tag = f"{p.symbol}_{p.tf}_{p.direction}_tp{p.tp_ratio}_sw{p.swing}"
    tr.to_csv(OUT / f"trades_{tag}.csv", index=False)
    _plot(tr, OUT / f"equity_{tag}.png", tag)
    res["trades_csv"] = str(OUT / f"trades_{tag}.csv")
    res["equity_png"] = str(OUT / f"equity_{tag}.png")
    print_report(res, tr)
    return res


def _plot(tr: pd.DataFrame, path: Path, tag: str) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(11, 5))
        ax.plot(tr["exit_time"], tr["equity"], lw=1.5)
        ax.axhline(0, color="grey", lw=0.8, ls="--")
        ax.set_title(f"Equity (MNQ, 1 contrato) — {tag}")
        ax.set_ylabel("PnL acumulado (USD)")
        ax.grid(alpha=0.3)
        fig.autofmt_xdate()
        fig.tight_layout()
        fig.savefig(path, dpi=110)
        plt.close(fig)
    except Exception as e:  # noqa: BLE001
        print(f"  (no se pudo graficar: {e})")


def print_report(res: dict, tr: pd.DataFrame) -> None:
    p = res["params"]
    print("=" * 64)
    print(f"AGOTAMIENTOS  {p['symbol']} {p['tf']}  dir={p['direction']}  "
          f"TP={p['tp_ratio']}R  swing={p['swing']}  retrace>={p['min_retrace_candles']}  "
          f"session={p['session']}")
    print(f"Periodo: {res['from']}  ->  {res['to']}  ({res['bars']} velas)")
    print("-" * 64)
    if res.get("n_trades", 0) == 0:
        print("Sin operaciones con estos parámetros.")
        print("=" * 64)
        return
    print(f"Trades: {res['n_trades']}  (long {res['n_long']} / short {res['n_short']})")
    print(f"Win rate: {res['win_rate']}%   TP/SL: {res['tp_hits']}/{res['sl_hits']}")
    print(f"PnL neto: ${res['net_pnl_usd']}  ({res['net_points']} pts)  [MNQ 1 contrato, con comis+slippage]")
    print(f"Profit factor: {res['profit_factor']}   Expectancy/trade: ${res['expectancy_usd']}")
    print(f"Avg win: ${res['avg_win_usd']}   Avg loss: ${res['avg_loss_usd']}")
    print(f"Max drawdown: ${res['max_drawdown_usd']}")
    print("=" * 64)


def build_parser() -> argparse.ArgumentParser:
    d = Params()
    ap = argparse.ArgumentParser(description="Backtest estrategia Agotamientos")
    ap.add_argument("--symbol", default=d.symbol)
    ap.add_argument("--tf", default=d.tf)
    ap.add_argument("--swing", type=int, default=d.swing)
    ap.add_argument("--min-retrace-candles", type=int, default=d.min_retrace_candles)
    ap.add_argument("--tp-ratio", type=float, default=d.tp_ratio)
    ap.add_argument("--sl-buffer-ticks", type=float, default=d.sl_buffer_ticks)
    ap.add_argument("--point-value", type=float, default=d.point_value)
    ap.add_argument("--commission-rt", type=float, default=d.commission_rt)
    ap.add_argument("--slippage-ticks", type=float, default=d.slippage_ticks)
    ap.add_argument("--direction", default=d.direction, choices=["long", "short", "both"])
    ap.add_argument("--no-session", action="store_true", help="no limitar a sesión NY")
    ap.add_argument("--sess-start", default=d.sess_start)
    ap.add_argument("--sess-end", default=d.sess_end)
    ap.add_argument("--max-hold-bars", type=int, default=d.max_hold_bars)
    ap.add_argument("--max-trades-per-day", type=int, default=d.max_trades_per_day)
    ap.add_argument("--stop-after-first-loss", action="store_true")
    ap.add_argument("--breakeven-after-r", type=float, default=d.breakeven_after_r)
    ap.add_argument("--force-close", default=d.force_close, help='cierre EOD "HH:MM" ET')
    return ap


if __name__ == "__main__":
    args = build_parser().parse_args()
    p = Params(
        symbol=args.symbol, tf=args.tf, swing=args.swing,
        min_retrace_candles=args.min_retrace_candles, tp_ratio=args.tp_ratio,
        sl_buffer_ticks=args.sl_buffer_ticks, point_value=args.point_value,
        commission_rt=args.commission_rt, slippage_ticks=args.slippage_ticks,
        direction=args.direction, session=not args.no_session,
        sess_start=args.sess_start, sess_end=args.sess_end, max_hold_bars=args.max_hold_bars,
        max_trades_per_day=args.max_trades_per_day,
        stop_after_first_loss=args.stop_after_first_loss,
        breakeven_after_r=args.breakeven_after_r, force_close=args.force_close,
    )
    run(p)
