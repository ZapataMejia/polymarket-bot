"""Fair-value pricer for 'Up or Down' binary markets.

For a market that resolves UP iff S(T_end) >= S(T_start), with strike K := S(T_start) fixed
once the window opens, the fair probability at time t (T_start <= t <= T_end) is

    p_up_fair(t) = P( S(T_end) >= K | S(t), sigma )

Under a driftless log-normal model (martingale; we assume drift ~0 over <=1h horizons
since funding/risk-free rates are tiny compared to crypto volatility):

    ln( S(T_end) / S(t) ) ~ Normal( -0.5 * sigma^2 * tau, sigma^2 * tau )

so

    P( S(T_end) >= K ) = Phi( ( ln(S(t)/K) + 0.5*sigma^2*tau ) / (sigma * sqrt(tau)) )
                      ≈ Phi(  ln(S(t)/K)              / (sigma * sqrt(tau)) )   for small sigma*sqrt(tau)

We keep the full term but it barely matters at 5-15 minute horizons.

`sigma` is the per-second log-return standard deviation, estimated from a rolling
window of Binance returns just before t.
"""
from __future__ import annotations

import math


def _phi(x: float) -> float:
    """Standard normal CDF via erf, no scipy dependency."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def fair_prob_up(
    s_now: float,
    strike: float,
    sigma_per_sec: float,
    seconds_remaining: float,
) -> float:
    """Probability that S(T_end) >= strike given S(t) = s_now, with `seconds_remaining` left.

    Edge cases:
    - If seconds_remaining <= 0: return 1.0 if s_now>=strike else 0.0 (deterministic).
    - If sigma_per_sec <= 0 or any input is non-positive: collapse to deterministic.
    """
    if s_now <= 0 or strike <= 0:
        return 0.5
    if seconds_remaining <= 0:
        return 1.0 if s_now >= strike else 0.0
    if sigma_per_sec <= 0:
        return 1.0 if s_now >= strike else 0.0

    std = sigma_per_sec * math.sqrt(seconds_remaining)
    if std <= 0:
        return 1.0 if s_now >= strike else 0.0

    log_moneyness = math.log(s_now / strike)
    d = (log_moneyness + 0.5 * std * std) / std
    return _phi(d)


def estimate_sigma_per_sec(log_returns: list[float], samples_per_sec: float) -> float:
    """Estimate per-second log-return stdev from a series of log returns.

    `samples_per_sec` is the sample frequency (e.g. 1/60 for 1-min returns).
    sigma_per_sec = stdev(returns) * sqrt(samples_per_sec).
    """
    n = len(log_returns)
    if n < 2:
        return 0.0
    mean = sum(log_returns) / n
    var = sum((r - mean) ** 2 for r in log_returns) / (n - 1)
    sd = math.sqrt(max(var, 0.0))
    return sd * math.sqrt(samples_per_sec)
