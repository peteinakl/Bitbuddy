"""Moving-average trend following.

`MaTrend` is a single parameterised family covering the useful variants: with or
without a confirmation cross, with or without volatility targeting, fast or slow
exit line. Keeping them one class means the search explores a continuum rather
than a handful of hand-written special cases.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from bitbuddy.data import BARS_PER_YEAR
from bitbuddy.engine import Entry
from bitbuddy.indicators import atr, ema, realized_vol, slope
from bitbuddy.strategies.base import Strategy, finite, vol_target_size


class MaTrend(Strategy):
    """Long while the trend is up; flat otherwise.

    Entry requires close above the entry MA, optionally that a fast MA sits
    above a slow MA (`confirm`), and optionally a rising slope. Exit is a close
    below the exit MA. A wide ATR stop covers gap-down crashes only -- the exit
    line does the routine work, and a tight stop on a trend system converts
    winners into losers.
    """

    def __init__(
        self,
        entry_ma: int = 50,
        exit_ma: int = 50,
        slow_ma: int = 200,
        confirm: bool = True,
        require_slope: bool = False,
        slope_lookback: int = 10,
        atr_period: int = 14,
        stop_atr: float = 8.0,
        target_vol: Optional[float] = 0.50,
        vol_window: int = 30,
        max_size: float = 0.95,
        min_size: float = 0.10,
        interval: str = "1d",
    ):
        self.entry_ma = entry_ma
        self.exit_ma = exit_ma
        self.slow_ma = slow_ma
        self.confirm = confirm
        self.require_slope = require_slope
        self.slope_lookback = slope_lookback
        self.atr_period = atr_period
        self.stop_atr = stop_atr
        self.target_vol = target_vol
        self.vol_window = vol_window
        self.max_size = max_size
        self.min_size = min_size
        self.interval = interval
        self.warmup = max(entry_ma, exit_ma, slow_ma if confirm else 0,
                          vol_window, atr_period) + 5

    @property
    def name(self) -> str:
        bits = [f"MaTrend {self.entry_ma}/{self.exit_ma}"]
        if self.confirm:
            bits.append(f"x{self.slow_ma}")
        if self.target_vol:
            bits.append(f"vt{self.target_vol:g}")
        return " ".join(bits)

    def prepare(self, bars):
        close, high, low = bars["close"], bars["high"], bars["low"]
        bpy = BARS_PER_YEAR.get(self.interval, 365.0)
        f = {
            "atr": atr(high, low, close, self.atr_period),
            "ma_entry": ema(close, self.entry_ma),
            "ma_exit": ema(close, self.exit_ma),
            "rv": realized_vol(close, self.vol_window, bpy),
        }
        f["ma_slow"] = ema(close, self.slow_ma) if self.confirm else f["ma_entry"]
        f["slope"] = slope(f["ma_entry"], self.slope_lookback)
        return f

    def entry_signal(self, ctx, i: int, equity: float) -> Optional[Entry]:
        price = float(ctx.close[i])
        a = ctx.feats["atr"][i]
        me, mx = ctx.feats["ma_entry"][i], ctx.feats["ma_exit"][i]
        if not finite(a, me) or a <= 0:
            return None
        if price <= me:
            return None

        if self.confirm:
            ms = ctx.feats["ma_slow"][i]
            if not finite(ms) or price <= ms or me <= ms:
                return None
        if self.require_slope:
            s = ctx.feats["slope"][i]
            if not finite(s) or s <= 0:
                return None
        # Do not open into a position the exit rule would close immediately
        if finite(mx) and price <= mx:
            return None

        stop = price - self.stop_atr * a
        if stop <= 0:
            return None

        size = (vol_target_size(ctx.feats["rv"][i], self.target_vol,
                                self.max_size, self.min_size)
                if self.target_vol else self.max_size)
        return Entry(stop_price=stop, size_frac=size, tag="matrend")

    def exit_signal(self, ctx, i: int, position) -> Optional[str]:
        price = float(ctx.close[i])
        mx = ctx.feats["ma_exit"][i]
        if finite(mx) and price < mx:
            return "MA_EXIT"
        if self.confirm:
            ms, me = ctx.feats["ma_slow"][i], ctx.feats["ma_entry"][i]
            if finite(ms, me) and me < ms and price < ms:
                return "REGIME_FLIP"
        return None
