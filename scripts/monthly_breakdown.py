"""Mes a mes del último año con la config EXACTA del bot live:
   Half Kelly (0.50), cap 15% bankroll, threshold 5pp, costos realistas 3¢ + 2%.

   Replica la lógica de paper_trader.py para sizing/costos, sobre los CSVs
   del backtest BTC+ETH hourly.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))


CSV_PATHS = [
    "data/poly_backtest_year/btc_hourly_1y_full.csv",
    "data/poly_backtest_year/eth_hourly_1y_full.csv",
]
INITIAL_BANKROLL = 100.0
KELLY_FRACTION = 0.50      # Half Kelly
MAX_PCT_PER_TRADE = 0.15   # cap por trade
THRESHOLD = 0.05           # 5pp
MIN_POSITION_USD = 1.0     # Polymarket minimum
MAX_POSITION_USD = 500.0   # realista: top-of-book depth en Up/Down crypto
BANKROLL_FLOOR = 30.0      # bot pausa si baja de aquí

# Costos realistas (los mismos que el daemon real asume)
HALF_SPREAD = 0.015     # 1.5¢
FLAT_FEE = 0.005        # 0.5¢ gas/relayer
FEE_RATE = 0.02         # 2% taker fee


def load_signals() -> pd.DataFrame:
    dfs = []
    for p in CSV_PATHS:
        df = pd.read_csv(p)
        sig = df[df["signal"].isin(["UP", "DOWN"])].copy()
        sig["window_start"] = pd.to_datetime(sig["window_start"], utc=True)
        dfs.append(sig)
    out = pd.concat(dfs, ignore_index=True)
    # Filtrar señales que pasen el umbral (el CSV viene con threshold 5pp ya)
    out = out[out["signal_edge_up"].abs() >= THRESHOLD].copy()
    return out.sort_values("window_start").reset_index(drop=True)


def simulate(sig: pd.DataFrame) -> tuple[pd.DataFrame, list[float]]:
    """Recorre las señales en orden cronológico, aplicando Half Kelly + cap."""
    bankroll = INITIAL_BANKROLL
    equity = [bankroll]
    paused_due_to_floor = 0
    rows = []

    for _, row in sig.iterrows():
        if bankroll < BANKROLL_FLOOR:
            paused_due_to_floor += 1
            rows.append({
                "window_start": row["window_start"],
                "asset": row["asset"],
                "signal": row["signal"],
                "took": False,
                "pnl": 0.0,
                "bankroll_after": bankroll,
            })
            equity.append(bankroll)
            continue

        direction = row["signal"]
        p_fair_up = float(row["p_fair_at_signal"])
        p_poly_up = float(row["p_poly_at_signal"])

        # Re-derivar fill como hace el daemon (no usar fill_price del CSV
        # porque el CSV ya tenía un half_spread asumido y queremos ser consistentes).
        if direction == "UP":
            p_model = p_fair_up
            naive_fill = p_poly_up
        else:
            p_model = 1.0 - p_fair_up
            naive_fill = 1.0 - p_poly_up
        fill = min(1.0, naive_fill + HALF_SPREAD)

        # --- KELLY SIZING (mismo cálculo que paper_trader.py) ---
        fill_total = fill * (1.0 + FEE_RATE)
        edge_after = p_model - fill_total
        if edge_after <= 0 or fill >= 0.99:
            rows.append({
                "window_start": row["window_start"],
                "asset": row["asset"],
                "signal": direction,
                "took": False,
                "pnl": 0.0,
                "bankroll_after": bankroll,
            })
            equity.append(bankroll)
            continue
        f_kelly = edge_after / max(1e-6, 1.0 - fill_total)
        f = max(0.0, min(f_kelly * KELLY_FRACTION, MAX_PCT_PER_TRADE))
        position_usd = bankroll * f
        position_usd = min(position_usd, bankroll * 0.95, MAX_POSITION_USD)
        if position_usd < MIN_POSITION_USD:
            rows.append({
                "window_start": row["window_start"],
                "asset": row["asset"],
                "signal": direction,
                "took": False,
                "pnl": 0.0,
                "bankroll_after": bankroll,
            })
            equity.append(bankroll)
            continue

        contracts = position_usd / fill
        prop_fee = contracts * fill * FEE_RATE
        cost = contracts * fill + prop_fee + FLAT_FEE
        if cost > bankroll:
            scale = bankroll / cost
            contracts *= scale
            cost *= scale

        payoff = contracts if bool(row["correct"]) else 0.0
        pnl = payoff - cost
        bankroll += pnl
        equity.append(bankroll)
        rows.append({
            "window_start": row["window_start"],
            "asset": row["asset"],
            "signal": direction,
            "took": True,
            "edge": row["signal_edge_up"],
            "fill": fill,
            "position_usd": position_usd,
            "contracts": contracts,
            "cost": cost,
            "correct": bool(row["correct"]),
            "pnl": pnl,
            "bankroll_after": bankroll,
        })

    print(f"Total señales con |edge| >= {THRESHOLD*100:.0f}pp: {len(sig):,}")
    print(f"Pausadas por floor < ${BANKROLL_FLOOR}: {paused_due_to_floor:,}")
    return pd.DataFrame(rows), equity


def monthly_table(trades: pd.DataFrame) -> pd.DataFrame:
    taken = trades[trades["took"]].copy()
    taken = taken.sort_values("window_start").reset_index(drop=True)
    # Necesitamos bankroll ANTES del primer trade del mes y DESPUÉS del último.
    taken["month"] = taken["window_start"].dt.tz_convert("UTC").dt.strftime("%Y-%m")
    taken["bankroll_before"] = taken["bankroll_after"] - taken["pnl"]
    rows: list[dict] = []
    for month, sub in taken.groupby("month", sort=True):
        start_bk = float(sub["bankroll_before"].iloc[0])
        end_bk = float(sub["bankroll_after"].iloc[-1])
        pnl = end_bk - start_bk
        ret_pct = pnl / start_bk * 100 if start_bk > 0 else 0
        wins = int(sub["correct"].sum())
        n = int(len(sub))
        rows.append({
            "Mes":            month,
            "Inicio":         f"${start_bk:>12,.2f}",
            "Fin":            f"${end_bk:>13,.2f}",
            "PnL del mes":    f"${pnl:>+12,.2f}",
            "% del mes":      f"{ret_pct:>+7.1f}%",
            "Trades":         n,
            "WinRate":        f"{wins/n*100:>4.1f}%",
        })
    return pd.DataFrame(rows)


def main() -> None:
    sig = load_signals()
    print(f"\nCONFIGURACIÓN:")
    print(f"  bankroll inicial   ${INITIAL_BANKROLL}")
    print(f"  sizing             Half Kelly ({KELLY_FRACTION*100:.0f}%K), cap {MAX_PCT_PER_TRADE*100:.0f}%")
    print(f"  threshold edge     {THRESHOLD*100:.0f}pp")
    print(f"  costos             {HALF_SPREAD*100}¢ half-spread + {FLAT_FEE*100}¢ flat + {FEE_RATE*100}% fee")
    print(f"  cap por posición   ${MAX_POSITION_USD:.0f} (límite realista de orderbook depth)")
    print(f"  assets             BTC + ETH hourly (SOL/XRP no en backtest)")
    print()

    trades, equity = simulate(sig)
    table = monthly_table(trades)

    print()
    print("=" * 90)
    print(" PnL MES A MES — CONFIG IDÉNTICA AL BOT LIVE ".center(90, "="))
    print("=" * 90)
    print()
    # Imprime fila por fila para mejor formato.
    header = f"{'Mes':<9} {'Inicio mes':>14} {'Fin mes':>15} {'PnL mes':>14} {'% mes':>8} {'Trades':>7} {'WinRate':>8}"
    print(header)
    print("-" * 90)
    for _, r in table.iterrows():
        print(
            f"{r['Mes']:<9} {r['Inicio']:>14} {r['Fin']:>15} {r['PnL del mes']:>14} "
            f"{r['% del mes']:>8} {r['Trades']:>7} {r['WinRate']:>8}"
        )
    print("-" * 90)

    final = trades["bankroll_after"].iloc[-1] if len(trades) else INITIAL_BANKROLL
    total_pnl = final - INITIAL_BANKROLL
    roi = total_pnl / INITIAL_BANKROLL * 100

    taken = trades[trades["took"]]
    print()
    print(f"\nRESUMEN ANUAL:")
    print(f"  Bankroll final         ${final:,.2f}  (de ${INITIAL_BANKROLL})")
    print(f"  PnL total              ${total_pnl:+,.2f}")
    print(f"  ROI                    {roi:+,.1f}%")
    print(f"  Trades tomados         {len(taken):,}  de {len(trades):,} señales")
    print(f"  Win rate               {taken['correct'].mean()*100:.1f}%")
    eq_arr = np.array(equity)
    peak = np.maximum.accumulate(eq_arr)
    dd = (eq_arr - peak) / peak * 100
    print(f"  Max drawdown           {dd.min():.1f}%")
    print(f"  Posición promedio      ${taken['position_usd'].mean():.2f}")
    print(f"  Posición más grande    ${taken['position_usd'].max():.2f}")


if __name__ == "__main__":
    main()
