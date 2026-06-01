"""Heavy backtest for the Polymarket Up/Down latency arb strategy.

Reads the per-market signal CSVs produced by `analyze_polymarket_edge.py`
(one row per market, with `p_poly_at_signal`, `signal`, `correct`, etc.)
and runs the following analyses on top:

  1. Cost sensitivity sweep  (half-spread × flat fee × proportional fee)
  2. Sizing sweep            (fixed $ vs Kelly fractions × position cap)
  3. Walk-forward            (rolling train/test by month)
  4. Bootstrap PnL           (1000 resamples → 95% confidence interval)
  5. Time-of-day analysis    (which UTC hours have most edge)
  6. Edge bucket analysis    (does the edge survive after costs at every magnitude?)

It re-derives the realized PnL per trade at the chosen cost params from the raw
`p_poly_at_signal`, `signal`, and `correct` columns, so we can sweep costs
without re-fetching anything.

Usage:
    python scripts/heavy_backtest.py \
        --csvs data/poly_backtest_year/btc_hourly_1y_full.csv \
               data/poly_backtest_year/eth_hourly_1y_full.csv \
        --outdir data/poly_backtest_year/heavy_btc_eth

When SOL/XRP fetches finish, just add their CSVs to --csvs and re-run.
"""
from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Trade re-pricing at arbitrary cost params.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Costs:
    """Execution costs in dollars (per $1 contract notional)."""

    half_spread: float = 0.015   # 1.5¢
    flat_fee: float = 0.005      # 0.5¢ gas/relayer per fill
    fee_rate: float = 0.02       # 2% proportional taker fee on fill price

    @property
    def label(self) -> str:
        return f"hs={self.half_spread*100:.1f}¢ ff={self.flat_fee*100:.1f}¢ rate={self.fee_rate*100:.1f}%"


def reprice(df: pd.DataFrame, c: Costs) -> pd.DataFrame:
    """Recompute pnl_realistic per row at the given cost params.

    The CSV stores p_poly_at_signal (Up-token mid), signal (UP/DOWN), correct (bool).
    Naive fill = p_poly if UP else 1 - p_poly.  Effective fill = naive_fill + half_spread.
    Payoff = 1 if correct else 0.  PnL_real = payoff - fill - flat_fee - fill*fee_rate.
    """
    s = df[df["signal"].isin(["UP", "DOWN"])].copy()
    p = s["p_poly_at_signal"].astype(float)
    naive_fill = np.where(s["signal"].eq("UP"), p, 1.0 - p)
    fill = np.minimum(1.0, naive_fill + c.half_spread)
    payoff = s["correct"].astype(bool).astype(float)
    prop_fee = fill * c.fee_rate
    pnl = payoff - fill - c.flat_fee - prop_fee
    s = s.assign(
        naive_fill=naive_fill,
        fill_real=fill,
        pnl_real=pnl,
        roi_real=pnl / fill,
    )
    return s


# ---------------------------------------------------------------------------
# Sequential bankroll simulator with Kelly + caps + bankroll floor.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SizeRule:
    mode: str                       # "fixed" or "kelly"
    fixed_usd: float = 1.0          # used when mode == "fixed"
    kelly_fraction: float = 0.5     # fraction of full Kelly to use
    max_pct_per_trade: float = 0.15
    max_position_usd: float = 500.0  # realistic Polymarket depth cap
    min_position_usd: float = 1.0
    bankroll_floor_usd: float = 30.0

    @property
    def label(self) -> str:
        if self.mode == "fixed":
            return f"fixed ${self.fixed_usd:.0f}"
        return (
            f"kelly×{self.kelly_fraction:.2f} cap{self.max_pct_per_trade*100:.0f}% "
            f"max${self.max_position_usd:.0f}"
        )


def simulate_sequential(
    trades: pd.DataFrame,
    initial: float,
    size_rule: SizeRule,
) -> pd.DataFrame:
    """Walk trades chronologically and grow / shrink bankroll.

    Expects columns: window_start (sortable), signal_edge_up, fill_real, pnl_real, correct.
    `pnl_real` is per $1 contract notional, so for a stake of `bet` dollars at fill_real
    the actual dollar PnL is `bet * (pnl_real / fill_real)`  (since pnl_real already
    represents what one contract returns).
    Equivalently: dollar_pnl = bet * roi_real.
    """
    s = trades.sort_values("window_start").copy()
    bankroll = initial
    bankrolls = []
    bets = []
    pnls = []
    skipped = 0
    for row in s.itertuples(index=False):
        if bankroll <= size_rule.bankroll_floor_usd:
            bankrolls.append(bankroll)
            bets.append(0.0)
            pnls.append(0.0)
            skipped += 1
            continue
        if size_rule.mode == "fixed":
            bet = min(size_rule.fixed_usd, bankroll)
        else:
            edge_after = max(0.0, abs(row.signal_edge_up) - 2 * 0.015 - 0.005)
            denom = max(1e-6, 1.0 - row.fill_real)
            f_full = edge_after / denom
            f = max(0.0, min(f_full * size_rule.kelly_fraction, size_rule.max_pct_per_trade))
            bet = f * bankroll
            bet = min(bet, size_rule.max_position_usd, bankroll)
            bet = max(0.0, bet)
            if bet < size_rule.min_position_usd:
                bankrolls.append(bankroll)
                bets.append(0.0)
                pnls.append(0.0)
                skipped += 1
                continue
        dollar_pnl = bet * row.roi_real
        bankroll += dollar_pnl
        bankrolls.append(bankroll)
        bets.append(bet)
        pnls.append(dollar_pnl)
    s["bet"] = bets
    s["dollar_pnl"] = pnls
    s["bankroll"] = bankrolls
    return s


def summarize_run(eq: pd.DataFrame, initial: float) -> dict:
    """Return summary stats on an equity trajectory."""
    if eq.empty:
        return {}
    final = float(eq["bankroll"].iloc[-1])
    pnl = final - initial
    traded = eq[eq["bet"] > 0]
    n = len(traded)
    wins = int((traded["dollar_pnl"] > 0).sum())
    win_rate = wins / n if n else 0.0
    peak = eq["bankroll"].cummax()
    dd = ((eq["bankroll"] - peak) / peak).min()
    daily = eq.set_index("window_start").groupby(pd.Grouper(freq="D"))["dollar_pnl"].sum()
    ann_sharpe = (daily.mean() / daily.std() * math.sqrt(365)) if daily.std() > 0 else float("nan")
    return {
        "final_bankroll": final,
        "pnl": pnl,
        "roi_pct": pnl / initial * 100.0,
        "n_trades": n,
        "win_rate": win_rate,
        "max_drawdown_pct": float(dd) * 100.0,
        "sharpe_daily": ann_sharpe,
    }


# ---------------------------------------------------------------------------
# Analyses.
# ---------------------------------------------------------------------------


def cost_sweep(df: pd.DataFrame, threshold: float, initial: float, outdir: Path) -> None:
    """Re-price PnL at a grid of cost configs and report final bankroll under fixed sizing."""
    half_spreads = [0.005, 0.010, 0.015, 0.020, 0.025, 0.030, 0.040, 0.050]
    flat_fees = [0.000, 0.003, 0.005, 0.010]
    fee_rates = [0.00, 0.01, 0.02, 0.03, 0.04]
    rows = []
    for hs in half_spreads:
        for ff in flat_fees:
            for fr in fee_rates:
                c = Costs(half_spread=hs, flat_fee=ff, fee_rate=fr)
                priced = reprice(df, c)
                priced = priced[priced["signal_edge_up"].abs() >= threshold]
                if priced.empty:
                    continue
                priced["window_start"] = pd.to_datetime(priced["window_start"], utc=True)
                rule = SizeRule(mode="kelly", kelly_fraction=0.5,
                                max_pct_per_trade=0.15, max_position_usd=500.0)
                eq = simulate_sequential(priced, initial, rule)
                summ = summarize_run(eq, initial)
                rows.append({
                    "half_spread_cents": hs * 100,
                    "flat_fee_cents": ff * 100,
                    "fee_rate_pct": fr * 100,
                    **summ,
                })
    out = pd.DataFrame(rows)
    out.to_csv(outdir / "cost_sweep.csv", index=False)
    print(f"  saved: {outdir / 'cost_sweep.csv'}  ({len(out)} rows)")


def sizing_sweep(df: pd.DataFrame, threshold: float, initial: float, outdir: Path) -> None:
    """Sweep fixed-dollar and Kelly-fractional sizing."""
    base_costs = Costs(0.015, 0.005, 0.02)
    priced = reprice(df, base_costs)
    priced = priced[priced["signal_edge_up"].abs() >= threshold].copy()
    priced["window_start"] = pd.to_datetime(priced["window_start"], utc=True)
    rules = []
    for usd in [0.5, 1.0, 2.0, 5.0, 10.0, 25.0, 50.0]:
        rules.append(SizeRule(mode="fixed", fixed_usd=usd))
    for kf in [0.10, 0.25, 0.5, 0.75, 1.0]:
        for cap_pct in [0.05, 0.10, 0.15, 0.25]:
            for cap_usd in [100.0, 250.0, 500.0, 1000.0]:
                rules.append(
                    SizeRule(mode="kelly", kelly_fraction=kf,
                             max_pct_per_trade=cap_pct, max_position_usd=cap_usd)
                )
    rows = []
    for r in rules:
        eq = simulate_sequential(priced, initial, r)
        summ = summarize_run(eq, initial)
        rows.append({"mode": r.mode, "label": r.label,
                     "kelly_fraction": r.kelly_fraction if r.mode == "kelly" else None,
                     "max_pct_per_trade": r.max_pct_per_trade if r.mode == "kelly" else None,
                     "max_position_usd": r.max_position_usd if r.mode == "kelly" else None,
                     "fixed_usd": r.fixed_usd if r.mode == "fixed" else None,
                     **summ})
    out = pd.DataFrame(rows)
    out.sort_values("final_bankroll", ascending=False, inplace=True)
    out.to_csv(outdir / "sizing_sweep.csv", index=False)
    print(f"  saved: {outdir / 'sizing_sweep.csv'}  ({len(out)} rules tested)")


def walk_forward(df: pd.DataFrame, threshold: float, initial: float, outdir: Path) -> None:
    """Per-month: train on prior month's edge stats, test on current month."""
    base_costs = Costs(0.015, 0.005, 0.02)
    priced = reprice(df, base_costs)
    priced = priced[priced["signal_edge_up"].abs() >= threshold].copy()
    priced["window_start"] = pd.to_datetime(priced["window_start"], utc=True)
    priced["month"] = priced["window_start"].dt.to_period("M")
    months = sorted(priced["month"].unique())
    rows = []
    for i, m in enumerate(months):
        # Train: all prior months (cumulative).
        train = priced[priced["month"] < m]
        test = priced[priced["month"] == m]
        if test.empty:
            continue
        train_pnl_mean = float(train["pnl_real"].mean()) if len(train) else float("nan")
        train_win_rate = float(train["correct"].mean()) if len(train) else float("nan")
        # Walk-forward equity: keep bankroll from previous month, only trade test rows.
        rule = SizeRule(mode="kelly", kelly_fraction=0.5,
                        max_pct_per_trade=0.15, max_position_usd=500.0)
        cumulative = priced[priced["month"] <= m].sort_values("window_start")
        eq = simulate_sequential(cumulative, initial, rule)
        month_eq = eq[eq["month"] == m] if "month" in eq.columns else eq.assign(month=eq["window_start"].dt.to_period("M"))
        month_eq = eq.assign(month=eq["window_start"].dt.to_period("M"))
        month_eq = month_eq[month_eq["month"] == m]
        start_b = float(eq.iloc[0]["bankroll"] - eq.iloc[0]["dollar_pnl"]) if i == 0 else float(eq[eq["month"] < m].iloc[-1]["bankroll"]) if len(eq[eq["month"] < m]) else initial
        end_b = float(month_eq.iloc[-1]["bankroll"])
        rows.append({
            "month": str(m),
            "train_n": int(len(train)),
            "train_pnl_real_mean_$": train_pnl_mean,
            "train_win_rate": train_win_rate,
            "test_n": int(len(test)),
            "test_win_rate": float(test["correct"].mean()),
            "test_pnl_real_mean_$": float(test["pnl_real"].mean()),
            "bankroll_start": start_b,
            "bankroll_end": end_b,
            "bankroll_pnl": end_b - start_b,
            "bankroll_pct": (end_b - start_b) / start_b * 100.0 if start_b > 0 else float("nan"),
        })
    out = pd.DataFrame(rows)
    out.to_csv(outdir / "walk_forward.csv", index=False)
    print(f"  saved: {outdir / 'walk_forward.csv'}  ({len(out)} months)")


def bootstrap_pnl(df: pd.DataFrame, threshold: float, initial: float, outdir: Path,
                  n_boot: int = 1000) -> None:
    """Bootstrap the trade list to get a distribution on final bankroll."""
    base_costs = Costs(0.015, 0.005, 0.02)
    priced = reprice(df, base_costs)
    priced = priced[priced["signal_edge_up"].abs() >= threshold].copy()
    priced["window_start"] = pd.to_datetime(priced["window_start"], utc=True)
    n = len(priced)
    if n == 0:
        return
    rule = SizeRule(mode="kelly", kelly_fraction=0.5,
                    max_pct_per_trade=0.15, max_position_usd=500.0)
    rng = np.random.default_rng(42)
    finals: list[float] = []
    drawdowns: list[float] = []
    for b in range(n_boot):
        idx = rng.integers(0, n, size=n)
        # Resample with replacement, but KEEP chronological order so compounding
        # behaves like a real sequence; otherwise capped lots of large bets early.
        sampled = priced.iloc[idx].sort_values("window_start").reset_index(drop=True)
        eq = simulate_sequential(sampled, initial, rule)
        finals.append(float(eq["bankroll"].iloc[-1]))
        peak = eq["bankroll"].cummax()
        dd = ((eq["bankroll"] - peak) / peak).min()
        drawdowns.append(float(dd))
    finals_arr = np.array(finals)
    dd_arr = np.array(drawdowns)
    q = np.quantile(finals_arr, [0.05, 0.25, 0.50, 0.75, 0.95])
    qd = np.quantile(dd_arr, [0.05, 0.50, 0.95])
    out = pd.DataFrame({
        "metric": ["final_bankroll_p05", "final_bankroll_p25", "final_bankroll_p50",
                   "final_bankroll_p75", "final_bankroll_p95",
                   "max_drawdown_p05", "max_drawdown_p50", "max_drawdown_p95",
                   "mean_final", "std_final"],
        "value": list(q) + list(qd * 100) + [float(finals_arr.mean()), float(finals_arr.std())],
    })
    out.to_csv(outdir / "bootstrap.csv", index=False)
    # Save raw finals for histogram.
    pd.DataFrame({"final_bankroll": finals_arr}).to_csv(outdir / "bootstrap_finals.csv", index=False)
    print(f"  saved: {outdir / 'bootstrap.csv'}  ({n_boot} bootstraps)")


def time_of_day(df: pd.DataFrame, threshold: float, outdir: Path) -> None:
    """Win rate and pnl per UTC hour and per weekday."""
    base_costs = Costs(0.015, 0.005, 0.02)
    priced = reprice(df, base_costs)
    priced = priced[priced["signal_edge_up"].abs() >= threshold].copy()
    priced["window_start"] = pd.to_datetime(priced["window_start"], utc=True)
    priced["hour_utc"] = priced["window_start"].dt.hour
    priced["weekday"] = priced["window_start"].dt.day_name()
    hr = priced.groupby("hour_utc").agg(
        n=("pnl_real", "size"),
        win_rate=("correct", "mean"),
        avg_edge_pp=("signal_edge_up", lambda s: s.abs().mean() * 100),
        pnl_real_total=("pnl_real", "sum"),
        pnl_real_per_trade=("pnl_real", "mean"),
    ).round(4)
    hr.to_csv(outdir / "by_hour_utc.csv")
    print(f"  saved: {outdir / 'by_hour_utc.csv'}")
    wd_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    wd = priced.groupby("weekday").agg(
        n=("pnl_real", "size"),
        win_rate=("correct", "mean"),
        pnl_real_total=("pnl_real", "sum"),
        pnl_real_per_trade=("pnl_real", "mean"),
    ).reindex(wd_order).round(4)
    wd.to_csv(outdir / "by_weekday.csv")
    print(f"  saved: {outdir / 'by_weekday.csv'}")


def edge_buckets(df: pd.DataFrame, outdir: Path) -> None:
    """Bucket by |edge| magnitude; check survival of edge after realistic costs."""
    base_costs = Costs(0.015, 0.005, 0.02)
    priced = reprice(df, base_costs)
    priced = priced[priced["signal_edge_up"].abs() >= 0.01].copy()
    priced["edge_abs"] = priced["signal_edge_up"].abs()
    bins = [0.01, 0.03, 0.05, 0.07, 0.10, 0.15, 0.20, 0.50]
    labels = [f"{a*100:.0f}-{b*100:.0f}pp" for a, b in zip(bins[:-1], bins[1:])]
    priced["bucket"] = pd.cut(priced["edge_abs"], bins=bins, labels=labels)
    out = priced.groupby("bucket", observed=True).agg(
        n=("pnl_real", "size"),
        win_rate=("correct", "mean"),
        avg_edge_pp=("signal_edge_up", lambda s: s.abs().mean() * 100),
        avg_p_poly=("p_poly_at_signal", "mean"),
        pnl_naive_total=("pnl_naive", "sum"),
        pnl_real_total=("pnl_real", "sum"),
        pnl_real_per_trade=("pnl_real", "mean"),
        roi_real_per_trade=("roi_real", "mean"),
    ).round(4)
    out.to_csv(outdir / "edge_buckets.csv")
    print(f"  saved: {outdir / 'edge_buckets.csv'}")


def per_asset(df: pd.DataFrame, threshold: float, initial: float, outdir: Path) -> None:
    """Run the simulator separately per asset (no cross-asset compounding)."""
    base_costs = Costs(0.015, 0.005, 0.02)
    priced = reprice(df, base_costs)
    priced = priced[priced["signal_edge_up"].abs() >= threshold].copy()
    priced["window_start"] = pd.to_datetime(priced["window_start"], utc=True)
    rule = SizeRule(mode="kelly", kelly_fraction=0.5,
                    max_pct_per_trade=0.15, max_position_usd=500.0)
    rows = []
    for asset, sub in priced.groupby("asset"):
        eq = simulate_sequential(sub.copy(), initial, rule)
        summ = summarize_run(eq, initial)
        rows.append({"asset": asset, "n_markets": int(len(sub)), **summ})
    out = pd.DataFrame(rows).sort_values("pnl", ascending=False)
    out.to_csv(outdir / "per_asset.csv", index=False)
    print(f"  saved: {outdir / 'per_asset.csv'}")


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------


def load_csvs(paths: Iterable[str]) -> pd.DataFrame:
    frames = []
    for p in paths:
        f = pd.read_csv(p)
        f["__src"] = Path(p).stem
        frames.append(f)
    df = pd.concat(frames, ignore_index=True)
    df["window_start"] = pd.to_datetime(df["window_start"], utc=True, errors="coerce")
    df = df.dropna(subset=["window_start"])
    return df


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--csvs", nargs="+", required=True,
                   help="Per-market CSV files from analyze_polymarket_edge.py")
    p.add_argument("--outdir", required=True, help="Where to drop result CSVs")
    p.add_argument("--threshold", type=float, default=0.05,
                   help="Min |edge| (default 0.05 = 5pp)")
    p.add_argument("--initial", type=float, default=100.0)
    p.add_argument("--bootstraps", type=int, default=1000)
    p.add_argument("--skip", nargs="*", default=[],
                   choices=["cost", "sizing", "walk", "boot", "tod", "buckets", "asset"],
                   help="Skip individual analyses (e.g. 'boot' for fast iteration)")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    print(f"Loading {len(args.csvs)} CSV(s)...")
    df = load_csvs(args.csvs)
    sig = df[df["signal"].isin(["UP", "DOWN"])]
    above = sig[sig["signal_edge_up"].abs() >= args.threshold]
    print(f"  Total markets : {len(df)}")
    print(f"  With signal   : {len(sig)}  ({len(sig)/len(df)*100:.1f}%)")
    print(f"  Above {args.threshold*100:.1f}pp threshold : {len(above)}")
    print(f"  Window: {df['window_start'].min()} → {df['window_start'].max()}")
    print(f"  By asset: {df['asset'].value_counts().to_dict()}")
    print()

    if "buckets" not in args.skip:
        print("[1/7] edge buckets...")
        edge_buckets(df, outdir)
    if "asset" not in args.skip:
        print("[2/7] per-asset standalone runs...")
        per_asset(df, args.threshold, args.initial, outdir)
    if "tod" not in args.skip:
        print("[3/7] time-of-day...")
        time_of_day(df, args.threshold, outdir)
    if "walk" not in args.skip:
        print("[4/7] walk-forward by month...")
        walk_forward(df, args.threshold, args.initial, outdir)
    if "sizing" not in args.skip:
        print("[5/7] sizing sweep...")
        sizing_sweep(df, args.threshold, args.initial, outdir)
    if "cost" not in args.skip:
        print("[6/7] cost sensitivity (8 × 4 × 5 = 160 configs)...")
        cost_sweep(df, args.threshold, args.initial, outdir)
    if "boot" not in args.skip:
        print(f"[7/7] bootstrap ({args.bootstraps} draws)...")
        bootstrap_pnl(df, args.threshold, args.initial, outdir, n_boot=args.bootstraps)

    print()
    print("Done. Results in:", outdir)


if __name__ == "__main__":
    main()
