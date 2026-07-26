"""Time-series momentum.

The classic cross-asset anomaly: hold the asset when its own trailing return is
positive. It has held up across decades and asset classes, which makes it a
better prior than anything fitted to one BTC sample, and it is nearly
parameter-free -- a single lookback.

`DualMomentum` adds a slow regime filter on top, testing whether the two signals
are complementary or redundant.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from bitbuddy.data import BARS_PER_YEAR
from bitbuddy.engine import Entry
from bitbuddy.indicators import atr, ema, realized_vol, total_return
from bitbuddy.strategies.base import Strategy, finite, vol_target_size


class TsMomentum(Strategy):
    """Long while trailing return over `lookback` bars exceeds `entry_thresh`."""

    def __init__(
        self,
        lookback: int = 90,
        entry_thresh: float = 0.0,
        exit_thresh: float = 0.0,
        atr_period: int = 14,
        stop_atr: float = 8.0,
        target_vol: Optional[float] = 0.50,
        vol_window: int = 30,
        max_size: float = 0.95,
        min_size: float = 0.10,
        interval: str = "1d",
    ):
        self.lookback = lookback
        self.entry_thresh = entry_thresh
        self.exit_thresh = exit_thresh
        self.atr_period = atr_period
        self.stop_atr = stop_atr
        self.target_vol = target_vol
        self.vol_window = vol_window
        self.max_size = max_size
        self.min_size = min_size
        self.interval = interval
        self.warmup = max(lookback, vol_window, atr_period) + 5

    @property
    def name(self) -> str:
        vt = f" vt{self.target_vol:g}" if self.target_vol else ""
        return f"TsMom {self.lookback}{vt}"

    def prepare(self, bars):
        close, high, low = bars["close"], bars["high"], bars["low"]
        bpy = BARS_PER_YEAR.get(self.interval, 365.0)
        return {
            "mom": total_return(close, self.lookback),
            "atr": atr(high, low, close, self.atr_period),
            "rv": realized_vol(close, self.vol_window, bpy),
        }

    def entry_signal(self, ctx, i: int, equity: float) -> Optional[Entry]:
        m, a = ctx.feats["mom"][i], ctx.feats["atr"][i]
        if not finite(m, a) or a <= 0:
            return None
        if m <= self.entry_thresh:
            return None
        price = float(ctx.close[i])
        stop = price - self.stop_atr * a
        if stop <= 0:
            return None
        size = (vol_target_size(ctx.feats["rv"][i], self.target_vol,
                                self.max_size, self.min_size)
                if self.target_vol else self.max_size)
        return Entry(stop_price=stop, size_frac=size, tag="tsmom")

    def exit_signal(self, ctx, i: int, position) -> Optional[str]:
        m = ctx.feats["mom"][i]
        if finite(m) and m < self.exit_thresh:
            return "MOM_EXIT"
        return None


class DualMomentum(Strategy):
    """Momentum gated by a slow moving-average regime filter.

    Requires both trailing return positive *and* price above a slow MA. Tests
    whether combining the two filters beats either alone, or whether they are
    measuring the same thing.
    """

    def __init__(
        self,
        lookback: int = 90,
        slow_ma: int = 200,
        exit_ma: int = 50,
        atr_period: int = 14,
        stop_atr: float = 8.0,
        target_vol: Optional[float] = 0.50,
        vol_window: int = 30,
        max_size: float = 0.95,
        min_size: float = 0.10,
        interval: str = "1d",
    ):
        self.lookback = lookback
        self.slow_ma = slow_ma
        self.exit_ma = exit_ma
        self.atr_period = atr_period
        self.stop_atr = stop_atr
        self.target_vol = target_vol
        self.vol_window = vol_window
        self.max_size = max_size
        self.min_size = min_size
        self.interval = interval
        self.warmup = max(lookback, slow_ma, exit_ma, vol_window, atr_period) + 5

    @property
    def name(self) -> str:
        return f"DualMom {self.lookback}/{self.slow_ma}/{self.exit_ma}"

    def prepare(self, bars):
        close, high, low = bars["close"], bars["high"], bars["low"]
        bpy = BARS_PER_YEAR.get(self.interval, 365.0)
        return {
            "mom": total_return(close, self.lookback),
            "ma_slow": ema(close, self.slow_ma),
            "ma_exit": ema(close, self.exit_ma),
            "atr": atr(high, low, close, self.atr_period),
            "rv": realized_vol(close, self.vol_window, bpy),
        }

    def entry_signal(self, ctx, i: int, equity: float) -> Optional[Entry]:
        price = float(ctx.close[i])
        m, ms, mx = ctx.feats["mom"][i], ctx.feats["ma_slow"][i], ctx.feats["ma_exit"][i]
        a = ctx.feats["atr"][i]
        if not finite(m, ms, a) or a <= 0:
            return None
        if m <= 0 or price <= ms:
            return None
        if finite(mx) and price <= mx:
            return None
        stop = price - self.stop_atr * a
        if stop <= 0:
            return None
        size = (vol_target_size(ctx.feats["rv"][i], self.target_vol,
                                self.max_size, self.min_size)
                if self.target_vol else self.max_size)
        return Entry(stop_price=stop, size_frac=size, tag="dualmom")

    def exit_signal(self, ctx, i: int, position) -> Optional[str]:
        price = float(ctx.close[i])
        m, ms, mx = ctx.feats["mom"][i], ctx.feats["ma_slow"][i], ctx.feats["ma_exit"][i]
        if finite(mx) and price < mx:
            return "MA_EXIT"
        if finite(m) and m < 0 and finite(ms) and price < ms:
            return "MOM_EXIT"
        return None
