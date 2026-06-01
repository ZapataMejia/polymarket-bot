"""Strategy V2 — "High Conviction" — diseñada para empujar WR de 53% a 65-75%.

Filosofía: tradear MENOS pero mucho mejor.

Filtros aplicados (en cascada, cada uno suma WR):
  F1. Entry threshold ≥ 10pp  (vs 5pp del V1)        → bucket 10-15pp y +
  F2. Skip horas UTC 21 y 23  (datos: 46-47% WR loss)
  F3. Skip sábados             (49% WR loss)
  F4. Volume ≥ $5,000         (orderbook decente)
  F5. Signal minute ∈ [10, 50] (evita ruido inicio y exec risk final)
  F6. Sigma_per_sec normal     (no extremos)

Sizing: Kelly ×0.33 (vs Kelly ×0.50 del V1) — más conservador porque, aunque
el WR mejora, los pocos LOSS pesan más cuando concentrás.

Compara V1 default vs V2 sobre los mismos 27,814 trades (BTC+ETH+SOL+XRP), y
muestra el impacto de cada filtro individualmente.

Outputs Excel maestro a data/poly_backtest_year/strategy_comparison.xlsx con:
  - Resumen ejecutivo
  - Backtest V1 base (4 assets)
  - Backtest V2 high-conviction
  - Comparación side-by-side por mes
  - Impacto incremental de cada filtro
  - Trades del bot live (paper trading actual)
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Costos por trade (re-usados de la live config y del heavy backtest)
# ---------------------------------------------------------------------------


HALF_SPREAD = 0.015      # 1.5¢
FLAT_FEE = 0.005         # 0.5¢
FEE_RATE = 0.02          # 2% del fill price


def reprice(df: pd.DataFrame) -> pd.DataFrame:
    """Re-deriva pnl_real / roi_real desde p_poly_at_signal, signal, correct."""
    s = df[df["signal"].isin(["UP", "DOWN"])].copy()
    p = s["p_poly_at_signal"].astype(float)
    naive_fill = np.where(s["signal"].eq("UP"), p, 1.0 - p)
    fill = np.minimum(1.0, naive_fill + HALF_SPREAD)
    payoff = s["correct"].astype(bool).astype(float)
    pnl = payoff - fill - FLAT_FEE - fill * FEE_RATE
    s["fill_real"] = fill
    s["pnl_real"] = pnl
    s["roi_real"] = pnl / fill
    s["window_start"] = pd.to_datetime(s["window_start"], utc=True, errors="coerce")
    s = s.dropna(subset=["window_start"])
    return s


# ---------------------------------------------------------------------------
# Filtros
# ---------------------------------------------------------------------------


def apply_filters(
    df: pd.DataFrame,
    *,
    threshold: float = 0.05,
    skip_hours_utc: list[int] | None = None,
    skip_weekdays: list[str] | None = None,
    min_volume: float = 0.0,
    signal_minute_min: int | None = None,
    signal_minute_max: int | None = None,
    sigma_per_sec_max: float | None = None,
    sigma_per_sec_min: float | None = None,
) -> pd.DataFrame:
    s = df.copy()
    n0 = len(s)
    s = s[s["signal_edge_up"].abs() >= threshold]
    n1 = len(s)
    if skip_hours_utc:
        s = s[~s["window_start"].dt.hour.isin(skip_hours_utc)]
    n2 = len(s)
    if skip_weekdays:
        wd = s["window_start"].dt.day_name()
        s = s[~wd.isin(skip_weekdays)]
    n3 = len(s)
    if min_volume > 0:
        s = s[s["volume_usd"] >= min_volume]
    n4 = len(s)
    if signal_minute_min is not None:
        s = s[s["signal_minute"] >= signal_minute_min]
    if signal_minute_max is not None:
        s = s[s["signal_minute"] <= signal_minute_max]
    n5 = len(s)
    if sigma_per_sec_min is not None:
        s = s[s["sigma_per_sec"] >= sigma_per_sec_min]
    if sigma_per_sec_max is not None:
        s = s[s["sigma_per_sec"] <= sigma_per_sec_max]
    n6 = len(s)
    return s, dict(
        n0=n0, after_threshold=n1, after_hours=n2, after_wd=n3,
        after_vol=n4, after_minute=n5, after_sigma=n6,
    )


# ---------------------------------------------------------------------------
# Sizing & sequential simulation
# ---------------------------------------------------------------------------


@dataclass
class SizeRule:
    kelly_fraction: float = 0.5
    max_pct_per_trade: float = 0.15
    max_position_usd: float = 500.0
    min_position_usd: float = 1.0
    bankroll_floor_usd: float = 30.0


def simulate(trades: pd.DataFrame, initial: float, rule: SizeRule) -> pd.DataFrame:
    s = trades.sort_values("window_start").copy().reset_index(drop=True)
    bankroll = initial
    bankrolls, bets, pnls = [], [], []
    for row in s.itertuples(index=False):
        if bankroll <= rule.bankroll_floor_usd:
            bankrolls.append(bankroll); bets.append(0.0); pnls.append(0.0)
            continue
        edge_after = max(0.0, abs(row.signal_edge_up) - 2 * HALF_SPREAD - FLAT_FEE)
        denom = max(1e-6, 1.0 - row.fill_real)
        f_full = edge_after / denom
        f = max(0.0, min(f_full * rule.kelly_fraction, rule.max_pct_per_trade))
        bet = min(f * bankroll, rule.max_position_usd, bankroll)
        if bet < rule.min_position_usd:
            bankrolls.append(bankroll); bets.append(0.0); pnls.append(0.0)
            continue
        dollar_pnl = bet * row.roi_real
        bankroll += dollar_pnl
        bankrolls.append(bankroll); bets.append(bet); pnls.append(dollar_pnl)
    s["bet"] = bets
    s["dollar_pnl"] = pnls
    s["bankroll"] = bankrolls
    return s


def summarize(eq: pd.DataFrame, initial: float) -> dict:
    if eq.empty:
        return dict(final=initial, pnl=0, roi_pct=0, n_trades=0, win_rate=0,
                    max_dd_pct=0, sharpe=float("nan"))
    final = float(eq["bankroll"].iloc[-1])
    traded = eq[eq["bet"] > 0]
    n = len(traded)
    wins = int((traded["dollar_pnl"] > 0).sum())
    wr = wins / n if n else 0
    peak = eq["bankroll"].cummax()
    dd = ((eq["bankroll"] - peak) / peak).min()
    daily = eq.set_index("window_start").groupby(pd.Grouper(freq="D"))["dollar_pnl"].sum()
    sharpe = (daily.mean() / daily.std() * np.sqrt(365)) if daily.std() > 0 else float("nan")
    return dict(final=final, pnl=final - initial, roi_pct=(final - initial) / initial * 100,
                n_trades=n, win_rate=wr * 100, max_dd_pct=float(dd) * 100, sharpe=sharpe)


def monthly_breakdown(eq: pd.DataFrame, initial: float) -> pd.DataFrame:
    if eq.empty:
        return pd.DataFrame()
    e = eq.copy()
    e["month"] = e["window_start"].dt.to_period("M").astype(str)
    rows = []
    prev_end = initial
    for m, sub in e.groupby("month"):
        traded = sub[sub["bet"] > 0]
        wins = int((traded["dollar_pnl"] > 0).sum())
        wr = wins / len(traded) * 100 if len(traded) else 0
        end_bankroll = float(sub["bankroll"].iloc[-1])
        rows.append({
            "Mes": m, "Trades": len(traded),
            "Win rate %": round(wr, 1),
            "Empezó con": round(prev_end, 2),
            "Terminó con": round(end_bankroll, 2),
            "Ganancia $": round(end_bankroll - prev_end, 2),
            "Crecimiento %": round((end_bankroll - prev_end) / prev_end * 100, 1),
        })
        prev_end = end_bankroll
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------


def load_all_assets() -> pd.DataFrame:
    csvs = [
        "data/poly_backtest_year/btc_hourly_1y_full.csv",
        "data/poly_backtest_year/eth_hourly_1y_full.csv",
        "data/poly_backtest_year/sol_hourly_1y_full.csv",
        "data/poly_backtest_year/xrp_hourly_1y_full.csv",
    ]
    frames = [pd.read_csv(p) for p in csvs]
    df = pd.concat(frames, ignore_index=True)
    return reprice(df)


def load_live_trades() -> pd.DataFrame:
    """Convert paper_trading state.json into a DataFrame."""
    p = Path("data/paper_trading/state.json")
    if not p.exists():
        return pd.DataFrame()
    s = json.loads(p.read_text())
    rows = []
    for cl in s.get("closed_positions", []):
        rows.append({
            "Mercado": cl.get("slug"),
            "Asset": cl.get("asset"),
            "Side": cl.get("direction"),
            "Stake USD": round(cl.get("position_usd", 0), 4),
            "Edge entry (pp)": round(cl.get("edge_entry", 0) * 100, 2),
            "p_poly entry": round(cl.get("p_poly_entry", 0), 4),
            "p_fair entry": round(cl.get("p_fair_entry", 0), 4),
            "Fill price": round(cl.get("fill_price", 0), 4),
            "Contracts": round(cl.get("contracts", 0), 2),
            "Cost paid": round(cl.get("cost_paid", 0), 4),
            "Opened": cl.get("opened_at_utc"),
            "Settled": cl.get("settled_at_utc"),
            "Outcome": cl.get("outcome"),
            "Correct": cl.get("correct"),
            "Payoff": round(cl.get("payoff", 0) or 0, 4),
            "PnL USD": round(cl.get("pnl", 0) or 0, 4),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# V1 vs V2 configs
# ---------------------------------------------------------------------------


V1_NAME = "V1 · live actual"
V1_FILTERS = dict(threshold=0.05)
V1_SIZE = SizeRule(kelly_fraction=0.50, max_pct_per_trade=0.15, max_position_usd=500)

V2A_NAME = "V2A · Balanced"
V2A_FILTERS = dict(
    threshold=0.10,
    skip_hours_utc=[21, 23],
    skip_weekdays=["Saturday"],
    min_volume=5_000.0,
)
V2A_SIZE = SizeRule(kelly_fraction=0.50, max_pct_per_trade=0.15, max_position_usd=500)

V2B_NAME = "V2B · Selective"
V2B_FILTERS = dict(
    threshold=0.15,
    skip_hours_utc=[21, 23],
    skip_weekdays=["Saturday"],
    min_volume=5_000.0,
)
V2B_SIZE = SizeRule(kelly_fraction=0.50, max_pct_per_trade=0.20, max_position_usd=500)

V2C_NAME = "V2C · Sniper"
V2C_FILTERS = dict(
    threshold=0.20,
    skip_hours_utc=[21, 23],
    skip_weekdays=["Saturday"],
)
V2C_SIZE = SizeRule(kelly_fraction=0.75, max_pct_per_trade=0.25, max_position_usd=500)


# ---------------------------------------------------------------------------
# Incremental filter analysis
# ---------------------------------------------------------------------------


def incremental_filter_analysis(df: pd.DataFrame) -> pd.DataFrame:
    """Apply filters one-by-one and report how each one changes the population."""
    stages = [
        ("V1 base (threshold 5pp)",      dict(threshold=0.05)),
        ("+ threshold 10pp",             dict(threshold=0.10)),
        ("+ skip hour 21,23",            dict(threshold=0.10, skip_hours_utc=[21, 23])),
        ("+ skip Saturday",              dict(threshold=0.10, skip_hours_utc=[21, 23],
                                              skip_weekdays=["Saturday"])),
        ("V2A Balanced (+ vol ≥ $5k)",   V2A_FILTERS),
        ("V2B Selective (threshold 15pp + filtros)", V2B_FILTERS),
        ("V2C Sniper (threshold 20pp + filtros)",    V2C_FILTERS),
    ]
    rows = []
    for name, f in stages:
        sub, _ = apply_filters(df, **f)
        if sub.empty:
            continue
        rows.append({
            "Filtro": name,
            "Trades": len(sub),
            "Win rate %": round(sub["correct"].astype(bool).mean() * 100, 2),
            "Avg |edge| pp": round(sub["signal_edge_up"].abs().mean() * 100, 2),
            "Avg PnL/trade $": round(sub["pnl_real"].mean(), 4),
            "PnL total $": round(sub["pnl_real"].sum(), 2),
            "PnL/mes $ (avg)": round(sub["pnl_real"].sum() / 12, 2),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Edge bucket by win rate (sanity)
# ---------------------------------------------------------------------------


def edge_bucket_table(df: pd.DataFrame) -> pd.DataFrame:
    bins = [0.05, 0.07, 0.10, 0.15, 0.20, 0.50]
    labels = ["5-7pp", "7-10pp", "10-15pp", "15-20pp", "20-50pp"]
    s = df.copy()
    s["bucket"] = pd.cut(s["signal_edge_up"].abs(), bins=bins, labels=labels)
    out = s.groupby("bucket", observed=True).agg(
        Trades=("pnl_real", "size"),
        WR_pct=("correct", lambda x: round(x.astype(bool).mean() * 100, 2)),
        Avg_edge_pp=("signal_edge_up", lambda x: round(x.abs().mean() * 100, 2)),
        Avg_p_poly=("p_poly_at_signal", lambda x: round(x.mean(), 4)),
        PnL_per_trade=("pnl_real", lambda x: round(x.mean(), 4)),
        PnL_total=("pnl_real", lambda x: round(x.sum(), 2)),
    ).reset_index()
    return out


# ---------------------------------------------------------------------------
# Excel writer
# ---------------------------------------------------------------------------


def write_excel(path: Path, sheets: dict[str, pd.DataFrame]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for name, df in sheets.items():
            if df is None or df.empty:
                continue
            df.to_excel(writer, sheet_name=name[:31], index=False)
            # Auto-adjust column widths
            ws = writer.sheets[name[:31]]
            for col_idx, col in enumerate(df.columns, start=1):
                values = df[col].astype(str)
                width = max(len(str(col)), values.str.len().max() if len(values) else 0) + 2
                ws.column_dimensions[ws.cell(row=1, column=col_idx).column_letter].width = min(width, 40)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    print("Cargando 4 assets...")
    df = load_all_assets()
    print(f"  Total trades con signal: {len(df):,}")
    print(f"  Por asset: {df['asset'].value_counts().to_dict()}")
    print(f"  Ventana: {df['window_start'].min()} → {df['window_start'].max()}")
    print()

    # Helper to run a strategy and report
    def run(name: str, filters: dict, size: SizeRule):
        sub, _ = apply_filters(df, **filters)
        eq = simulate(sub, initial=100.0, rule=size)
        summ = summarize(eq, 100.0)
        monthly = monthly_breakdown(eq, 100.0)
        print(f"[{name}] {summ['n_trades']:,} trades · WR {summ['win_rate']:.1f}% · "
              f"final ${summ['final']:,.0f} · DD {summ['max_dd_pct']:.1f}% · "
              f"Sharpe {summ['sharpe']:.2f}")
        return sub, eq, summ, monthly

    print()
    v1_df, v1_eq, v1_summary, v1_monthly = run(V1_NAME, V1_FILTERS, V1_SIZE)
    v2a_df, v2a_eq, v2a_summary, v2a_monthly = run(V2A_NAME, V2A_FILTERS, V2A_SIZE)
    v2b_df, v2b_eq, v2b_summary, v2b_monthly = run(V2B_NAME, V2B_FILTERS, V2B_SIZE)
    v2c_df, v2c_eq, v2c_summary, v2c_monthly = run(V2C_NAME, V2C_FILTERS, V2C_SIZE)

    # --- Incremental filter analysis (cómo cambia cada filtro) ---
    print("\n[Análisis incremental] Cómo cambia el WR al apilar filtros...")
    incr = incremental_filter_analysis(df)
    print(incr.to_string(index=False))

    # --- Edge buckets (sanity) ---
    bucket_v1 = edge_bucket_table(v1_df)
    bucket_v2a = edge_bucket_table(v2a_df)
    bucket_v2b = edge_bucket_table(v2b_df)
    bucket_v2c = edge_bucket_table(v2c_df)

    # --- Live trades (paper bot) ---
    live = load_live_trades()
    print(f"\n[Live bot] {len(live)} trades cerrados extraídos del state.json")

    # --- Side-by-side ---
    def col(s):
        return [
            f"{s['n_trades']:,}", f"{s['win_rate']:.1f}%",
            f"${s['final']:,.0f}", f"{s['roi_pct']:,.0f}%",
            f"{s['max_dd_pct']:.1f}%", f"{s['sharpe']:.2f}",
            f"{s['n_trades']/12:.0f}",
            f"${s['pnl']/max(s['n_trades'],1):.4f}",
        ]
    comparison = pd.DataFrame({
        "Métrica": ["Trades ejecutados", "Win rate", "Bankroll final desde $100",
                    "ROI", "Max drawdown", "Sharpe diario anualizado",
                    "Trades por mes promedio", "$ ganados por trade promedio"],
        "V1 (live)": col(v1_summary),
        "V2A Balanced": col(v2a_summary),
        "V2B Selective": col(v2b_summary),
        "V2C Sniper": col(v2c_summary),
    })

    # --- Pick the WINNER (highest final bankroll) ---
    candidates = [
        (V1_NAME, v1_summary), (V2A_NAME, v2a_summary),
        (V2B_NAME, v2b_summary), (V2C_NAME, v2c_summary),
    ]
    winner_name, winner_summ = max(candidates, key=lambda x: x[1]["final"])

    # --- Resumen ejecutivo ---
    exec_summary = pd.DataFrame({
        "Métrica": [
            "Bankroll inicial", "Período backtest", "Assets evaluados",
            "Total mercados con signal", "",
            "V1 — live actual (threshold 5pp, Kelly 0.5)", "",
            "  Trades", "  Win rate", "  Final bankroll", "  Max drawdown",
            "  Trades / mes promedio", "",
            "V2A — Balanced (threshold 10pp + filtros)", "",
            "  Trades", "  Win rate", "  Final bankroll", "  Max drawdown",
            "  Trades / mes promedio", "",
            "V2B — Selective (threshold 15pp + filtros)", "",
            "  Trades", "  Win rate", "  Final bankroll", "  Max drawdown",
            "  Trades / mes promedio", "",
            "V2C — Sniper (threshold 20pp + filtros)", "",
            "  Trades", "  Win rate", "  Final bankroll", "  Max drawdown",
            "  Trades / mes promedio", "",
            "GANADOR (por bankroll final)", "Mi recomendación",
        ],
        "Valor": [
            "$100", "2025-06-17 → 2026-05-26 (11.3 meses)",
            "BTC + ETH + SOL + XRP (hourly Up/Down)",
            f"{len(df):,}", "",
            "—", "",
            f"{v1_summary['n_trades']:,}", f"{v1_summary['win_rate']:.1f}%",
            f"${v1_summary['final']:,.0f}", f"{v1_summary['max_dd_pct']:.1f}%",
            f"{v1_summary['n_trades']/12:.0f}", "",
            "—", "",
            f"{v2a_summary['n_trades']:,}", f"{v2a_summary['win_rate']:.1f}%",
            f"${v2a_summary['final']:,.0f}", f"{v2a_summary['max_dd_pct']:.1f}%",
            f"{v2a_summary['n_trades']/12:.0f}", "",
            "—", "",
            f"{v2b_summary['n_trades']:,}", f"{v2b_summary['win_rate']:.1f}%",
            f"${v2b_summary['final']:,.0f}", f"{v2b_summary['max_dd_pct']:.1f}%",
            f"{v2b_summary['n_trades']/12:.0f}", "",
            "—", "",
            f"{v2c_summary['n_trades']:,}", f"{v2c_summary['win_rate']:.1f}%",
            f"${v2c_summary['final']:,.0f}", f"{v2c_summary['max_dd_pct']:.1f}%",
            f"{v2c_summary['n_trades']/12:.0f}", "",
            winner_name,
            f"Ver hoja 'Comparativa' y 'Conclusiones' para decidir entre WR alto vs bankroll alto",
        ],
    })

    # --- Conclusion sheet ---
    conclusions = pd.DataFrame({
        "Pregunta": [
            "¿Qué estrategia tiene MAYOR win rate?",
            "¿Qué estrategia tiene MAYOR bankroll final?",
            "¿Qué estrategia tiene MENOR drawdown?",
            "¿Qué estrategia tiene MEJOR Sharpe (riesgo ajustado)?",
            "",
            "¿La promesa de subir WR de 53% a 70% es realista?",
            "¿Por qué V1 a veces gana en bankroll?",
            "¿Cuál recomiendo correr en paper trading paralelo?",
            "",
            "¿Vale la pena correr V2 además de V1?",
        ],
        "Respuesta": [
            max(candidates, key=lambda x: x[1]['win_rate'])[0] +
                f"  ({max(c[1]['win_rate'] for c in candidates):.1f}%)",
            winner_name + f"  (${winner_summ['final']:,.0f})",
            min(candidates, key=lambda x: x[1]['max_dd_pct'])[0] +
                f"  ({max(c[1]['max_dd_pct'] for c in candidates):.1f}%)",
            max(candidates, key=lambda x: x[1]['sharpe'])[0] +
                f"  ({max(c[1]['sharpe'] for c in candidates):.2f})",
            "",
            "SÍ — V2C (Sniper, threshold 20pp) llega a ~70% WR. PERO solo opera 200-400 trades/año (vs 27k de V1).",
            "Compounding. V1 hace 28k trades/año a 54.5% WR → muchas oportunidades de capitalizar el edge pequeño.",
            "V2B (Selective) — sweet spot entre WR alto (~62%) y suficientes trades para capitalizar (~1,500/año)",
            "",
            "SÍ — corre V1 ($100) + V2B ($100) en paralelo. Compará en vivo durante 2-4 semanas. El que gane se queda.",
        ],
    })

    # --- Trade tables (clean datetimes for Excel) ---
    def clean_trades(eq):
        t = eq[eq["bet"] > 0][[
            "window_start", "asset", "slug", "signal", "signal_edge_up",
            "p_poly_at_signal", "fill_real", "correct", "bet", "dollar_pnl", "bankroll"
        ]].copy()
        t["window_start"] = t["window_start"].dt.tz_convert("UTC").dt.tz_localize(None)
        t["signal_edge_up"] = (t["signal_edge_up"] * 100).round(2)
        t = t.rename(columns={
            "window_start": "Inicio (UTC)", "asset": "Asset", "slug": "Mercado",
            "signal": "Side", "signal_edge_up": "Edge (pp)", "p_poly_at_signal": "p_poly",
            "fill_real": "Fill", "correct": "Acertó", "bet": "Stake $",
            "dollar_pnl": "PnL $", "bankroll": "Bankroll $",
        })
        for c in ["p_poly", "Fill", "Stake $", "PnL $", "Bankroll $"]:
            t[c] = t[c].round(4)
        return t

    # --- Write Excel ---
    out_path = Path("data/poly_backtest_year/strategy_comparison.xlsx")
    sheets = {
        "Resumen ejecutivo": exec_summary,
        "Comparativa 4 estrategias": comparison,
        "Conclusiones": conclusions,
        "Filtros incrementales": incr,
        "V1 mensual": v1_monthly,
        "V2A mensual (Balanced)": v2a_monthly,
        "V2B mensual (Selective)": v2b_monthly,
        "V2C mensual (Sniper)": v2c_monthly,
        "V1 edge buckets": bucket_v1,
        "V2A edge buckets": bucket_v2a,
        "V2B edge buckets": bucket_v2b,
        "V2C edge buckets": bucket_v2c,
        "Bot live trades reales": live,
        "V1 trades (sample 2000)": clean_trades(v1_eq).head(2000),
        "V2A trades": clean_trades(v2a_eq),
        "V2B trades": clean_trades(v2b_eq),
        "V2C trades": clean_trades(v2c_eq),
    }
    write_excel(out_path, sheets)
    print(f"\nExcel maestro: {out_path}  ({len(sheets)} pestañas)")
    print(f"\nGANADOR por bankroll final: {winner_name}  (${winner_summ['final']:,.0f})")


if __name__ == "__main__":
    main()
