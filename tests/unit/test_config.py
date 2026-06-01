"""Test 0.1-0.5: Config, logger, database initialization."""
import pytest

from src.core.config import Config, ExchangeConfig, RiskConfig, TsmomConfig


class TestConfig:
    def test_default_config(self):
        cfg = Config()
        assert cfg.exchange.name == "binance"
        assert cfg.exchange.sandbox is True
        assert "BTC/USDT" in cfg.symbols
        assert cfg.risk.max_risk_per_trade == 0.02

    def test_from_yaml(self):
        cfg = Config.from_yaml("config/default.yaml")
        assert cfg.exchange.name == "binance"
        assert len(cfg.symbols) >= 1
        assert len(cfg.timeframes) >= 1
        assert cfg.risk.max_drawdown == 0.15
        assert cfg.log_level == "INFO"

    def test_from_yaml_missing_file(self):
        cfg = Config.from_yaml("nonexistent.yaml")
        assert cfg.exchange.name == "binance"

    def test_exchange_config(self):
        ec = ExchangeConfig(name="bybit", sandbox=False, rate_limit=600, timeout=10000)
        assert ec.name == "bybit"
        assert ec.sandbox is False

    def test_risk_config(self):
        rc = RiskConfig(max_risk_per_trade=0.01, max_daily_loss=0.03)
        assert rc.max_risk_per_trade == 0.01
        assert rc.max_daily_loss == 0.03

    def test_tsmom_from_yaml(self):
        cfg = Config.from_yaml("config/default.yaml")
        assert isinstance(cfg.tsmom, TsmomConfig)
        assert cfg.tsmom.rebalance_rule == "W-FRI"
        assert cfg.tsmom.target_ann_vol == 0.12
        assert cfg.tsmom.vol_lookback == 20
        assert cfg.tsmom.max_leverage == 1.0
