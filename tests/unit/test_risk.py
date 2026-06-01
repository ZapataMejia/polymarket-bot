"""Test 7.1-7.7: Risk management engine."""
import pytest

from src.core.config import RiskConfig
from src.risk.manager import RiskManager


@pytest.fixture
def risk_mgr():
    config = RiskConfig(
        max_risk_per_trade=0.02,
        max_daily_loss=0.05,
        max_drawdown=0.15,
        max_open_positions=3,
    )
    return RiskManager(config=config, capital=10_000.0, peak_capital=10_000.0)


class TestPositionSizing:
    def test_size_within_capital(self, risk_mgr):
        size = risk_mgr.calculate_position_size(
            entry_price=50000, stop_loss_price=49500
        )
        assert size > 0
        risk_amount = size * abs(50000 - 49500)
        assert risk_amount <= risk_mgr.capital * risk_mgr.config.max_risk_per_trade

    def test_zero_risk_distance(self, risk_mgr):
        size = risk_mgr.calculate_position_size(
            entry_price=50000, stop_loss_price=50000
        )
        assert size == 0.0


class TestTradeGating:
    def test_normal_trade_allowed(self, risk_mgr):
        ok, msg = risk_mgr.can_open_trade("BTC/USDT", 100)
        assert ok is True

    def test_exceeds_risk_per_trade(self, risk_mgr):
        ok, msg = risk_mgr.can_open_trade("BTC/USDT", 500)
        assert ok is False
        assert "Risk per trade" in msg

    def test_max_positions_enforced(self, risk_mgr):
        risk_mgr.register_open("BTC/USDT", "LONG", 50000, 0.01)
        risk_mgr.register_open("ETH/USDT", "LONG", 3000, 0.1)
        risk_mgr.register_open("SOL/USDT", "LONG", 100, 1.0)
        ok, msg = risk_mgr.can_open_trade("AVAX/USDT", 50)
        assert ok is False
        assert "Max open positions" in msg

    def test_daily_loss_triggers_circuit_breaker(self, risk_mgr):
        risk_mgr.register_open("BTC/USDT", "LONG", 50000, 1.0)
        risk_mgr.register_close("BTC/USDT", 49000)  # -1000 loss on 10k capital = 10%
        ok, msg = risk_mgr.can_open_trade("ETH/USDT", 50)
        assert ok is False
        assert "Circuit breaker" in msg or "Daily loss" in msg or "drawdown" in msg.lower()


class TestPnLTracking:
    def test_profitable_trade(self, risk_mgr):
        risk_mgr.register_open("BTC/USDT", "LONG", 50000, 0.1)
        pnl = risk_mgr.register_close("BTC/USDT", 51000)
        assert pnl == pytest.approx(100.0)
        assert risk_mgr.capital == pytest.approx(10100.0)

    def test_losing_trade(self, risk_mgr):
        risk_mgr.register_open("BTC/USDT", "LONG", 50000, 0.1)
        pnl = risk_mgr.register_close("BTC/USDT", 49000)
        assert pnl == pytest.approx(-100.0)
        assert risk_mgr.capital == pytest.approx(9900.0)

    def test_short_trade(self, risk_mgr):
        risk_mgr.register_open("BTC/USDT", "SHORT", 50000, 0.1)
        pnl = risk_mgr.register_close("BTC/USDT", 49000)
        assert pnl == pytest.approx(100.0)
