from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.strategies.base import SignalDirection, Strategy

logger = logging.getLogger("trading.strategies.backtest")


@dataclass
class BacktestResult:
    strategy_name: str
    total_return: float
    annual_return: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: float
    win_rate: float
    total_trades: int
    profit_factor: float
    buy_hold_return: float
    equity_curve: pd.Series
    trades: pd.DataFrame


class BacktestEngine:
    def __init__(
        self,
        commission: float = 0.001,
        slippage: float = 0.0005,
        initial_capital: float = 10_000.0,
    ):
        self.commission = commission
        self.slippage = slippage
        self.initial_capital = initial_capital

    def run(self, strategy: Strategy, df: pd.DataFrame) -> BacktestResult:
        signals = strategy.generate_signals(df)

        positions = signals.map(
            {SignalDirection.LONG: 1, SignalDirection.SHORT: -1, SignalDirection.FLAT: 0}
        ).fillna(0)

        returns = df["close"].pct_change().fillna(0)

        trade_mask = positions.diff().abs() > 0
        costs = trade_mask.astype(float) * (self.commission + self.slippage)

        strat_returns = (positions.shift(1).fillna(0) * returns) - costs
        equity = self.initial_capital * (1 + strat_returns).cumprod()
        buy_hold = self.initial_capital * (1 + returns).cumprod()

        trades = self._extract_trades(df, positions)

        return BacktestResult(
            strategy_name=strategy.name,
            total_return=float(equity.iloc[-1] / self.initial_capital - 1),
            annual_return=self._annual_return(strat_returns, periods=252 * 24),
            sharpe_ratio=self._sharpe(strat_returns, periods=252 * 24),
            sortino_ratio=self._sortino(strat_returns, periods=252 * 24),
            max_drawdown=self._max_drawdown(equity),
            win_rate=self._win_rate(trades),
            total_trades=len(trades),
            profit_factor=self._profit_factor(trades),
            buy_hold_return=float(buy_hold.iloc[-1] / self.initial_capital - 1),
            equity_curve=equity,
            trades=trades,
        )

    def run_exposure(
        self,
        df: pd.DataFrame,
        exposure: pd.Series,
        strategy_name: str = "tsmom_vol",
        periods_per_year: int = 252,
    ) -> BacktestResult:
        """
        Backtest con exposición fraccional [0, max] (ej. TSMOM + vol targeting).
        Costes proporcionales al cambio de exposición (turnover).
        """
        exposure = exposure.reindex(df.index).astype(float).fillna(0.0).clip(0.0, 10.0)
        returns = df["close"].pct_change().fillna(0.0)
        delta_e = exposure.diff().abs().fillna(exposure)
        costs = delta_e * (self.commission + self.slippage)
        strat_returns = exposure.shift(1).fillna(0.0) * returns - costs
        equity = self.initial_capital * (1 + strat_returns).cumprod()
        buy_hold = self.initial_capital * (1 + returns).cumprod()

        in_market = (exposure > 0.01).astype(int)
        trades = self._extract_trades(df, in_market)

        return BacktestResult(
            strategy_name=strategy_name,
            total_return=float(equity.iloc[-1] / self.initial_capital - 1),
            annual_return=self._annual_return(strat_returns, periods=periods_per_year),
            sharpe_ratio=self._sharpe(strat_returns, periods=periods_per_year),
            sortino_ratio=self._sortino(strat_returns, periods=periods_per_year),
            max_drawdown=self._max_drawdown(equity),
            win_rate=self._win_rate(trades),
            total_trades=len(trades),
            profit_factor=self._profit_factor(trades),
            buy_hold_return=float(buy_hold.iloc[-1] / self.initial_capital - 1),
            equity_curve=equity,
            trades=trades,
        )

    def _extract_trades(self, df: pd.DataFrame, positions: pd.Series) -> pd.DataFrame:
        trades_list = []
        entry_price = 0.0
        entry_time = None
        current_pos = 0

        for ts, pos in positions.items():
            pos = int(pos)
            if pos != current_pos:
                if current_pos != 0 and entry_time is not None:
                    exit_price = df.loc[ts, "close"]
                    pnl = (exit_price - entry_price) * current_pos
                    pnl_pct = pnl / entry_price
                    trades_list.append({
                        "entry_time": entry_time,
                        "exit_time": ts,
                        "side": "LONG" if current_pos > 0 else "SHORT",
                        "entry_price": entry_price,
                        "exit_price": exit_price,
                        "pnl": pnl,
                        "pnl_pct": pnl_pct,
                    })
                if pos != 0:
                    entry_price = df.loc[ts, "close"]
                    entry_time = ts
                current_pos = pos

        return pd.DataFrame(trades_list) if trades_list else pd.DataFrame(
            columns=["entry_time", "exit_time", "side", "entry_price", "exit_price", "pnl", "pnl_pct"]
        )

    @staticmethod
    def _sharpe(returns: pd.Series, risk_free: float = 0.0, periods: int = 252 * 24) -> float:
        excess = returns - risk_free / periods
        if excess.std() == 0:
            return 0.0
        return float(np.sqrt(periods) * excess.mean() / excess.std())

    @staticmethod
    def _sortino(returns: pd.Series, risk_free: float = 0.0, periods: int = 252 * 24) -> float:
        excess = returns - risk_free / periods
        downside = excess[excess < 0]
        if len(downside) == 0 or downside.std() == 0:
            return 0.0
        return float(np.sqrt(periods) * excess.mean() / downside.std())

    @staticmethod
    def _annual_return(returns: pd.Series, periods: int = 252 * 24) -> float:
        total = (1 + returns).prod()
        n = len(returns) / periods
        if n == 0:
            return 0.0
        return float(total ** (1 / n) - 1)

    @staticmethod
    def _max_drawdown(equity: pd.Series) -> float:
        peak = equity.expanding().max()
        dd = (equity - peak) / peak
        return float(dd.min())

    @staticmethod
    def _win_rate(trades: pd.DataFrame) -> float:
        if trades.empty:
            return 0.0
        return float((trades["pnl"] > 0).sum() / len(trades))

    @staticmethod
    def _profit_factor(trades: pd.DataFrame) -> float:
        if trades.empty:
            return 0.0
        gains = trades.loc[trades["pnl"] > 0, "pnl"].sum()
        losses = abs(trades.loc[trades["pnl"] < 0, "pnl"].sum())
        if losses == 0:
            return float("inf") if gains > 0 else 0.0
        return float(gains / losses)


def walk_forward_backtest(
    strategy: Strategy,
    df: pd.DataFrame,
    n_splits: int = 5,
    train_ratio: float = 0.7,
    **engine_kwargs,
) -> list[BacktestResult]:
    """Walk-forward analysis: train on past, test on future, roll forward."""
    engine = BacktestEngine(**engine_kwargs)
    total_len = len(df)
    split_size = total_len // n_splits
    results = []

    for i in range(n_splits):
        start = i * split_size
        end = min(start + split_size, total_len)
        split_data = df.iloc[start:end]

        train_end = int(len(split_data) * train_ratio)
        test_data = split_data.iloc[train_end:]

        if len(test_data) < 50:
            continue

        result = engine.run(strategy, test_data)
        result.strategy_name = f"{strategy.name}_split_{i}"
        results.append(result)
        logger.info(
            "Split %d: Sharpe=%.2f, Return=%.2f%%, MaxDD=%.2f%%",
            i, result.sharpe_ratio, result.total_return * 100, result.max_drawdown * 100,
        )

    return results
