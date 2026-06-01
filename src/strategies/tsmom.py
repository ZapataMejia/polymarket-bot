"""
Time Series Momentum (TSMOM) spot: largo si el activo sube en lookbacks;
caja si no. Rebalanceo semanal + vol targeting (literatura Moskowitz et al.;
adaptación spot sin cortos).

Referencias públicas: TSMOM (Moskowitz, Ooi, Pedersen); vol targeting en
gestión sistemática.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger("trading.strategies.tsmom")


def _default_lookbacks(n_bars: int) -> tuple[int, ...]:
    """Si hay poco histórico, acorta lookbacks para que el backtest tenga sentido."""
    if n_bars < 400:
        a = max(10, n_bars // 15)
        b = max(15, n_bars // 8)
        c = max(20, min(63, n_bars // 4))
        return tuple(sorted(set(int(x) for x in (a, b, c) if x < n_bars - 5)))
    return (63, 126, 252)


def build_tsmom_binary_signal(
    df: pd.DataFrame,
    lookbacks: tuple[int, ...] | None = None,
    min_votes: int | None = None,
    rebalance_rule: str = "W-FRI",
) -> pd.Series:
    """
    Serie 0/1 alineada al índice de df (diario): 1 = en largo según votos TSMOM.
    Se actualiza solo en el cierre de cada periodo de rebalanceo; el resto ffil.
    """
    close = df["close"].astype(float)
    n = len(close)
    lbs = lookbacks if lookbacks is not None else _default_lookbacks(n)
    if not lbs:
        return pd.Series(0.0, index=df.index)

    votes_needed = min_votes if min_votes is not None else max(1, min(2, len(lbs)))

    vote_count = pd.Series(0.0, index=df.index)
    for L in lbs:
        mom = close / close.shift(L) - 1.0
        vote_count = vote_count + (mom > 0).astype(float)

    raw = (vote_count >= votes_needed).astype(float)
    raw = raw.fillna(0.0)

    # Rebalanceo: último valor del bucket (viernes semanal por defecto)
    weekly = raw.resample(rebalance_rule).last()
    binary_ffill = weekly.reindex(df.index).ffill().fillna(0.0)
    return binary_ffill.clip(0.0, 1.0)


def build_vol_target_weights(
    close: pd.Series,
    target_ann_vol: float = 0.12,
    vol_lookback: int = 20,
    max_leverage: float = 1.0,
    min_vol_floor: float = 0.05,
) -> pd.Series:
    """
    w_t = clip(target_ann_vol / realized_ann_vol, 0, max_leverage).
    min_vol_floor evita dividir por vol casi cero.
    """
    r = close.pct_change()
    realized = r.rolling(vol_lookback, min_periods=5).std() * np.sqrt(252.0)
    realized = realized.clip(lower=min_vol_floor)
    w = target_ann_vol / realized.replace(0, np.nan)
    w = w.clip(upper=max_leverage).fillna(0.0)
    return w.clip(0.0, max_leverage)


def build_tsmom_exposure(
    df: pd.DataFrame,
    lookbacks: tuple[int, ...] | None = None,
    min_votes: int | None = None,
    rebalance_rule: str = "W-FRI",
    target_ann_vol: float = 0.12,
    vol_lookback: int = 20,
    max_leverage: float = 1.0,
) -> pd.Series:
    binary = build_tsmom_binary_signal(
        df, lookbacks=lookbacks, min_votes=min_votes, rebalance_rule=rebalance_rule
    )
    w = build_vol_target_weights(
        df["close"],
        target_ann_vol=target_ann_vol,
        vol_lookback=vol_lookback,
        max_leverage=max_leverage,
    )
    exposure = (binary * w).clip(0.0, max_leverage)
    return exposure


def compute_tsmom_snapshot(
    daily: pd.DataFrame,
    *,
    lookbacks: tuple[int, ...] | None = None,
    min_votes: int | None = None,
    rebalance_rule: str = "W-FRI",
    target_ann_vol: float = 0.12,
    vol_lookback: int = 20,
    max_leverage: float = 1.0,
) -> dict[str, Any]:
    """
    Estado actual (última vela diaria) para alertas: votos por lookback,
    señal rebalanceada y exposición con vol targeting.
    """
    close = daily["close"].astype(float)
    n = len(close)
    lbs = lookbacks if lookbacks is not None else _default_lookbacks(n)
    if min_votes is not None:
        votes_needed = min_votes
    elif not lbs:
        votes_needed = 1
    else:
        votes_needed = max(1, min(2, len(lbs)))

    mom_rows: list[dict[str, Any]] = []
    vote_count = 0
    for L in lbs:
        if n <= L:
            ret_pct = None
            pos = False
        else:
            r = float(close.iloc[-1] / close.iloc[-(L + 1)] - 1.0)
            ret_pct = r * 100.0
            pos = r > 0
        if pos:
            vote_count += 1
        mom_rows.append({"lookback_days": L, "return_pct": ret_pct, "positive": pos})

    instant_long = vote_count >= votes_needed
    binary_last = float(
        build_tsmom_binary_signal(
            daily, lookbacks=lbs, min_votes=min_votes, rebalance_rule=rebalance_rule
        ).iloc[-1]
    )
    w_last = float(
        build_vol_target_weights(
            close,
            target_ann_vol=target_ann_vol,
            vol_lookback=vol_lookback,
            max_leverage=max_leverage,
        ).iloc[-1]
    )
    exp_last = float(
        build_tsmom_exposure(
            daily,
            lookbacks=lbs,
            min_votes=min_votes,
            rebalance_rule=rebalance_rule,
            target_ann_vol=target_ann_vol,
            vol_lookback=vol_lookback,
            max_leverage=max_leverage,
        ).iloc[-1]
    )

    return {
        "lookbacks_detail": mom_rows,
        "votes": vote_count,
        "votes_needed": votes_needed,
        "instant_momentum_long": instant_long,
        "rebalanced_binary": binary_last,
        "vol_weight": w_last,
        "target_exposure": exp_last,
        "stance": "LONG" if exp_last > 0.02 else "CASH",
        "close_last": float(close.iloc[-1]) if n else 0.0,
    }


def ohlcv_to_daily(ohlcv: pd.DataFrame) -> pd.DataFrame:
    """Convierte velas (ej. 1h) a OHLCV diario."""
    if ohlcv.empty:
        return ohlcv
    agg = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }
    cols = [c for c in agg if c in ohlcv.columns]
    d = ohlcv[cols].resample("1D").agg({c: agg[c] for c in cols}).dropna(how="any")
    return d
