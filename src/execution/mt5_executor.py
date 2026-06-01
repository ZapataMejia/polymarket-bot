"""Phase 6: MetaTrader 5 integration for Forex trading.

NOTE: MetaTrader5 only runs on Windows. On macOS/Linux this module
provides the interface but requires a Windows machine or VM for live execution.
Import errors are caught gracefully so the rest of the system works everywhere.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger("trading.execution.mt5")

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False
    logger.info("MetaTrader5 not available — MT5 features disabled (Windows only)")


@dataclass
class MT5Order:
    symbol: str
    side: str  # BUY / SELL
    volume: float
    price: float
    sl: float = 0.0
    tp: float = 0.0
    comment: str = ""


class MT5Executor:
    """Execute trades on MetaTrader 5 platform."""

    def __init__(self):
        self.connected = False

    def connect(self) -> bool:
        if not MT5_AVAILABLE:
            logger.error("MetaTrader5 package not installed. Install on Windows: pip install MetaTrader5")
            return False
        if not mt5.initialize():
            logger.error("MT5 initialize failed: %s", mt5.last_error())
            return False
        info = mt5.terminal_info()
        logger.info("Connected to MT5: %s", info.name if info else "unknown")
        self.connected = True
        return True

    def disconnect(self) -> None:
        if MT5_AVAILABLE and self.connected:
            mt5.shutdown()
            self.connected = False

    def get_price(self, symbol: str) -> dict | None:
        if not self._check():
            return None
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return None
        return {"bid": tick.bid, "ask": tick.ask, "time": tick.time}

    def open_order(self, order: MT5Order) -> int | None:
        if not self._check():
            return None
        order_type = mt5.ORDER_TYPE_BUY if order.side == "BUY" else mt5.ORDER_TYPE_SELL
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": order.symbol,
            "volume": order.volume,
            "type": order_type,
            "price": order.price,
            "sl": order.sl,
            "tp": order.tp,
            "deviation": 20,
            "magic": 234000,
            "comment": order.comment or "trading-ai",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error("Order failed: %s", result)
            return None
        logger.info("Order opened: ticket=%d, %s %s %.2f @ %.5f", result.order, order.side, order.symbol, order.volume, order.price)
        return result.order

    def close_position(self, ticket: int, symbol: str, volume: float, side: str) -> bool:
        if not self._check():
            return False
        close_type = mt5.ORDER_TYPE_SELL if side == "BUY" else mt5.ORDER_TYPE_BUY
        tick = mt5.symbol_info_tick(symbol)
        price = tick.bid if side == "BUY" else tick.ask
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": close_type,
            "position": ticket,
            "price": price,
            "deviation": 20,
            "magic": 234000,
            "comment": "close trading-ai",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error("Close failed: %s", result)
            return False
        logger.info("Position closed: ticket=%d", ticket)
        return True

    def get_positions(self) -> list[dict]:
        if not self._check():
            return []
        positions = mt5.positions_get()
        if positions is None:
            return []
        return [
            {
                "ticket": p.ticket,
                "symbol": p.symbol,
                "side": "BUY" if p.type == 0 else "SELL",
                "volume": p.volume,
                "price_open": p.price_open,
                "price_current": p.price_current,
                "profit": p.profit,
                "sl": p.sl,
                "tp": p.tp,
            }
            for p in positions
        ]

    def _check(self) -> bool:
        if not MT5_AVAILABLE:
            logger.error("MT5 not available")
            return False
        if not self.connected:
            logger.error("MT5 not connected")
            return False
        return True
