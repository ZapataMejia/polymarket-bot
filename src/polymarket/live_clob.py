"""Polymarket CLOB live order execution (real USDC).

Wraps the synchronous ``py-clob-client`` SDK for use inside the async
``PaperTrader`` loop. Credentials come from environment variables only.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    AssetType,
    BalanceAllowanceParams,
    MarketOrderArgs,
    OrderType,
)
from py_clob_client.order_builder.constants import BUY

logger = logging.getLogger("trading.polymarket.live_clob")

CLOB_HOST = "https://clob.polymarket.com"


@dataclass
class LiveClobConfig:
    private_key: str
    funder_address: str
    signature_type: int = 1
    chain_id: int = 137
    max_slippage_cents: float = 5.0


@dataclass
class LiveOrderResult:
    ok: bool
    token_id: str
    amount_usd: float
    fill_price: float = 0.0
    contracts: float = 0.0
    cost_paid: float = 0.0
    order_id: str = ""
    raw: dict[str, Any] | None = None
    error: str = ""


def load_live_config() -> LiveClobConfig:
    load_dotenv()
    key = os.getenv("POLYMARKET_PRIVATE_KEY", "").strip()
    funder = os.getenv("POLYMARKET_FUNDER_ADDRESS", "").strip()
    if not key or not funder:
        raise RuntimeError(
            "POLYMARKET_PRIVATE_KEY and POLYMARKET_FUNDER_ADDRESS must be set in .env"
        )
    sig = int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "1"))
    chain = int(os.getenv("POLYMARKET_CHAIN_ID", "137"))
    slip = float(os.getenv("POLYMARKET_MAX_SLIPPAGE_CENTS", "5"))
    return LiveClobConfig(
        private_key=key,
        funder_address=funder,
        signature_type=sig,
        chain_id=chain,
        max_slippage_cents=slip,
    )


class LiveClobExecutor:
    """Places FOK market buys on Polymarket CLOB."""

    def __init__(self, config: LiveClobConfig):
        self.cfg = config
        self._client: ClobClient | None = None

    def _client_sync(self) -> ClobClient:
        if self._client is None:
            client = ClobClient(
                CLOB_HOST,
                key=self.cfg.private_key,
                chain_id=self.cfg.chain_id,
                signature_type=self.cfg.signature_type,
                funder=self.cfg.funder_address,
            )
            client.set_api_creds(client.create_or_derive_api_creds())
            self._client = client
        return self._client

    async def _run(self, fn, *args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)

    async def ensure_allowance(self) -> None:
        """Refresh collateral allowance (safe to call on startup)."""
        def _go() -> None:
            client = self._client_sync()
            params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            client.update_balance_allowance(params)

        await self._run(_go)
        logger.info("collateral allowance updated")

    async def get_usdc_balance(self) -> float:
        def _go() -> float:
            client = self._client_sync()
            params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            data = client.get_balance_allowance(params)
            if not isinstance(data, dict):
                return 0.0
            # balance is in micro-USDC (6 decimals)
            raw = data.get("balance") or "0"
            return int(raw) / 1_000_000.0

        return await self._run(_go)

    async def buy_fok(
        self,
        token_id: str,
        amount_usd: float,
        max_price: float | None = None,
    ) -> LiveOrderResult:
        """Market-buy ``amount_usd`` dollars of ``token_id`` (FOK)."""

        def _go() -> LiveOrderResult:
            client = self._client_sync()
            try:
                mo = MarketOrderArgs(
                    token_id=token_id,
                    amount=amount_usd,
                    side=BUY,
                    order_type=OrderType.FOK,
                )
                if max_price and max_price > 0:
                    mo.price = max_price
                signed = client.create_market_order(mo)
                resp = client.post_order(signed, OrderType.FOK)
                return _parse_order_response(token_id, amount_usd, resp)
            except Exception as exc:
                logger.exception("live order failed")
                return LiveOrderResult(
                    ok=False,
                    token_id=token_id,
                    amount_usd=amount_usd,
                    error=str(exc),
                )

        return await self._run(_go)


def _parse_order_response(
    token_id: str,
    amount_usd: float,
    resp: Any,
) -> LiveOrderResult:
    if resp is None:
        return LiveOrderResult(
            ok=False, token_id=token_id, amount_usd=amount_usd,
            error="empty response from CLOB",
        )

    if isinstance(resp, dict):
        data = resp
    else:
        data = {"raw": str(resp)}

    status = str(data.get("status", "")).lower()
    success = data.get("success")
    if success is False:
        return LiveOrderResult(
            ok=False, token_id=token_id, amount_usd=amount_usd,
            raw=data, error=data.get("errorMsg") or data.get("error") or "order rejected",
        )
    if status in ("matched", "filled", "live", "delayed"):
        ok = True
    elif success is True:
        ok = True
    else:
        ok = bool(data.get("orderID") or data.get("orderId"))

    fill_price = _f(data.get("price") or data.get("avgPrice") or 0)
    contracts = _f(
        data.get("takingAmount")
        or data.get("size")
        or data.get("filledSize")
        or 0
    )
    cost = _f(data.get("makingAmount") or amount_usd)
    if contracts > 0 and fill_price <= 0 and cost > 0:
        fill_price = cost / contracts
    if fill_price <= 0:
        fill_price = 0.5  # fallback for bookkeeping

    order_id = str(data.get("orderID") or data.get("orderId") or "")

    return LiveOrderResult(
        ok=ok,
        token_id=token_id,
        amount_usd=amount_usd,
        fill_price=fill_price,
        contracts=contracts if contracts > 0 else amount_usd / max(fill_price, 0.01),
        cost_paid=cost if cost > 0 else amount_usd,
        order_id=order_id,
        raw=data,
        error="" if ok else str(data),
    )


def _f(val: Any) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0
