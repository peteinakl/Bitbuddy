"""Donchian channel breakout with an ATR chandelier exit.

Turtle-style: buy a new N-bar high, ride it with a trailing stop set a multiple
of ATR below the running high. Included in the search as a structurally
different entry mechanism from moving-average crossings -- it reacts to price
extremes rather than averages, so it enters earlier in a new trend and suffers
more false starts in chop.
"""

from __future__ import annotations

from typing import Optional

from bitbuddy.data import BARS_PER_YEAR
from bitbuddy.engine import Entry
from bitbuddy.indicators import atr, ema, realized_vol, rolling_max, rolling_min
from bitbuddy.strategies.base import Strategy, finite, vol_target_size


class Donchian(Strategy):
    """Enter on an N-bar breakout, exit on an ATR trail or an M-bar low."""

    def __init__(
        self,
        breakout: int = 50,
        exit_window: int = 25,
        trend_ma: Optional[int] = 200,
        atr_period: int = 14,
        stop_atr: float = 3.0,
        trail_atr: Optional[float] = 6.0,
        target_vol: Optional[float] = 0.50,
        vol_window: int = 30,
        max_size: float = 0.95,
        min_size: float = 0.10,
        interval: str = "1d",
    ):
        self.breakout = breakout
        self.exit_window = exit_window
        self.trend_ma = trend_ma
        self.atr_period = atr_period
        self.stop_atr = stop_atr
        self.trail_atr = trail_atr
        self.target_vol = target_vol
        self.vol_window = vol_window
        self.max_size = max_size
        self.min_size = min_size
        self.interval = interval
        self.warmup = max(breakout, exit_window, trend_ma or 0,
                          vol_window, atr_period) + 5

    @property
    def name(self) -> str:
        t = f"/ma{self.trend_ma}" if self.trend_ma else ""
        return f"Donchian {self.breakout}/{self.exit_window}{t}"

    def prepare(self, bars):
        close, high, low = bars["close"], bars["high"], bars["low"]
        bpy = BARS_PER_YEAR.get(self.interval, 365.0)
        f = {
            "hi": rolling_max(close, self.breakout),
            "lo": rolling_min(close, self.exit_window),
            "atr": atr(high, low, close, self.atr_period),
            "rv": realized_vol(close, self.vol_window, bpy),
        }
        if self.trend_ma:
            f["ma"] = ema(close, self.trend_ma)
        return f

    def entry_signal(self, ctx, i: int, equity: float) -> Optional[Entry]:
        price = float(ctx.close[i])
        hi, a = ctx.feats["hi"][i], ctx.feats["atr"][i]
        if not finite(hi, a) or a <= 0:
            return None
        if price <= hi:
            return None
        if self.trend_ma:
            ma = ctx.feats["ma"][i]
            if not finite(ma) or price <= ma:
                return None

        stop = price - self.stop_atr * a
        if stop <= 0:
            return None
        size = (vol_target_size(ctx.feats["rv"][i], self.target_vol,
                                self.max_size, self.min_size)
                if self.target_vol else self.max_size)
        return Entry(
            stop_price=stop,
            size_frac=size,
            trail_trigger=price if self.trail_atr else None,
            trail_dist=self.trail_atr * a if self.trail_atr else None,
            tag="donchian",
        )

    def exit_signal(self, ctx, i: int, position) -> Optional[str]:
        lo = ctx.feats["lo"][i]
        if finite(lo) and float(ctx.close[i]) < lo:
            return "CHANNEL_EXIT"
        return None
