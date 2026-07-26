"""Indicator primitives.

All functions are **causal**: the value at index i uses only data up to and
including i. Anything else is lookahead, which silently inflates every result
downstream.

Computation uses pandas (vectorised, done once in prepare()) but every function
returns a numpy array, because the backtest hot loop indexes these hundreds of
thousands of times and pandas scalar access is roughly 50x slower.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _arr(s: pd.Series) -> np.ndarray:
    return s.to_numpy(dtype=float)


def ema(close: pd.Series, period: int) -> np.ndarray:
    return _arr(close.ewm(span=period, adjust=False, min_periods=period).mean())


def sma(close: pd.Series, period: int) -> np.ndarray:
    return _arr(close.rolling(period).mean())


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> np.ndarray:
    """Wilder's Average True Range."""
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    return _arr(tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean())


def rolling_max(close: pd.Series, period: int, exclude_current: bool = True) -> np.ndarray:
    """Highest value over the window.

    exclude_current shifts by one bar. A breakout level that includes the current
    bar is trivially broken by that bar, which produces a signal on every bar.
    """
    out = close.rolling(period).max()
    if exclude_current:
        out = out.shift(1)
    return _arr(out)


def rolling_min(close: pd.Series, period: int, exclude_current: bool = True) -> np.ndarray:
    out = close.rolling(period).min()
    if exclude_current:
        out = out.shift(1)
    return _arr(out)


def realized_vol(close: pd.Series, window: int, bars_per_year: float) -> np.ndarray:
    """Annualised standard deviation of returns."""
    r = close.pct_change(fill_method=None)
    return _arr(r.rolling(window).std() * np.sqrt(bars_per_year))


def rsi(close: pd.Series, period: int = 14) -> np.ndarray:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = gain / loss.replace(0, np.nan)
    return _arr((100 - 100 / (1 + rs)).fillna(50))


def slope(values: np.ndarray, lookback: int) -> np.ndarray:
    """Change in a series over `lookback` bars. NaN-safe."""
    out = np.full_like(values, np.nan)
    if lookback < len(values):
        out[lookback:] = values[lookback:] - values[:-lookback]
    return out


def total_return(close: pd.Series, lookback: int) -> np.ndarray:
    """Return over `lookback` bars — the time-series-momentum signal."""
    return _arr(close.pct_change(lookback, fill_method=None))


def drawdown_from_peak(close: pd.Series, window: int) -> np.ndarray:
    """Current distance below the rolling peak, as a negative fraction."""
    peak = close.rolling(window, min_periods=1).max()
    return _arr(close / peak - 1.0)
