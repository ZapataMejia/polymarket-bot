from __future__ import annotations

import numpy as np
import pandas as pd


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(window=period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> pd.DataFrame:
    fast_ema = ema(series, fast)
    slow_ema = ema(series, slow)
    macd_line = fast_ema - slow_ema
    signal_line = ema(macd_line, signal_period)
    histogram = macd_line - signal_line
    return pd.DataFrame({
        "macd": macd_line,
        "macd_signal": signal_line,
        "macd_histogram": histogram,
    })


def bollinger_bands(series: pd.Series, period: int = 20, std_dev: float = 2.0) -> pd.DataFrame:
    middle = series.rolling(window=period).mean()
    rolling_std = series.rolling(window=period).std()
    upper = middle + (rolling_std * std_dev)
    lower = middle - (rolling_std * std_dev)
    width = (upper - lower) / middle
    pct_b = (series - lower) / (upper - lower)
    return pd.DataFrame({
        "bb_upper": upper,
        "bb_middle": middle,
        "bb_lower": lower,
        "bb_width": width,
        "bb_pct_b": pct_b,
    })


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return true_range.rolling(window=period).mean()


def obv(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df["close"].diff())
    return (direction * df["volume"]).cumsum()


def vwap(df: pd.DataFrame) -> pd.Series:
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    cum_vol = df["volume"].cumsum()
    cum_tp_vol = (typical_price * df["volume"]).cumsum()
    return cum_tp_vol / cum_vol


def hurst_exponent(series: pd.Series, max_lag: int = 100) -> float:
    """Estimate Hurst exponent via R/S analysis. >0.5 trending, <0.5 mean-reverting."""
    vals = series.dropna().values
    if len(vals) < max_lag * 2:
        return 0.5

    lags = range(2, max_lag)
    rs_values = []

    for lag in lags:
        subseries = [vals[i * lag:(i + 1) * lag] for i in range(len(vals) // lag)]
        if len(subseries) < 2:
            break

        rs_list = []
        for sub in subseries:
            if len(sub) < 2:
                continue
            mean = np.mean(sub)
            deviations = sub - mean
            cumdev = np.cumsum(deviations)
            r = np.max(cumdev) - np.min(cumdev)
            s = np.std(sub, ddof=1)
            if s > 0:
                rs_list.append(r / s)

        if rs_list:
            rs_values.append((np.log(lag), np.log(np.mean(rs_list))))

    if len(rs_values) < 2:
        return 0.5

    x = np.array([v[0] for v in rs_values])
    y = np.array([v[1] for v in rs_values])
    slope = np.polyfit(x, y, 1)[0]
    return float(np.clip(slope, 0, 1))


def realized_volatility(series: pd.Series, period: int = 20) -> pd.Series:
    log_returns = np.log(series / series.shift(1))
    return log_returns.rolling(window=period).std() * np.sqrt(252)


def order_flow_imbalance(df: pd.DataFrame) -> pd.Series:
    """Approximate buy/sell pressure from price and volume."""
    price_change = df["close"].diff()
    buy_vol = df["volume"].where(price_change > 0, 0)
    sell_vol = df["volume"].where(price_change < 0, 0)
    total = buy_vol + sell_vol
    return ((buy_vol - sell_vol) / total.replace(0, np.nan)).fillna(0)
