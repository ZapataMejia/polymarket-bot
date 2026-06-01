"""Simulate the Polymarket Up/Down strategy with a real bankroll.

Takes the per-market signals from the year-long backtest and runs a sequential
portfolio simulation: each signal uses a fraction of CURRENT bankroll (Kelly-style),
applies realistic costs, and rolls forward.

Outputs:
- Final bankroll and total return %
- Max drawdown
- Daily PnL stats
- Comparison across sizing strategies (fixed-pct, quarter-Kelly, half-Kelly, full-Kelly)
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))


@dataclass
class StrategyResult:
    name: str
    final_bankroll: float
    total_return_pct: float
    max_drawdown_pct: float
    sharpe: float
    n_trades: int
    n_skipped: int
    win_rate: float
    best_day: float
    worst_day: float
    avg_position_pct: float
    median_position_pct: float


def simulate(
    sig: pd.DataFrame,
    initial_bankroll: float,
    sizing: str,
    kelly_fraction: float = 0.25,
    fixed_pct: float = 0.02,
    fixed_usd: float = 2.0,
    max_pct_per_trade: float = 0.10,
    min_position_usd: float = 1.0,
    max_position_usd: float = 500.0,
    half_spread: float = 0.015,
    flat_fee: float = 0.005,
    fee_rate: float = 0.02,
) -> tuple[pd.DataFrame, StrategyResult]:
    """Run a single sizing strategy through the signal stream.

    sizing:
        'fixed_pct': bet `fixed_pct` of bankroll each trade
        'kelly'    : bet kelly_fraction × (p_model - fill_price) / (1 - fill_price)
                     of bankroll, capped at max_pct_per_trade
    """
    sig = sig.sort_values("window_start").reset_index(drop=True)

    bankroll = initial_bankroll
    equity: list[float] = [initial_bankroll]
    pos_pcts: list[float] = []
    pnls: list[float] = []
    n_skipped = 0
    n_won = 0
    n_traded = 0

    for _, row in sig.iterrows():
        # Reconstruct effective fill price for chosen direction.
        direction = row["signal"]
        p_poly = row["p_poly_at_signal"]
        p_fair = row["p_fair_at_signal"]
        if direction == "UP":
            naive_fill = p_poly
            p_model = p_fair
        else:
            naive_fill = 1.0 - p_poly
            p_model = 1.0 - p_fair
        fill = min(1.0, naive_fill + half_spread)
        prop_fee = fill * fee_rate

        # Decide position size as fraction of CURRENT bankroll.
        if sizing == "fixed_usd":
            # Flat dollar amount per trade, no compounding.
            if bankroll < fixed_usd or fixed_usd < min_position_usd:
                n_skipped += 1
                equity.append(bankroll)
                continue
            position_usd = fixed_usd
            f = position_usd / bankroll
            # Skip the % logic below; do trade directly.
            contracts = position_usd / fill
            cost_paid = contracts * fill + contracts * prop_fee + flat_fee
            if cost_paid > bankroll:
                scale = bankroll / cost_paid
                contracts *= scale; cost_paid *= scale
            payoff = contracts if row["correct"] else 0.0
            trade_pnl = payoff - cost_paid
            bankroll += trade_pnl
            equity.append(bankroll); pos_pcts.append(f * 100); pnls.append(trade_pnl)
            n_traded += 1
            if row["correct"]: n_won += 1
            if bankroll <= 0:
                for _ in range(len(sig) - len(equity) + 1):
                    equity.append(0.0)
                break
            continue
        if sizing == "fixed_pct":
            f = fixed_pct
        elif sizing == "kelly":
            edge_after_costs = p_model - fill - prop_fee - flat_fee
            if edge_after_costs <= 0 or fill >= 1.0:
                f = 0.0
            else:
                # Kelly for binary bet: f* = (p*b - q)/b with b=(1-fill)/fill.
                # Equivalent: f* = (p_model - fill_total) / (1 - fill_total).
                fill_total = fill + prop_fee + flat_fee
                f = (p_model - fill_total) / max(1e-6, (1.0 - fill_total))
                f *= kelly_fraction
        else:
            raise ValueError(f"unknown sizing: {sizing}")

        f = max(0.0, min(f, max_pct_per_trade))
        # Position = bankroll × f, but constrained by what we can actually buy:
        # we spend `position_usd` on one share that costs `fill` → contracts = position_usd / fill.
        position_usd = min(bankroll * f, max_position_usd)
        if position_usd < min_position_usd or position_usd <= 0:
            n_skipped += 1
            equity.append(bankroll)
            continue

        contracts = position_usd / fill  # each contract pays $1 if right
        # Realistic cost paid up front:
        cost_paid = contracts * fill + contracts * prop_fee + flat_fee
        # If we somehow can't afford it (rounding), trim:
        if cost_paid > bankroll:
            scale = bankroll / cost_paid
            contracts *= scale
            cost_paid *= scale
        payoff = contracts if row["correct"] else 0.0

        trade_pnl = payoff - cost_paid
        bankroll += trade_pnl
        equity.append(bankroll)
        pos_pcts.append(f * 100)
        pnls.append(trade_pnl)
        n_traded += 1
        if row["correct"]:
            n_won += 1
        if bankroll <= 0:
            # Wiped out — push remaining equity as 0
            for _ in range(len(sig) - len(equity) + 1):
                equity.append(0.0)
            break

    sig_out = sig.copy()
    sig_out["equity"] = equity[1: len(sig_out) + 1]

    eq_arr = np.array(equity)
    peak = np.maximum.accumulate(eq_arr)
    dd = (eq_arr - peak) / peak
    max_dd = float(dd.min() * 100)

    sig_out["day"] = pd.to_datetime(sig_out["window_start"], utc=True).dt.date
    daily_eq = sig_out.groupby("day")["equity"].last().to_frame("equity")
    daily_eq["pnl"] = daily_eq["equity"].diff().fillna(daily_eq["equity"].iloc[0] - initial_bankroll)
    daily_pnl = daily_eq["pnl"]
    sharpe = (
        float(daily_pnl.mean() / daily_pnl.std() * np.sqrt(252))
        if daily_pnl.std() > 0 else 0.0
    )

    win_rate = (n_won / n_traded) if n_traded else 0.0
    final = float(eq_arr[-1])
    if sizing == "kelly":
        label = f"kelly ({kelly_fraction*100:.0f}%K)"
    elif sizing == "fixed_pct":
        label = f"fixed_pct ({fixed_pct*100:.1f}%/trade)"
    elif sizing == "fixed_usd":
        label = f"flat ${fixed_usd:.0f}/trade"
    else:
        label = sizing
    res = StrategyResult(
        name=label,
        final_bankroll=final,
        total_return_pct=(final / initial_bankroll - 1) * 100,
        max_drawdown_pct=max_dd,
        sharpe=sharpe,
        n_trades=n_traded,
        n_skipped=n_skipped,
        win_rate=win_rate,
        best_day=float(daily_pnl.max()) if not daily_pnl.empty else 0.0,
        worst_day=float(daily_pnl.min()) if not daily_pnl.empty else 0.0,
        avg_position_pct=float(np.mean(pos_pcts)) if pos_pcts else 0.0,
        median_position_pct=float(np.median(pos_pcts)) if pos_pcts else 0.0,
    )
    return sig_out, res


def load_signals(csv_paths: list[str]) -> pd.DataFrame:
    dfs = []
    for p in csv_paths:
        df = pd.read_csv(p)
        sig = df[df["signal"].isin(["UP", "DOWN"])].copy()
        sig["window_start"] = pd.to_datetime(sig["window_start"], utc=True)
        sig["source"] = Path(p).stem
        dfs.append(sig)
    out = pd.concat(dfs, ignore_index=True)
    return out.sort_values("window_start").reset_index(drop=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bankroll", type=float, default=100.0)
    ap.add_argument("--csv", nargs="+", default=[
        "data/poly_backtest_year/btc_hourly_1y_full.csv",
        "data/poly_backtest_year/eth_hourly_1y_full.csv",
    ])
    ap.add_argument("--half-spread-cents", type=float, default=1.5)
    ap.add_argument("--fee-rate-pct", type=float, default=2.0)
    ap.add_argument("--min-position-usd", type=float, default=1.0,
                    help="Polymarket minimum order size (most markets use $1-5)")
    ap.add_argument("--max-position-usd", type=float, default=500.0,
                    help="Realistic orderbook depth ceiling per fill (default $500)")
    ap.add_argument("--out-equity-csv", default="data/poly_backtest_year/bankroll_equity.csv")
    args = ap.parse_args()

    sig = load_signals(args.csv)
    print(f"Loaded {len(sig):,} signals from {len(args.csv)} files")
    print(f"Date range: {sig['window_start'].min()}  →  {sig['window_start'].max()}")
    print(f"Starting bankroll: ${args.bankroll:,.2f}")
    print()

    common = dict(
        half_spread=args.half_spread_cents / 100,
        fee_rate=args.fee_rate_pct / 100,
        flat_fee=0.005,
        min_position_usd=args.min_position_usd,
        max_position_usd=args.max_position_usd,
    )

    runs = [
        ("fixed_usd", dict(fixed_usd=1.0)),
        ("fixed_usd", dict(fixed_usd=2.0)),
        ("fixed_usd", dict(fixed_usd=5.0)),
        ("fixed_pct", dict(fixed_pct=0.02)),
        ("kelly",     dict(kelly_fraction=0.10)),
        ("kelly",     dict(kelly_fraction=0.25)),
        ("kelly",     dict(kelly_fraction=0.50)),
    ]

    print("=" * 100)
    print(f" SIZING STRATEGY COMPARISON — START ${args.bankroll:.0f} bankroll".center(100))
    print("=" * 100)
    print(
        f"  {'strategy':<25} {'final':>10}  {'ret%':>8}  {'maxDD%':>8}  "
        f"{'sharpe':>7}  {'trades':>7}  {'win%':>6}  {'avg_pos%':>9}"
    )
    print("  " + "-" * 96)

    equity_curves: dict[str, pd.Series] = {}
    for sizing, kwargs in runs:
        sig_out, res = simulate(sig, args.bankroll, sizing=sizing, **kwargs, **common)
        label = res.name
        print(
            f"  {label:<25} ${res.final_bankroll:>9,.2f}  "
            f"{res.total_return_pct:>+7.2f}%  {res.max_drawdown_pct:>+7.2f}%  "
            f"{res.sharpe:>7.2f}  {res.n_trades:>7d}  {res.win_rate*100:>5.1f}%  "
            f"{res.avg_position_pct:>8.2f}%"
        )
        equity_curves[label] = sig_out.set_index("window_start")["equity"]
    print("=" * 100)
    print()
    print("Notes:")
    print(f"  - Costs: {args.half_spread_cents}¢ half-spread + 0.5¢ flat + {args.fee_rate_pct}% taker fee")
    print(f"  - Position range: ${args.min_position_usd}–${args.max_position_usd:.0f}.")
    print(f"    Min = Polymarket minimum order. Max = realistic top-of-book depth.")
    print(f"  - Trades below minimum are SKIPPED; oversized bets are CAPPED at max.")
    print(f"  - 'avg_pos%' = avg position as %% of bankroll at trade time.")
    print(f"  - 'kelly (NN%K)' = NN% of full Kelly stake; lower = safer, higher = bigger drawdowns.")

    # Save the best equity curve
    eq = pd.DataFrame(equity_curves)
    eq.index.name = "timestamp"
    out = Path(args.out_equity_csv)
    out.parent.mkdir(parents=True, exist_ok=True)
    eq.to_csv(out)
    print(f"\nWrote equity curves to {out}")


if __name__ == "__main__":
    main()
