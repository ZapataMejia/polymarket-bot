"""Phase 9: Telegram notifications for trade alerts and daily reports."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import ssl

import aiohttp
import certifi

logger = logging.getLogger("trading.notifications")


class TelegramNotifier:
    """Send messages to Telegram via Bot API (no extra dependencies)."""

    BASE_URL = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.enabled = bool(token and chat_id)

    def _ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context(cafile=certifi.where())
        return ctx

    async def send(self, text: str, parse_mode: str = "HTML") -> bool:
        if not self.enabled:
            logger.debug("Telegram disabled, skipping: %s", text[:50])
            return False
        url = self.BASE_URL.format(token=self.token)
        payload = {"chat_id": self.chat_id, "text": text, "parse_mode": parse_mode}
        try:
            conn = aiohttp.TCPConnector(ssl=self._ssl_context())
            async with aiohttp.ClientSession(connector=conn) as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status == 200:
                        return True
                    logger.error("Telegram error %d: %s", resp.status, await resp.text())
                    return False
        except Exception:
            logger.exception("Telegram send failed")
            return False

    async def notify_trade(self, symbol: str, side: str, price: float, strategy: str, risk_pct: float) -> bool:
        now = datetime.now(timezone.utc).strftime("%H:%M UTC")
        msg = (
            f"🔔 <b>TRADE {side}</b>\n"
            f"Symbol: <code>{symbol}</code>\n"
            f"Price: <code>{price:.2f}</code>\n"
            f"Strategy: {strategy}\n"
            f"Risk: {risk_pct:.1%}\n"
            f"Time: {now}"
        )
        return await self.send(msg)

    async def notify_daily_report(
        self, pnl: float, total_trades: int, win_rate: float, portfolio_value: float
    ) -> bool:
        emoji = "📈" if pnl >= 0 else "📉"
        msg = (
            f"{emoji} <b>Daily Report</b>\n"
            f"PnL: <code>${pnl:+,.2f}</code>\n"
            f"Trades: {total_trades}\n"
            f"Win Rate: {win_rate:.1%}\n"
            f"Portfolio: <code>${portfolio_value:,.2f}</code>"
        )
        return await self.send(msg)

    async def notify_error(self, error: str) -> bool:
        return await self.send(f"⚠️ <b>ERROR</b>\n<code>{error[:500]}</code>")

    async def notify_circuit_breaker(self, reason: str) -> bool:
        return await self.send(f"🛑 <b>CIRCUIT BREAKER</b>\n{reason}")
