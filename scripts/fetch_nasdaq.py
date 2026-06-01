"""Baja datos intradía de Nasdaq (futuros NQ=F y ETF QQQ) vía yfinance.

Límites de Yahoo: 1m -> ~7d, 5m/15m -> ~60d, 1h -> ~730d.
Para MNQ no hay feed gratis; NQ=F (E-mini Nasdaq) es el proxy correcto (mismo subyacente).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

OUT = Path(__file__).resolve().parent.parent / "data" / "raw" / "nasdaq"
OUT.mkdir(parents=True, exist_ok=True)

JOBS = [
    ("NQ=F", "5m", "60d"),
    ("NQ=F", "15m", "60d"),
    ("NQ=F", "1h", "730d"),
    ("NQ=F", "1m", "7d"),
    ("QQQ", "5m", "60d"),
]


def fetch(symbol: str, interval: str, period: str) -> None:
    df = yf.download(
        symbol, interval=interval, period=period,
        auto_adjust=False, prepost=True, progress=False,
    )
    if df is None or df.empty:
        print(f"  [!] vacío: {symbol} {interval} {period}")
        return
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
    df.index.name = "timestamp"
    safe = symbol.replace("=", "")
    path = OUT / f"{safe}_{interval}.parquet"
    df.to_parquet(path)
    print(f"  [ok] {symbol} {interval}: {len(df)} velas  {df.index.min()} -> {df.index.max()}  ({path.name})")


if __name__ == "__main__":
    print(f"Guardando en {OUT}")
    for sym, itv, per in JOBS:
        try:
            fetch(sym, itv, per)
        except Exception as e:  # noqa: BLE001
            print(f"  [error] {sym} {itv}: {e}", file=sys.stderr)
