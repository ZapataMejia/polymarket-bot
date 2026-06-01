"""Phase 8: Paper trading engine — simulates live trading with real data."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from src.core.config import Config
from src.core.database import Database, Trade, PortfolioSnapshot
from src.data.exchange import ExchangeClient
from src.features.pipeline import FeaturePipeline
from src.notifications.telegram import TelegramNotifier
from src.risk.manager import RiskManager
from src.strategies.base import SignalDirection, Strategy

logger = logging.getLogger("trading.execution.paper")


@dataclass
class PaperPosition:
    symbol: str
    side: str
    entry_price: float
    quantity: float
    entry_time: datetime


class PaperTrader:
    """Execute strategies on live data without real money."""

    def __init__(
        self,
        config: Config,
        strategies: list[Strategy],
        initial_capital: float = 10_000.0,
    ):
        self.config = config
        self.strategies = strategies
        self.client = ExchangeClient(config.exchange)
        self.pipeline = FeaturePipeline()
        self.risk = RiskManager(config=config.risk, capital=initial_capital, peak_capital=initial_capital)
        self.db = Database(config.database_url)
        self.notifier = TelegramNotifier(config.telegram_token, config.telegram_chat_id)
        self.positions: dict[str, PaperPosition] = {}
        self.trade_log: list[dict] = []

    async def init(self) -> None:
        await self.db.init_tables()
        logger.info("Paper trader initialized. Capital: $%.2f", self.risk.capital)

    async def close(self) -> None:
        await self.client.close()
        await self.db.close()

    async def run(self, interval_seconds: int = 60) -> None:
        """Main loop: fetch data → compute features → check signals → execute."""
        await self.init()
        logger.info("Paper trading started (interval=%ds)", interval_seconds)

        while True:
            try:
                for symbol in self.config.symbols:
                    await self._process_symbol(symbol)
                await self._snapshot_portfolio()
            except Exception:
                logger.exception("Paper trading loop error")
                await self.notifier.notify_error("Paper trading loop error")
            await asyncio.sleep(interval_seconds)

    async def _process_symbol(self, symbol: str) -> None:
        df = await self.client.fetch_ohlcv(symbol, "5m", limit=300)
        if df.empty:
            return

        features = self.pipeline.build(df)
        if features.empty:
            return

        for strategy in self.strategies:
            signals = strategy.generate_signals(features)
            last_signal = signals.iloc[-1]

            if last_signal == SignalDirection.LONG and symbol not in self.positions:
                await self._open_position(symbol, "LONG", strategy.name, features)
            elif last_signal == SignalDirection.SHORT and symbol in self.positions:
                await self._close_position(symbol, strategy.name)
            elif last_signal == SignalDirection.FLAT and symbol in self.positions:
                await self._close_position(symbol, strategy.name)

    async def _open_position(self, symbol: str, side: str, strategy: str, features) -> None:
        price = features["close"].iloc[-1]
        atr_val = features.get("atr_14", features["close"] * 0.02).iloc[-1]
        sl_price = price - atr_val * 2 if side == "LONG" else price + atr_val * 2
        risk_amount = abs(price - sl_price) * self.risk.calculate_position_size(price, sl_price)

        ok, msg = self.risk.can_open_trade(symbol, risk_amount)
        if not ok:
            logger.info("Trade blocked: %s — %s", symbol, msg)
            return

        qty = self.risk.calculate_position_size(price, sl_price)
        self.positions[symbol] = PaperPosition(symbol, side, price, qty, datetime.now(timezone.utc))
        self.risk.register_open(symbol, side, price, qty)

        async with self.db.session() as s:
            s.add(Trade(symbol=symbol, side="BUY", price=price, quantity=qty, fee=0, strategy=strategy))
            await s.commit()

        risk_pct = risk_amount / self.risk.capital
        await self.notifier.notify_trade(symbol, side, price, strategy, risk_pct)
        logger.info("📗 OPEN %s %s @ %.2f qty=%.4f (%s)", side, symbol, price, qty, strategy)

    async def _close_position(self, symbol: str, strategy: str) -> None:
        pos = self.positions.pop(symbol, None)
        if pos is None:
            return
        ticker = await self.client.fetch_ticker(symbol)
        exit_price = ticker["last"]
        pnl = self.risk.register_close(symbol, exit_price)

        async with self.db.session() as s:
            s.add(Trade(symbol=symbol, side="SELL", price=exit_price, quantity=pos.quantity, fee=0, pnl=pnl, strategy=strategy))
            await s.commit()

        self.trade_log.append({"symbol": symbol, "pnl": pnl, "time": datetime.now(timezone.utc)})
        await self.notifier.notify_trade(symbol, "CLOSE", exit_price, strategy, 0)
        logger.info("📕 CLOSE %s @ %.2f PnL=%.2f (%s)", symbol, exit_price, pnl, strategy)

    async def _snapshot_portfolio(self) -> None:
        total = self.risk.capital
        for sym, pos in self.positions.items():
            try:
                ticker = await self.client.fetch_ticker(sym)
                total += pos.quantity * ticker["last"]
            except Exception:
                total += pos.quantity * pos.entry_price

        async with self.db.session() as s:
            s.add(PortfolioSnapshot(
                total_value=total,
                cash=self.risk.capital,
                unrealized_pnl=total - self.risk.capital,
                realized_pnl=self.risk.daily_pnl,
                open_positions=len(self.positions),
            ))
            await s.commit()
