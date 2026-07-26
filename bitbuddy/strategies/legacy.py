"""Port of the original bitcoin_trading_bot.py rules.

Kept so the current design can be measured against what it replaced rather than
against a strawman. Mirrors analyze_market_momentum() (sum of pct changes over
12 samples), analyze_market_condition() (20-minute return bucketed by
scalp_threshold) and the gates in _get_trade_decision().

Backtests to -99.2% over 2023-2026 on 5m bars.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from bitbuddy.engine import Entry
from bitbuddy.strategies.base import Strategy, finite


class LegacyBot(Strategy):
    name = "legacy (original bot)"

    def __init__(
        self,
        momentum_window: int = 12,
        momentum_threshold: float = 0.0005,
        scalp_threshold: float = 0.001,
        profit_target: float = 0.015,
        stop_loss: float = 0.008,
        trailing_stop: float = 0.005,
        partial_frac: float = 0.30,
        skip_quiet_hours: bool = True,
    ):
        self.momentum_window = momentum_window
        self.momentum_threshold = momentum_threshold
        self.scalp_threshold = scalp_threshold
        self.profit_target = profit_target
        self.stop_loss = stop_loss
        self.trailing_stop = trailing_stop
        self.partial_frac = partial_frac
        self.skip_quiet_hours = skip_quiet_hours
        self.warmup = momentum_window + 2

    def prepare(self, bars):
        close = bars["close"]
        pct = close.pct_change(fill_method=None)
        return {
            "mom_sum": pct.rolling(self.momentum_window - 1).sum().to_numpy(dtype=float),
            "short_mom": close.pct_change(4, fill_method=None).to_numpy(dtype=float),
            "ema_fast": close.rolling(5).mean().to_numpy(dtype=float),
            "ema_med": close.rolling(10).mean().to_numpy(dtype=float),
            "ema_slow": close.rolling(self.momentum_window).mean().to_numpy(dtype=float),
            "hour": pd.DatetimeIndex(bars["timestamp"]).hour.to_numpy(),
            "close_lag4": close.shift(4).to_numpy(dtype=float),
        }

    def _condition(self, ctx, i: int) -> str:
        sm = ctx.feats["short_mom"][i]
        f, m, s = ctx.feats["ema_fast"][i], ctx.feats["ema_med"][i], ctx.feats["ema_slow"][i]
        if sm > self.scalp_threshold * 2.0:
            return "STRONG_BULLISH" if f > m > s else "BULLISH"
        if sm < -self.scalp_threshold * 2.0:
            return "STRONG_BEARISH" if f < m < s else "BEARISH"
        if abs(sm) < self.scalp_threshold * 0.5:
            return "RANGING"
        return "NEUTRAL"

    def entry_signal(self, ctx, i: int, equity: float) -> Optional[Entry]:
        mom = ctx.feats["mom_sum"][i]
        if not finite(mom, ctx.feats["ema_slow"][i]):
            return None
        if self.skip_quiet_hours and 2 <= ctx.feats["hour"][i] < 6:
            return None
        if self._condition(ctx, i) not in ("BULLISH", "STRONG_BULLISH", "RANGING"):
            return None
        if mom <= 0 or abs(mom) < self.momentum_threshold:
            return None
        lag = ctx.feats["close_lag4"][i]
        price = float(ctx.close[i])
        if not finite(lag) or price <= lag:
            return None

        mult = min(2.0, 1.0 + abs(mom) / self.momentum_threshold)
        return Entry(
            stop_price=price * (1 - self.stop_loss),
            target_price=price * (1 + self.profit_target),
            size_frac=min(0.30 * mult, 0.50),
            partial_frac=self.partial_frac,
            trail_dist=price * self.trailing_stop,
            tag="legacy",
        )
