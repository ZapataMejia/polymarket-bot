"""Honest 1-month projection for V1 with $500 real money.

Pipeline:
  1. Load 4-asset year of trades (27,773).
  2. Slice the FIRST MONTH (~30 days from earliest trade).
  3. Apply 4 friction scenarios:
       - Optimistic   : backtest fill (1.5c spread, 2% fee) — best case
       - Base         : 2.0c spread + 2% fee + 5% random execution misses
       - Pessimistic  : 2.5c spread + 2.5% fee + 10% misses + edge -15%
       - Worst        : 3.0c spread + 3% fee + 20% misses + edge -25%
  4. Bootstrap 2000 alternate realities per scenario (resample WITH replacement,
     preserve chronology so compounding behaves correctly).
  5. Account for one-time entry costs ($20: USDC bridge + KYC + initial slippage).
  6. Show P5/P25/P50/P75/P95 of final bankroll + drawdown distribution.

Output: data/poly_backtest_year/projection_1month_500usd.csv
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


# ---------------------------------------------------------------------------
# Friction scenarios
# ---------------------------------------------------------------------------


@dataclass
class Scenario:
    name: str
    half_spread: float        # cost crossed on each fill (in $ per $1 notional)
    fee_rate: float           # proportional taker fee
    flat_fee: float           # gas / overhead per trade
    miss_rate: float          # fraction of trades silently skipped (downtime / network)
    edge_haircut: float       # apply to signal magnitude before deciding entry & sizing
    entry_cost: float         # one-time fee for setting up real money

OPTIMISTIC = Scenario("Optimista",   half_spread=0.015, fee_rate=0.020, flat_fee=0.005, miss_rate=0.00, edge_haircut=0.00, entry_cost=10.0)
BASE       = Scenario("Base",        half_spread=0.020, fee_rate=0.020, flat_fee=0.005, miss_rate=0.05, edge_haircut=0.10, entry_cost=20.0)
PESSIMISTIC= Scenario("Pesimista",   half_spread=0.025, fee_rate=0.025, flat_fee=0.005, miss_rate=0.10, edge_haircut=0.15, entry_cost=25.0)
WORST      = Scenario("Mala suerte", half_spread=0.030, fee_rate=0.030, flat_fee=0.005, miss_rate=0.20, edge_haircut=0.25, entry_cost=30.0)


# ---------------------------------------------------------------------------
# Sequential simulator
# ---------------------------------------------------------------------------


def reprice(df: pd.DataFrame, sc: Scenario, rng: np.random.Generator) -> pd.DataFrame:
    """Re-derive PnL using scenario costs. Apply miss_rate and edge_haircut."""
    s = df[df["signal"].isin(["UP", "DOWN"])].copy()
    p = s["p_poly_at_signal"].astype(float)
    naive_fill = np.where(s["signal"].eq("UP"), p, 1.0 - p)
    fill = np.minimum(1.0, naive_fill + sc.half_spread)
    payoff = s["correct"].astype(bool).astype(float)
    prop_fee = fill * sc.fee_rate
    pnl = payoff - fill - sc.flat_fee - prop_fee
    s["fill_real"] = fill
    s["pnl_real"] = pnl
    s["roi_real"] = pnl / fill
    s["window_start"] = pd.to_datetime(s["window_start"], utc=True)
    # Apply edge haircut (model overestimates the edge magnitude)
    s["effective_edge"] = s["signal_edge_up"].astype(float) * (1.0 - sc.edge_haircut)
    # Random miss (network down / restart / etc.)
    if sc.miss_rate > 0:
        miss = rng.uniform(0.0, 1.0, size=len(s)) < sc.miss_rate
        s = s.loc[~miss].copy()
    return s


def simulate(trades: pd.DataFrame, initial: float,
             kelly_fraction: float = 0.5,
             max_pct: float = 0.15,
             max_position_usd: float = 500.0,
             min_position_usd: float = 1.0,
             bankroll_floor: float = 30.0,
             max_concurrent: int = 4) -> dict:
    """Run sequential simulation, return summary stats."""
    s = trades.sort_values("window_start").reset_index(drop=True)
    bankroll = initial
    bankrolls = []
    for row in s.itertuples(index=False):
        if bankroll <= bankroll_floor:
            bankrolls.append(bankroll)
            continue
        edge_after = max(0.0, abs(row.effective_edge) - 2 * 0.020 - 0.005)
        denom = max(1e-6, 1.0 - row.fill_real)
        f_full = edge_after / denom
        f = max(0.0, min(f_full * kelly_fraction, max_pct))
        bet = min(f * bankroll, max_position_usd, bankroll)
        if bet < min_position_usd:
            bankrolls.append(bankroll)
            continue
        bankroll += bet * row.roi_real
        bankrolls.append(bankroll)
    arr = np.array(bankrolls)
    peak = np.maximum.accumulate(arr)
    dd = (arr / peak - 1.0).min() if len(arr) > 0 else 0.0
    return dict(
        final=float(bankroll),
        max_dd=float(dd) * 100.0,
        days=(s["window_start"].iloc[-1] - s["window_start"].iloc[0]).days if len(s) > 1 else 30,
    )


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------


def bootstrap_scenario(first_month: pd.DataFrame, sc: Scenario,
                       initial: float, n_boot: int = 2000) -> dict:
    rng = np.random.default_rng(seed=hash(sc.name) % (2**32))
    finals: list[float] = []
    drawdowns: list[float] = []
    n = len(first_month)
    for _ in range(n_boot):
        # Resample with replacement, preserve chronology.
        idx = rng.integers(0, n, size=n)
        sampled = first_month.iloc[idx].sort_values("window_start").reset_index(drop=True)
        priced = reprice(sampled, sc, rng)
        # Re-apply entry cost on the initial bankroll
        result = simulate(priced, initial - sc.entry_cost)
        finals.append(result["final"])
        drawdowns.append(result["max_dd"])
    f = np.array(finals)
    d = np.array(drawdowns)
    return dict(
        scenario=sc.name,
        p05=float(np.quantile(f, 0.05)),
        p25=float(np.quantile(f, 0.25)),
        p50=float(np.quantile(f, 0.50)),
        p75=float(np.quantile(f, 0.75)),
        p95=float(np.quantile(f, 0.95)),
        mean=float(f.mean()),
        prob_loss=float((f < initial).mean() * 100),
        prob_burned=float((f < bankroll_floor_threshold(initial)).mean() * 100),
        prob_2x=float((f >= 2 * initial).mean() * 100),
        prob_5x=float((f >= 5 * initial).mean() * 100),
        dd_median=float(np.quantile(d, 0.50)),
        dd_p95=float(np.quantile(d, 0.05)),  # worst 5% drawdown
    )


def bankroll_floor_threshold(initial: float) -> float:
    return initial * 0.1  # consider "burned" if below 10% of initial


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    INITIAL = 500.0
    DAYS = 30

    print("Cargando los 27,773 trades del último año...")
    csvs = [
        "data/poly_backtest_year/btc_hourly_1y_full.csv",
        "data/poly_backtest_year/eth_hourly_1y_full.csv",
        "data/poly_backtest_year/sol_hourly_1y_full.csv",
        "data/poly_backtest_year/xrp_hourly_1y_full.csv",
    ]
    df = pd.concat([pd.read_csv(p) for p in csvs], ignore_index=True)
    df = df[df["signal"].isin(["UP", "DOWN"])].copy()
    df["window_start"] = pd.to_datetime(df["window_start"], utc=True)
    df = df.dropna(subset=["window_start"]).sort_values("window_start")

    # Slice the first DAYS days
    start = df["window_start"].min()
    end_first_month = start + pd.Timedelta(days=DAYS)
    first_month = df[(df["window_start"] >= start) & (df["window_start"] < end_first_month)].copy()
    print(f"  Ventana del primer mes: {start.date()} → {end_first_month.date()}")
    print(f"  Trades en ese mes: {len(first_month):,}")
    print(f"  Por asset: {first_month['asset'].value_counts().to_dict()}")
    print()

    # Run bootstrap for each scenario
    rows = []
    for sc in [OPTIMISTIC, BASE, PESSIMISTIC, WORST]:
        print(f"[{sc.name}] spread={sc.half_spread*100:.1f}¢ fee={sc.fee_rate*100:.1f}% "
              f"misses={sc.miss_rate*100:.0f}% edge_haircut={sc.edge_haircut*100:.0f}% "
              f"entry_cost=${sc.entry_cost:.0f}")
        result = bootstrap_scenario(first_month, sc, INITIAL, n_boot=2000)
        rows.append(result)
        pnl_mid = result["p50"] - INITIAL
        print(f"  Median: ${result['p50']:,.0f}  (PnL ${pnl_mid:+,.0f})  ·  "
              f"prob de perder: {result['prob_loss']:.0f}%  ·  prob de doblar: {result['prob_2x']:.0f}%")

    out = pd.DataFrame(rows)
    out.to_csv("data/poly_backtest_year/projection_1month_500usd.csv", index=False)

    # ---- Pretty print summary ----
    print()
    print("=" * 100)
    print(f"  PROYECCIÓN 1 MES · $500 PLATA REAL · 2000 escenarios por configuración".center(100))
    print("=" * 100)
    print(f"{'Escenario':<14} {'P5':>10} {'P25':>10} {'P50 (medio)':>14} {'P75':>10} {'P95':>10}"
          f" {'Mean':>10} {'P(perder)':>10} {'P(2×)':>8} {'P(5×)':>8}")
    print("-" * 100)
    for r in rows:
        print(f"{r['scenario']:<14} ${r['p05']:>9,.0f} ${r['p25']:>9,.0f} ${r['p50']:>13,.0f}"
              f" ${r['p75']:>9,.0f} ${r['p95']:>9,.0f} ${r['mean']:>9,.0f}"
              f" {r['prob_loss']:>9.1f}% {r['prob_2x']:>7.1f}% {r['prob_5x']:>7.1f}%")


if __name__ == "__main__":
    main()
