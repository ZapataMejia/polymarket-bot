"""Core edge analyzer for Polymarket 'Up or Down' crypto markets.

Pipeline per market:
  1. Get Polymarket Up-token price history at 1-min fidelity for [window_start, window_end].
  2. Get Binance 1-min closes for the same window plus a vol-estimation pre-window.
  3. Estimate per-second sigma from the 60 minutes of returns BEFORE the window opens.
  4. Strike K = Binance close at window_start_utc (snapped to nearest minute).
  5. For each minute t in (window_start, window_end]:
       p_fair(t) = log-normal P( S(end) >= K | S(t), sigma )
       p_poly(t) = Polymarket "Up" mid (price of Up token, 0..1)
       edge(t)   = p_fair(t) - p_poly(t)
  6. Detect signal at first minute where |edge| > entry_threshold.
  7. Simulate execution at that minute:
       - Direction = sign(edge): BUY UP if edge>0, BUY DOWN if edge<0.
       - Effective fill = p_poly(t) + half-spread (we cross the spread on entry).
       - Position size = $1 per contract (=> upside = (1 - fill), downside = (-fill)).
       - At resolution: payoff = 1 if direction matches outcome, else 0.
       - PnL_per_$1_notional = (payoff_per_contract - fill) / fill   (% return on capital)
         or in absolute terms: payoff - fill, where you spent `fill` to buy one share.

We report BOTH:
  - "Naive edge" (no costs): would have generated PnL ignoring spread/slippage.
  - "Realistic" (half-spread crossed + fee): conservative honest estimate.
"""
from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pandas as pd

from src.polymarket.clob import ClobClient
from src.polymarket.gamma import GammaClient, UpDownMarket
from src.polymarket.pricing import estimate_sigma_per_sec, fair_prob_up

logger = logging.getLogger("trading.polymarket.edge")


@dataclass
class EdgeConfig:
    """Knobs for the edge analysis & simulator."""

    # Entry threshold on |p_fair - p_poly|. 0.05 = 5 percentage points.
    entry_threshold: float = 0.05
    # Skip very last second(s) of the window — at that point edge is trivially big
    # but unfillable; this is the "look-ahead" guardrail.
    min_seconds_to_resolution: int = 30
    # Round-trip cost we charge: half-spread on entry only (no exit since we hold to resolution).
    # Empirically Polymarket Up/Down crypto markets show ~1-3¢ bid-ask near the money.
    half_spread_cents: float = 1.5
    # Flat fee per fill in cents (gas/relayer overhead on Polygon).
    fee_cents: float = 0.5
    # Proportional TAKER fee on these crypto markets. Polymarket published 2% on
    # crypto "Up or Down" series at launch; the API exposes feeSchedule.rate=0.07
    # which we treat as a configurable override. We charge `fee_rate_pct` × fill.
    fee_rate_pct: float = 2.0
    # Rolling vol lookback before window opens, in minutes.
    vol_lookback_min: int = 60
    # Cap on sigma_per_sec to prevent crazy values from sparse data (annualised cap ~500%).
    max_sigma_per_sec: float = 0.001
    # Floor on sigma_per_sec to prevent degenerate p_fair = 0 or 1 forever.
    min_sigma_per_sec: float = 5e-5

    @property
    def half_spread(self) -> float:
        return self.half_spread_cents / 100.0

    @property
    def fee(self) -> float:
        return self.fee_cents / 100.0

    @property
    def fee_rate(self) -> float:
        return self.fee_rate_pct / 100.0


@dataclass
class TickRow:
    """Per-minute snapshot of the edge during a market window."""

    minute_in_window: int
    seconds_to_resolution: int
    binance_price: float
    strike: float
    p_fair_up: float
    p_poly_up: float
    edge_up: float  # p_fair - p_poly


@dataclass
class MarketResult:
    """Per-market analysis output."""

    market: UpDownMarket
    strike: float
    sigma_per_sec: float
    ticks: list[TickRow] = field(default_factory=list)
    # Signal (first tick crossing threshold within min_seconds_to_resolution constraint)
    signal_tick: TickRow | None = None
    signal_direction: str = "NONE"  # 'UP' / 'DOWN' / 'NONE'
    fill_price: float = 0.0
    realized_outcome: str = "UNKNOWN"
    correct: bool = False
    pnl_naive: float = 0.0     # ignoring costs, per $1 of stake
    pnl_realistic: float = 0.0  # half-spread + fee charged
    note: str = ""

    @property
    def has_signal(self) -> bool:
        return self.signal_tick is not None


def _outcome_from_prices(start_price: float, end_price: float) -> str:
    return "UP" if end_price >= start_price else "DOWN"


def _spot_at(df: pd.DataFrame, ts: datetime) -> tuple[datetime, float] | None:
    """Return (bar_timestamp, spot_price) at `ts` without look-ahead.

    CCXT 1-min bars are timestamped at their OPEN. So the spot price at instant `ts`
    (when `ts` is minute-aligned) is `bar[ts].open` — the price tick at the start of
    that minute. Using `bar[ts].close` would be a 60-second look-ahead.

    For instants between minute boundaries, returns the previous minute's close,
    which is the most recent observable price (no future information).
    """
    if df.empty:
        return None
    floored = ts.replace(second=0, microsecond=0)
    if ts == floored and floored in df.index:
        return floored, float(df.loc[floored, "open"])
    # Mid-minute: use previous minute's close = current price (no look-ahead).
    prev = floored if ts == floored else floored
    if prev in df.index:
        # prev minute's close is the latest tick before `ts` only if ts > prev's open.
        # When ts == prev exactly, prefer open (the very first tick of that minute).
        if ts > floored:
            return prev, float(df.loc[prev, "close"])  # close = price at floored+60s
        return prev, float(df.loc[prev, "open"])
    diffs = (df.index - floored).total_seconds().abs()
    if diffs.min() > 90:
        return None
    idx = diffs.argmin()
    row_ts = df.index[idx]
    return row_ts.to_pydatetime(), float(df.iloc[idx]["open"])


class EdgeAnalyzer:
    """Coordinates Gamma+CLOB+Binance fetches and runs the per-market analysis."""

    def __init__(
        self,
        gamma: GammaClient,
        clob: ClobClient,
        binance_fetch_minutes,
        config: EdgeConfig | None = None,
    ):
        """
        Args:
          binance_fetch_minutes: async callable
              (symbol: str, start_utc: datetime, end_utc: datetime) -> pd.DataFrame
              indexed by UTC timestamp with at least a 'close' column at 1-min resolution.
              We inject this so the analyzer doesn't depend on a specific exchange client.
        """
        self.gamma = gamma
        self.clob = clob
        self.fetch_klines = binance_fetch_minutes
        self.config = config or EdgeConfig()

    async def analyze_market(self, market: UpDownMarket) -> MarketResult:
        cfg = self.config
        # Need Binance data from (window_start - vol_lookback) to window_end.
        binance_start = market.window_start_utc - timedelta(minutes=cfg.vol_lookback_min + 2)
        binance_end = market.window_end_utc + timedelta(minutes=1)

        bdf = await self.fetch_klines(market.binance_symbol, binance_start, binance_end)
        poly_df = await self.clob.fetch_price_history(
            market.token_id_up, market.window_start_utc - timedelta(minutes=1), market.window_end_utc,
            fidelity_min=1,
        )

        result = MarketResult(market=market, strike=0.0, sigma_per_sec=0.0)

        if bdf.empty or "close" not in bdf.columns:
            result.note = "binance_empty"
            return result
        if poly_df.empty:
            result.note = "poly_empty"
            return result

        # Strike = spot price at window_start_utc (the bar's OPEN, no look-ahead).
        snap = _spot_at(bdf, market.window_start_utc)
        if snap is None:
            result.note = "no_strike_price"
            return result
        strike = snap[1]
        result.strike = strike

        # Sigma estimate from minutes in (binance_start, window_start).
        pre = bdf.loc[bdf.index < market.window_start_utc].tail(cfg.vol_lookback_min)
        if len(pre) < 5:
            result.note = "insufficient_vol_history"
            return result
        log_rets = pre["close"].apply(math.log).diff().dropna().tolist()
        sigma_min = estimate_sigma_per_sec(log_rets, samples_per_sec=1.0)  # treat as per-minute
        # Convert to per-second: stdev of 1-min returns over sqrt(60) gives per-sec stdev.
        sigma_per_sec = sigma_min / math.sqrt(60.0)
        sigma_per_sec = max(cfg.min_sigma_per_sec, min(cfg.max_sigma_per_sec, sigma_per_sec))
        result.sigma_per_sec = sigma_per_sec

        # Realized outcome from Binance (cross-check vs market.outcome).
        end_snap = _spot_at(bdf, market.window_end_utc)
        if end_snap is None:
            result.note = "no_end_price"
            return result
        realized = _outcome_from_prices(strike, end_snap[1])
        result.realized_outcome = realized
        # If Gamma reports a different outcome, trust Gamma but log.
        if market.outcome in ("UP", "DOWN") and market.outcome != realized:
            logger.debug(
                "Outcome mismatch for %s: gamma=%s binance=%s (strike=%.4f end=%.4f)",
                market.slug, market.outcome, realized, strike, end_snap[1],
            )
            realized = market.outcome
            result.realized_outcome = realized

        # Build per-minute tick table inside the window.
        first_minute = market.window_start_utc + timedelta(minutes=1)
        ticks: list[TickRow] = []
        t = first_minute
        idx = 1
        while t <= market.window_end_utc:
            snap_t = _spot_at(bdf, t)
            if snap_t is None:
                t = t + timedelta(minutes=1)
                idx += 1
                continue
            s_now = snap_t[1]
            seconds_remaining = max(0, int((market.window_end_utc - t).total_seconds()))
            p_fair = fair_prob_up(s_now, strike, sigma_per_sec, seconds_remaining)

            # Polymarket: take latest mid at or before t.
            mask = poly_df.index <= t
            if not mask.any():
                p_poly = 0.5  # before any quote, assume neutral
            else:
                p_poly = float(poly_df.loc[mask, "price"].iloc[-1])

            ticks.append(
                TickRow(
                    minute_in_window=idx,
                    seconds_to_resolution=seconds_remaining,
                    binance_price=s_now,
                    strike=strike,
                    p_fair_up=p_fair,
                    p_poly_up=p_poly,
                    edge_up=p_fair - p_poly,
                )
            )
            t = t + timedelta(minutes=1)
            idx += 1

        result.ticks = ticks

        # Pick first tick where |edge| > threshold AND seconds_to_resolution >= guardrail.
        for tk in ticks:
            if tk.seconds_to_resolution < cfg.min_seconds_to_resolution:
                continue
            if abs(tk.edge_up) >= cfg.entry_threshold:
                result.signal_tick = tk
                result.signal_direction = "UP" if tk.edge_up > 0 else "DOWN"
                # Effective fill: cross the spread on the direction we're buying.
                if result.signal_direction == "UP":
                    fill = min(1.0, tk.p_poly_up + cfg.half_spread)
                else:
                    fill = min(1.0, (1.0 - tk.p_poly_up) + cfg.half_spread)
                result.fill_price = fill
                payoff = 1.0 if result.signal_direction == realized else 0.0
                result.correct = payoff == 1.0
                # Naive: ignore spread + fee.
                naive_fill = tk.p_poly_up if result.signal_direction == "UP" else (1.0 - tk.p_poly_up)
                result.pnl_naive = payoff - naive_fill
                # Realistic: charge half-spread on entry + flat gas/relayer cents +
                # proportional taker fee charged on the fill price.
                prop_fee = fill * cfg.fee_rate
                result.pnl_realistic = payoff - fill - cfg.fee - prop_fee
                break

        if result.signal_tick is None:
            result.note = result.note or "no_signal"
        return result

    async def analyze_many(
        self,
        markets: list[UpDownMarket],
        concurrency: int = 4,
    ) -> list[MarketResult]:
        sem = asyncio.Semaphore(concurrency)

        async def run(m: UpDownMarket) -> MarketResult:
            async with sem:
                try:
                    return await self.analyze_market(m)
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning("Failed market %s: %s", m.slug, exc)
                    return MarketResult(market=m, strike=0.0, sigma_per_sec=0.0, note=f"error: {exc}")

        return await asyncio.gather(*(run(m) for m in markets))


def summarize(results: list[MarketResult]) -> pd.DataFrame:
    """Flatten per-market results into a tidy DataFrame for reporting."""
    rows = []
    for r in results:
        rows.append({
            "slug": r.market.slug,
            "asset": r.market.asset,
            "window_start": r.market.window_start_utc,
            "window_seconds": r.market.window_seconds,
            "volume_usd": r.market.volume_usd,
            "strike": r.strike,
            "sigma_per_sec": r.sigma_per_sec,
            "ticks": len(r.ticks),
            "signal": r.signal_direction if r.has_signal else "NONE",
            "signal_minute": r.signal_tick.minute_in_window if r.signal_tick else None,
            "signal_edge_up": r.signal_tick.edge_up if r.signal_tick else None,
            "p_poly_at_signal": r.signal_tick.p_poly_up if r.signal_tick else None,
            "p_fair_at_signal": r.signal_tick.p_fair_up if r.signal_tick else None,
            "fill_price": r.fill_price,
            "outcome": r.realized_outcome,
            "correct": r.correct if r.has_signal else None,
            "pnl_naive": r.pnl_naive if r.has_signal else 0.0,
            "pnl_realistic": r.pnl_realistic if r.has_signal else 0.0,
            "note": r.note,
        })
    return pd.DataFrame(rows)
