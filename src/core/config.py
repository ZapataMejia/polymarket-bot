from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


@dataclass
class ExchangeConfig:
    name: str = "binance"
    sandbox: bool = True
    rate_limit: int = 1200
    timeout: int = 30000
    api_key: str = ""
    secret: str = ""


@dataclass
class RiskConfig:
    max_risk_per_trade: float = 0.02
    max_daily_loss: float = 0.05
    max_drawdown: float = 0.15
    max_open_positions: int = 5
    max_correlation: float = 0.8
    circuit_breaker_cooldown_minutes: int = 60


@dataclass
class TsmomConfig:
    """Parámetros TSMOM + vol targeting (leídos de config/default.yaml)."""

    rebalance_rule: str = "W-FRI"
    target_ann_vol: float = 0.12
    vol_lookback: int = 20
    max_leverage: float = 1.0
    min_votes: int | None = None  # None = automático según nº de lookbacks


@dataclass
class Config:
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    symbols: list[str] = field(default_factory=lambda: ["BTC/USDT"])
    timeframes: list[str] = field(default_factory=lambda: ["1m", "5m", "15m", "1h", "4h"])
    risk: RiskConfig = field(default_factory=RiskConfig)
    data_storage_path: str = "data/raw"
    data_processed_path: str = "data/processed"
    data_format: str = "parquet"
    history_days: int = 365
    log_level: str = "INFO"
    log_file: str = "logs/trading.log"
    database_url: str = "sqlite+aiosqlite:///data/trades.db"
    telegram_enabled: bool = False
    telegram_token: str = ""
    telegram_chat_id: str = ""
    tsmom: TsmomConfig = field(default_factory=TsmomConfig)

    @classmethod
    def from_yaml(cls, path: str | Path = "config/default.yaml") -> Config:
        load_dotenv()

        config_path = Path(path)
        if not config_path.exists():
            return cls._with_env_keys(cls())

        with open(config_path) as f:
            raw: dict[str, Any] = yaml.safe_load(f) or {}

        exc_raw = raw.get("exchange", {})
        exchange = ExchangeConfig(
            name=exc_raw.get("name", "binance"),
            sandbox=exc_raw.get("sandbox", True),
            rate_limit=exc_raw.get("rate_limit", 1200),
            timeout=exc_raw.get("timeout", 30000),
        )

        risk_raw = raw.get("risk", {})
        risk = RiskConfig(
            max_risk_per_trade=risk_raw.get("max_risk_per_trade", 0.02),
            max_daily_loss=risk_raw.get("max_daily_loss", 0.05),
            max_drawdown=risk_raw.get("max_drawdown", 0.15),
            max_open_positions=risk_raw.get("max_open_positions", 5),
            max_correlation=risk_raw.get("max_correlation", 0.8),
            circuit_breaker_cooldown_minutes=risk_raw.get("circuit_breaker_cooldown_minutes", 60),
        )

        data_raw = raw.get("data", {})
        log_raw = raw.get("logging", {})
        db_raw = raw.get("database", {})
        tg_raw = raw.get("notifications", {}).get("telegram", {})
        tsmom_raw = raw.get("tsmom", {})
        tsmom_cfg = TsmomConfig(
            rebalance_rule=str(tsmom_raw.get("rebalance_rule", "W-FRI")),
            target_ann_vol=float(tsmom_raw.get("target_ann_vol", 0.12)),
            vol_lookback=int(tsmom_raw.get("vol_lookback", 20)),
            max_leverage=float(tsmom_raw.get("max_leverage", 1.0)),
            min_votes=tsmom_raw.get("min_votes"),
        )

        cfg = cls(
            exchange=exchange,
            symbols=raw.get("symbols", ["BTC/USDT"]),
            timeframes=raw.get("timeframes", ["1m", "5m", "15m", "1h", "4h"]),
            risk=risk,
            data_storage_path=data_raw.get("storage_path", "data/raw"),
            data_processed_path=data_raw.get("processed_path", "data/processed"),
            data_format=data_raw.get("format", "parquet"),
            history_days=data_raw.get("history_days", 365),
            log_level=log_raw.get("level", "INFO"),
            log_file=log_raw.get("file", "logs/trading.log"),
            database_url=db_raw.get("url", "sqlite+aiosqlite:///data/trades.db"),
            telegram_enabled=tg_raw.get("enabled", False),
            tsmom=tsmom_cfg,
        )
        return cls._with_env_keys(cfg)

    @staticmethod
    def _with_env_keys(cfg: Config) -> Config:
        name = cfg.exchange.name.upper()
        cfg.exchange.api_key = os.getenv(f"{name}_API_KEY", "")
        cfg.exchange.secret = os.getenv(f"{name}_SECRET", "")
        cfg.telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        cfg.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        return cfg
