"""Polymarket CLOB live order execution (real USDC).

Uses ``py-clob-client-v2`` (CLOB V2 / signature_type=3 deposit wallets).
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv
from py_clob_client_v2 import ClobClient, MarketOrderArgs, OrderType, PartialCreateOrderOptions
from py_clob_client_v2.clob_types import AssetType, BalanceAllowanceParams
from py_clob_client_v2.order_builder.constants import BUY

logger = logging.getLogger("trading.polymarket.live_clob")

CLOB_HOST = "https://clob.polymarket.com"


@dataclass
class LiveClobConfig:
    private_key: str
    funder_address: str
    signature_type: int = 3  # 3=deposit wallet (cuentas Google nuevas); 1=Magic proxy viejo
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
    sig = int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "3"))
    chain = int(os.getenv("POLYMARKET_CHAIN_ID", "137"))
    slip = float(os.getenv("POLYMARKET_MAX_SLIPPAGE_CENTS", "10"))
    return LiveClobConfig(
        private_key=key,
        funder_address=funder,
        signature_type=sig,
        chain_id=chain,
        max_slippage_cents=slip,
    )


class LiveClobExecutor:
    """Places FOK market buys on Polymarket CLOB (V2 SDK)."""

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
            client.set_api_creds(client.create_or_derive_api_key())
            self._client = client
        return self._client

    async def _run(self, fn, *args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)

    def _balance_params(self) -> BalanceAllowanceParams:
        return BalanceAllowanceParams(
            asset_type=AssetType.COLLATERAL,
            signature_type=self.cfg.signature_type,
        )

    async def ensure_allowance(self) -> None:
        """Refresh collateral allowance (safe to call on startup)."""
        def _go() -> None:
            client = self._client_sync()
            client.update_balance_allowance(self._balance_params())

        await self._run(_go)
        logger.info("collateral allowance updated")

    async def get_usdc_balance(self) -> float:
        def _go() -> float:
            client = self._client_sync()
            data = client.get_balance_allowance(self._balance_params())
            bal = _parse_usdc_balance(data)
            logger.info("live USDC balance raw=%s parsed=%.2f", data, bal)
            return bal

        return await self._run(_go)

    async def get_balance_raw(self) -> dict:
        """Debug: respuesta cruda de Polymarket."""
        def _go() -> dict:
            client = self._client_sync()
            data = client.get_balance_allowance(self._balance_params())
            return data if isinstance(data, dict) else {"raw": str(data)}

        return await self._run(_go)

    def _fok_limit_price(
        self,
        client: ClobClient,
        token_id: str,
        amount_usd: float,
        price_ceiling: float,
    ) -> float:
        """Best FOK limit: SDK walk-the-book price + slippage, capped at ceiling."""
        slip = self.cfg.max_slippage_cents / 100.0
        try:
            calc = float(
                client.calculate_market_price(
                    token_id, BUY, amount_usd, OrderType.FOK,
                )
            )
            if calc > 0:
                return min(price_ceiling, calc + slip)
        except Exception as exc:
            logger.debug("calculate_market_price failed: %s", exc)
        return price_ceiling

    async def buy_fok(
        self,
        token_id: str,
        amount_usd: float,
        max_price: float | None = None,
    ) -> LiveOrderResult:
        """Market-buy ``amount_usd`` dollars of ``token_id`` (FOK).

        Retries up to 4 times with a wider price ceiling — FOK kills the order
        when the book cannot fill at the limit (common in endgame windows).
        """

        def _go() -> LiveOrderResult:
            client = self._client_sync()
            tick = client.get_tick_size(token_id)
            neg_risk = client.get_neg_risk(token_id)
            options = PartialCreateOrderOptions(tick_size=tick, neg_risk=neg_risk)
            base_ceiling = min(0.99, max_price if max_price and max_price > 0 else 0.99)
            # Widen ceiling on each retry (+5¢ / +10¢ / +15¢).
            ceiling_bumps = (0.0, 0.05, 0.10, 0.15)
            last: LiveOrderResult | None = None

            for attempt, bump in enumerate(ceiling_bumps):
                ceiling = min(0.99, base_ceiling + bump)
                price = self._fok_limit_price(client, token_id, amount_usd, ceiling)
                logger.info(
                    "FOK attempt %d token=%s amount=%.2f price=%.3f ceiling=%.3f",
                    attempt + 1, token_id[:16], amount_usd, price, ceiling,
                )
                try:
                    mo = MarketOrderArgs(
                        token_id=token_id,
                        amount=amount_usd,
                        side=BUY,
                        price=price,
                    )
                    signed = client.create_market_order(mo, options)
                    resp = client.post_order(signed, OrderType.FOK)
                    result = _parse_order_response(token_id, amount_usd, resp)
                    if result.ok:
                        if attempt > 0:
                            logger.info("FOK filled on retry %d", attempt + 1)
                        return result
                    last = result
                    err = (result.error or "").lower()
                    if "fully filled" not in err and "killed" not in err:
                        return result
                except Exception as exc:
                    err_s = str(exc)
                    logger.warning(
                        "FOK attempt %d failed token=%s: %s",
                        attempt + 1, token_id[:16], err_s[:200],
                    )
                    last = LiveOrderResult(
                        ok=False,
                        token_id=token_id,
                        amount_usd=amount_usd,
                        error=err_s,
                    )
                    if "fully filled" not in err_s.lower() and "killed" not in err_s.lower():
                        return last

            return last or LiveOrderResult(
                ok=False,
                token_id=token_id,
                amount_usd=amount_usd,
                error="FOK: no fill after retries",
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


def _parse_usdc_balance(data: Any) -> float:
    """Parse CLOB balance-allowance response (micro-USDC or decimal string)."""
    if not isinstance(data, dict):
        return 0.0
    raw = data.get("balance") or data.get("available") or data.get("collateral") or "0"
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if val >= 1_000_000:
        return val / 1_000_000.0
    if val > 1_000 and val < 1_000_000:
        return val / 1_000_000.0
    return val
