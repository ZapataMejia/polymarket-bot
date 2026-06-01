"""
Estrategia conservadora adaptativa al régimen de mercado.

Objetivo: pocas operaciones, menos exposición en chop, reglas distintas según
alcista / bajista / lateral, con salidas claras para limitar pérdidas.

No garantiza ganancias en ningún mercado; busca robustez relativa (menos
sufrir en caídas fuertes y no forzar tendencia en rangos).
"""
from __future__ import annotations

import pandas as pd

from src.strategies.base import SignalDirection, Strategy


class RegimeAdaptiveConservative(Strategy):
    """
    - Régimen alcista: solo largos (EMA 9/21 alineada con filtro EMA 200).
    - Régimen bajista: solo cortos (simétrico).
    - Régimen lateral: mean reversion suave en bandas + RSI (sin ir contra EMA200 fuerte).

    La señal por fila refleja la posición deseada (LONG/SHORT/FLAT) con
    persistencia: mantiene hasta cruce de salida o rotura de tendencia.
    """

    name = "regime_adaptive_conservative"  # overridden in __init__

    def __init__(
        self,
        rsi_mr_long: int = 38,
        rsi_mr_short: int = 62,
        min_atr_pct: float = 0.0025,
        bb_width_quantile: float = 0.20,
        allow_short: bool = True,
        cooldown_bars: int = 0,
        mr_confirm_bars: int = 1,
        trend_require_close_vs_ema21: bool = False,
        name_override: str | None = None,
    ):
        self.rsi_mr_long = rsi_mr_long
        self.rsi_mr_short = rsi_mr_short
        self.min_atr_pct = min_atr_pct
        self.bb_width_quantile = bb_width_quantile
        self.allow_short = allow_short
        self.cooldown_bars = max(0, cooldown_bars)
        self.mr_confirm_bars = max(1, mr_confirm_bars)
        self.trend_require_close_vs_ema21 = trend_require_close_vs_ema21
        if name_override:
            self.name = name_override
        else:
            self.name = "regime_adaptive_spot" if not allow_short else "regime_adaptive_full"

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        out = pd.Series(SignalDirection.FLAT, index=df.index)

        need = [
            "close", "ema_9", "ema_21", "ema_50", "ema_200",
            "bb_lower", "bb_upper", "bb_middle", "bb_width", "rsi_14", "atr_14",
        ]
        if not all(c in df.columns for c in need):
            return out

        bb_med = df["bb_width"].rolling(60, min_periods=20).quantile(self.bb_width_quantile)
        atr_pct = df["atr_14"] / df["close"].replace(0, pd.NA)
        vol_ok = atr_pct >= self.min_atr_pct
        not_dead = (df["bb_width"] >= bb_med).fillna(True)

        bull_regime = (df["ema_21"] > df["ema_50"]) & (df["close"] > df["ema_200"])
        bear_regime = (df["ema_21"] < df["ema_50"]) & (df["close"] < df["ema_200"])
        neutral_regime = ~(bull_regime | bear_regime)

        ema_bull_cross = (df["ema_9"] > df["ema_21"]) & (df["ema_9"].shift(1) <= df["ema_21"].shift(1))
        ema_bear_cross = (df["ema_9"] < df["ema_21"]) & (df["ema_9"].shift(1) >= df["ema_21"].shift(1))

        mr_long = (
            (df["close"] <= df["bb_lower"])
            & (df["rsi_14"] < self.rsi_mr_long)
            & (df["close"] >= df["ema_200"] * 0.97)
        )
        mr_short = (
            (df["close"] >= df["bb_upper"])
            & (df["rsi_14"] > self.rsi_mr_short)
            & (df["close"] <= df["ema_200"] * 1.03)
        )

        def mr_long_ok(idx: int) -> bool:
            if idx < self.mr_confirm_bars - 1:
                return False
            for k in range(self.mr_confirm_bars):
                if not bool(mr_long.iloc[idx - k]):
                    return False
            return True

        def mr_short_ok(idx: int) -> bool:
            if idx < self.mr_confirm_bars - 1:
                return False
            for k in range(self.mr_confirm_bars):
                if not bool(mr_short.iloc[idx - k]):
                    return False
            return True

        position = 0
        cooldown_remain = 0

        for i, ts in enumerate(df.index):
            if i == 0:
                continue

            row_vol = bool(vol_ok.iloc[i]) and bool(not_dead.iloc[i])
            bull = bool(bull_regime.iloc[i])
            bear = bool(bear_regime.iloc[i])
            neu = bool(neutral_regime.iloc[i])

            ema_bull_now = bool(ema_bull_cross.iloc[i])
            ema_bear_now = bool(ema_bear_cross.iloc[i])
            if self.trend_require_close_vs_ema21:
                ema_bull_now = ema_bull_now and (df["close"].iloc[i] > df["ema_21"].iloc[i])
                ema_bear_now = ema_bear_now and (df["close"].iloc[i] < df["ema_21"].iloc[i])

            if position == 0:
                if cooldown_remain > 0:
                    cooldown_remain -= 1
                    out.iloc[i] = SignalDirection.FLAT
                    continue
                if not row_vol:
                    out.iloc[i] = SignalDirection.FLAT
                    continue
                enter_long = False
                enter_short = False
                if bull and ema_bull_now:
                    enter_long = True
                elif bear and ema_bear_now:
                    enter_short = True
                elif neu and mr_long_ok(i):
                    enter_long = True
                elif neu and mr_short_ok(i):
                    enter_short = True

                if enter_long and not enter_short:
                    position = 1
                    out.iloc[i] = SignalDirection.LONG
                elif enter_short and not enter_long and self.allow_short:
                    position = -1
                    out.iloc[i] = SignalDirection.SHORT
                else:
                    out.iloc[i] = SignalDirection.FLAT

            elif position == 1:
                exit_long = (
                    bool(ema_bear_cross.iloc[i])
                    or (df["close"].iloc[i] < df["ema_200"].iloc[i])
                    or (neu and df["close"].iloc[i] >= df["bb_upper"].iloc[i])
                )
                if exit_long:
                    position = 0
                    cooldown_remain = self.cooldown_bars
                    out.iloc[i] = SignalDirection.FLAT
                else:
                    out.iloc[i] = SignalDirection.LONG

            else:
                if not self.allow_short:
                    position = 0
                    out.iloc[i] = SignalDirection.FLAT
                    continue
                exit_short = (
                    bool(ema_bull_cross.iloc[i])
                    or (df["close"].iloc[i] > df["ema_200"].iloc[i])
                    or (neu and df["close"].iloc[i] <= df["bb_lower"].iloc[i])
                )
                if exit_short:
                    position = 0
                    cooldown_remain = self.cooldown_bars
                    out.iloc[i] = SignalDirection.FLAT
                else:
                    out.iloc[i] = SignalDirection.SHORT

        return out


class RegimeAdaptiveUltraConservative(RegimeAdaptiveConservative):
    """
    Menos trades, filtros más duros:
    - ATR mínimo más alto (solo cuando hay movimiento real).
    - Bandas de Bollinger más anchas que el percentil 35 (evita chop).
    - Mean reversion: RSI más extremo y 2 velas seguidas cumpliendo.
    - Tendencia: cruce EMA + cierre del lado correcto de EMA21.
    - Cooldown tras cerrar (12 velas en timeframe del backtest = 12h si es 1h).
    """

    def __init__(self, allow_short: bool = True):
        super().__init__(
            rsi_mr_long=32,
            rsi_mr_short=68,
            min_atr_pct=0.004,
            bb_width_quantile=0.35,
            allow_short=allow_short,
            cooldown_bars=12,
            mr_confirm_bars=2,
            trend_require_close_vs_ema21=True,
            name_override="regime_ultra_spot" if not allow_short else "regime_ultra_full",
        )
