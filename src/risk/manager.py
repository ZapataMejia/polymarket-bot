from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from src.core.config import RiskConfig

logger = logging.getLogger("trading.risk")


@dataclass
class PositionInfo:
    symbol: str
    side: str
    entry_price: float
    quantity: float
    entry_time: datetime


@dataclass
class RiskManager:
    config: RiskConfig
    capital: float = 10_000.0
    daily_pnl: float = 0.0
    peak_capital: float = 10_000.0
    open_positions: dict[str, PositionInfo] = field(default_factory=dict)
    circuit_breaker_active: bool = False
    _daily_reset_date: str = ""

    def __post_init__(self) -> None:
        if not self._daily_reset_date:
            self._daily_reset_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _check_daily_reset(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._daily_reset_date:
            self.daily_pnl = 0.0
            self.circuit_breaker_active = False
            self._daily_reset_date = today

    def can_open_trade(self, symbol: str, risk_amount: float) -> tuple[bool, str]:
        self._check_daily_reset()

        if self.circuit_breaker_active:
            return False, "Circuit breaker active — trading paused"

        if len(self.open_positions) >= self.config.max_open_positions:
            return False, f"Max open positions ({self.config.max_open_positions}) reached"

        risk_pct = risk_amount / self.capital
        if risk_pct > self.config.max_risk_per_trade:
            return False, f"Risk per trade {risk_pct:.1%} exceeds max {self.config.max_risk_per_trade:.1%}"

        if abs(self.daily_pnl) / self.capital > self.config.max_daily_loss:
            self.circuit_breaker_active = True
            return False, f"Daily loss limit hit ({self.config.max_daily_loss:.1%})"

        drawdown = (self.peak_capital - self.capital) / self.peak_capital
        if drawdown > self.config.max_drawdown:
            self.circuit_breaker_active = True
            return False, f"Max drawdown {drawdown:.1%} exceeds limit {self.config.max_drawdown:.1%}"

        return True, "OK"

    def calculate_position_size(
        self,
        entry_price: float,
        stop_loss_price: float,
        risk_fraction: float | None = None,
    ) -> float:
        """Kelly-inspired position sizing based on distance to stop loss."""
        risk_frac = risk_fraction or self.config.max_risk_per_trade
        risk_amount = self.capital * risk_frac
        price_risk = abs(entry_price - stop_loss_price)
        if price_risk == 0:
            return 0.0
        return risk_amount / price_risk

    def register_open(self, symbol: str, side: str, entry_price: float, quantity: float) -> None:
        self.open_positions[symbol] = PositionInfo(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            quantity=quantity,
            entry_time=datetime.now(timezone.utc),
        )
        logger.info("Position opened: %s %s @ %.2f x %.4f", side, symbol, entry_price, quantity)

    def register_close(self, symbol: str, exit_price: float) -> float:
        pos = self.open_positions.pop(symbol, None)
        if pos is None:
            return 0.0
        multiplier = 1 if pos.side == "LONG" else -1
        pnl = (exit_price - pos.entry_price) * pos.quantity * multiplier
        self.daily_pnl += pnl
        self.capital += pnl
        self.peak_capital = max(self.peak_capital, self.capital)
        logger.info("Position closed: %s PnL=%.2f (daily=%.2f)", symbol, pnl, self.daily_pnl)
        return pnl
