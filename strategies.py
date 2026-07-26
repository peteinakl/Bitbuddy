"""Trading strategies for the backtest engine.

Two implementations:

`LegacyBotStrategy` is a faithful port of what bitcoin_trading_bot.py does today
(1-hour summed momentum, 5-bar uptrend confirmation, fixed 1.5% target / -0.8%
stop, 30% scale-out then a 0.5% trail). It exists to measure the current design
rather than argue about it.

`TrendAtrStrategy` is the redesign. Its four departures from the legacy rules,
in descending order of expected impact:

1. **Regime filter.** Long only while price holds above a slow EMA. The legacy
   bot has no concept of a bear market and will keep buying dips all the way
   down; BTC fell 54% inside this dataset.

2. **Volatility-scaled exits.** Stops and trails are multiples of ATR, not fixed
   percentages. A flat -0.8% stop is ~1.6x hourly sigma, so in normal conditions
   noise alone closes the position; in calm markets it is needlessly wide.

3. **No profit cap.** Trend following earns its money from a small number of
   large moves. A fixed 1.5% target mathematically forbids ever capturing a 40%
   run, while the -0.8% stop still lets losses through. Replaced with a
   chandelier (ATR trailing) exit that rides winners.

4. **Fixed-fractional risk sizing.** Quantity derives from stop distance so each
   trade risks the same fraction of equity, instead of a flat 30-50% of cash
   that ignores how far away the stop is.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from backtest import Entry


# ---------------------------------------------------------------------------
# indicators
# ---------------------------------------------------------------------------
def atr(df: pd.DataFrame, period: int) -> pd.Series:
    """Wilder's Average True Range."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


# ---------------------------------------------------------------------------
# baseline: what the bot does today
# ---------------------------------------------------------------------------
class LegacyBotStrategy:
    """Port of the current bitcoin_trading_bot.py entry/exit rules.

    Mirrors analyze_market_momentum() (sum of pct changes over the last 12
    samples), analyze_market_condition() (20-minute return bucketed by
    scalp_threshold), and the gates in _get_trade_decision().
    """

    name = "legacy (current bot)"

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

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        close = df["close"]

        # analyze_market_momentum: sum of successive pct changes over the window
        pct = close.pct_change(fill_method=None)
        df["mom_sum"] = pct.rolling(self.momentum_window - 1).sum()

        # analyze_market_condition: 20-minute (5-bar) return drives the bucket
        df["short_mom"] = close.pct_change(4, fill_method=None)
        df["ema_fast"] = close.rolling(5).mean()
        df["ema_med"] = close.rolling(10).mean()
        df["ema_slow"] = close.rolling(self.momentum_window).mean()

        df["hour"] = df["timestamp"].dt.hour
        return df

    def _condition(self, row) -> str:
        sm = row["short_mom"]
        if sm > self.scalp_threshold * 2.0:
            if row["ema_fast"] > row["ema_med"] > row["ema_slow"]:
                return "STRONG_BULLISH"
            return "BULLISH"
        if sm < -self.scalp_threshold * 2.0:
            if row["ema_fast"] < row["ema_med"] < row["ema_slow"]:
                return "STRONG_BEARISH"
            return "BEARISH"
        if abs(sm) < self.scalp_threshold * 0.5:
            return "RANGING"
        return "NEUTRAL"

    def entry_signal(self, df: pd.DataFrame, i: int, equity: float) -> Optional[Entry]:
        row = df.iloc[i]
        if np.isnan(row["mom_sum"]) or np.isnan(row["ema_slow"]):
            return None

        # _validate_entry_conditions: skip the quiet overnight window
        if self.skip_quiet_hours and 2 <= row["hour"] < 6:
            return None

        if self._condition(row) not in ("BULLISH", "STRONG_BULLISH", "RANGING"):
            return None

        # Momentum gate: direction up and strength above threshold
        mom = row["mom_sum"]
        if mom <= 0 or abs(mom) < self.momentum_threshold:
            return None

        # 5-price uptrend confirmation
        if df["close"].iloc[i] <= df["close"].iloc[i - 4]:
            return None

        price = float(row["close"])
        # Legacy sizing: 30% of cash scaled up to 2x by momentum, capped at 50%
        mult = min(2.0, 1.0 + abs(mom) / self.momentum_threshold)
        size_frac = min(0.30 * mult, 0.50)

        return Entry(
            stop_price=price * (1 - self.stop_loss),
            target_price=price * (1 + self.profit_target),
            size_frac=size_frac,
            partial_frac=self.partial_frac,
            trail_dist=price * self.trailing_stop,
            tag="legacy",
        )


# ---------------------------------------------------------------------------
# redesign: trend-following with volatility-scaled risk
# ---------------------------------------------------------------------------
class TrendAtrStrategy:
    """Donchian breakout, filtered by a slow-EMA regime, exited on an ATR trail.

    Entry:  close breaks the highest close of the prior `breakout_window` bars
            AND close > EMA(`trend_window`)  (regime filter)
            AND ATR/price is inside a sane band (skip dead and berserk markets)
    Stop:   entry - `stop_atr` * ATR
    Exit:   chandelier -- trail `trail_atr` * ATR below the highest close seen
    Size:   risk `risk_frac` of equity to the initial stop
    """

    name = "trend + ATR"

    def __init__(
        self,
        breakout_window: int = 50,
        trend_window: int = 200,
        atr_period: int = 24,
        stop_atr: float = 2.5,
        trail_atr: float = 4.0,
        risk_frac: float = 0.02,
        max_atr_pct: float = 0.05,
        min_atr_pct: float = 0.0015,
        max_bars_held: Optional[int] = None,
        require_slope: bool = True,
    ):
        self.breakout_window = breakout_window
        self.trend_window = trend_window
        self.atr_period = atr_period
        self.stop_atr = stop_atr
        self.trail_atr = trail_atr
        self.risk_frac = risk_frac
        self.max_atr_pct = max_atr_pct
        self.min_atr_pct = min_atr_pct
        self.max_bars_held = max_bars_held
        self.require_slope = require_slope
        self.warmup = max(trend_window, breakout_window, atr_period) + 5

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        close = df["close"]
        df["atr"] = atr(df, self.atr_period)
        df["ema_trend"] = ema(close, self.trend_window)
        # shift(1): the breakout level must exclude the current bar, otherwise
        # every bar trivially "breaks out" of a window containing itself
        df["donchian_hi"] = close.rolling(self.breakout_window).max().shift(1)
        df["ema_slope"] = df["ema_trend"].diff(self.trend_window // 4)
        df["atr_pct"] = df["atr"] / close
        return df

    def entry_signal(self, df: pd.DataFrame, i: int, equity: float) -> Optional[Entry]:
        row = df.iloc[i]
        price = float(row["close"])
        a = row["atr"]

        if not np.isfinite(a) or a <= 0:
            return None
        if not np.isfinite(row["donchian_hi"]) or not np.isfinite(row["ema_trend"]):
            return None

        # regime filter
        if price <= row["ema_trend"]:
            return None
        if self.require_slope and not (np.isfinite(row["ema_slope"]) and row["ema_slope"] > 0):
            return None

        # volatility sanity band
        if not (self.min_atr_pct <= row["atr_pct"] <= self.max_atr_pct):
            return None

        # breakout trigger
        if price <= row["donchian_hi"]:
            return None

        stop = price - self.stop_atr * a
        if stop <= 0:
            return None

        return Entry(
            stop_price=stop,
            target_price=None,          # no cap: let the trail decide
            risk_frac=self.risk_frac,
            trail_trigger=price,        # trail is live immediately
            trail_dist=self.trail_atr * a,
            tag="trend_atr",
        )


class RegimeTrendStrategy:
    """Long/flat moving-average trend following with a catastrophic ATR stop.

    The thesis this tests: on BTC almost all of the achievable edge is in
    *participation control* -- being long during sustained uptrends and flat
    during sustained downtrends -- not in timing individual entries. So this
    holds a single position for as long as the trend persists and trades rarely,
    which also makes costs almost irrelevant.

    Entry: close > EMA(entry_ma) and that EMA is rising
    Exit:  close < EMA(exit_ma)   (evaluated on the close, filled next open)
    Stop:  a wide ATR stop that only fires on a genuine crash
    """

    name = "regime trend"

    def __init__(
        self,
        entry_ma: int = 50,
        exit_ma: int = 100,
        atr_period: int = 14,
        stop_atr: float = 6.0,
        risk_frac: float = 0.02,
        size_frac: Optional[float] = 0.95,
        slope_lookback: int = 10,
        require_slope: bool = True,
    ):
        self.entry_ma = entry_ma
        self.exit_ma = exit_ma
        self.atr_period = atr_period
        self.stop_atr = stop_atr
        self.risk_frac = risk_frac
        self.size_frac = size_frac
        self.slope_lookback = slope_lookback
        self.require_slope = require_slope
        self.warmup = max(entry_ma, exit_ma, atr_period) + 5

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        close = df["close"]
        df["atr"] = atr(df, self.atr_period)
        df["ma_entry"] = ema(close, self.entry_ma)
        df["ma_exit"] = ema(close, self.exit_ma)
        df["ma_slope"] = df["ma_entry"].diff(self.slope_lookback)
        return df

    def entry_signal(self, df: pd.DataFrame, i: int, equity: float) -> Optional[Entry]:
        row = df.iloc[i]
        price = float(row["close"])
        a = row["atr"]
        if not np.isfinite(a) or a <= 0 or not np.isfinite(row["ma_entry"]):
            return None
        if price <= row["ma_entry"]:
            return None
        if self.require_slope and not (np.isfinite(row["ma_slope"]) and row["ma_slope"] > 0):
            return None
        # Only take a fresh long if we are above the exit line too, otherwise we
        # would enter into a position the exit rule wants closed immediately
        if np.isfinite(row["ma_exit"]) and price <= row["ma_exit"]:
            return None

        stop = price - self.stop_atr * a
        if stop <= 0:
            return None
        return Entry(
            stop_price=stop,
            target_price=None,
            risk_frac=self.risk_frac,
            size_frac=self.size_frac,
            tag="regime",
        )

    def exit_signal(self, df: pd.DataFrame, i: int, position) -> Optional[str]:
        row = df.iloc[i]
        if not np.isfinite(row["ma_exit"]):
            return None
        if float(row["close"]) < row["ma_exit"]:
            return "REGIME_EXIT"
        return None


class VolTargetTrendStrategy:
    """Trend participation with a hard regime gate and volatility-targeted size.

    Three ideas stacked, each addressing a specific failure seen in testing:

    1. **Two-condition regime gate** (price > slow EMA *and* fast EMA > slow EMA).
       A single price-vs-MA test whipsaws repeatedly during a bear market: price
       pokes above the line, we buy, it fails, we stop out. Requiring the MA
       structure itself to be bullish keeps the bot genuinely flat through
       sustained downtrends instead of bleeding on failed re-entries.

    2. **Volatility targeting.** Position size is scaled so the position's
       expected volatility hits `target_vol`, capped at `max_size_frac` since
       spot cannot use leverage. BTC's realized vol ranges roughly 25-100%
       annualized; holding constant notional across that range means wildly
       inconsistent risk per unit of capital.

    3. **Wide ATR disaster stop only.** The regime exit does the routine work;
       the stop exists purely for gap-down crashes, so it sits far away and
       rarely fires. Tight stops on a trend system convert winners into losers.
    """

    name = "vol-target trend"

    def __init__(
        self,
        fast_ma: int = 50,
        slow_ma: int = 200,
        exit_ma: int = 50,
        atr_period: int = 14,
        stop_atr: float = 8.0,
        vol_window: int = 30,
        target_vol: float = 0.50,
        max_size_frac: float = 0.95,
        min_size_frac: float = 0.10,
        bars_per_year: float = 365.0,
        require_confirm: bool = True,
    ):
        self.fast_ma = fast_ma
        self.slow_ma = slow_ma
        self.exit_ma = exit_ma
        self.atr_period = atr_period
        self.stop_atr = stop_atr
        self.vol_window = vol_window
        self.target_vol = target_vol
        self.max_size_frac = max_size_frac
        self.min_size_frac = min_size_frac
        self.bars_per_year = bars_per_year
        self.require_confirm = require_confirm
        self.warmup = max(slow_ma, fast_ma, exit_ma, vol_window, atr_period) + 5

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        close = df["close"]
        df["atr"] = atr(df, self.atr_period)
        df["ma_fast"] = ema(close, self.fast_ma)
        df["ma_slow"] = ema(close, self.slow_ma)
        df["ma_exit"] = ema(close, self.exit_ma)
        ret = close.pct_change(fill_method=None)
        df["realized_vol"] = ret.rolling(self.vol_window).std() * np.sqrt(self.bars_per_year)
        return df

    def _size(self, row) -> float:
        rv = row["realized_vol"]
        if not np.isfinite(rv) or rv <= 0:
            return self.min_size_frac
        return float(np.clip(self.target_vol / rv, self.min_size_frac, self.max_size_frac))

    def entry_signal(self, df: pd.DataFrame, i: int, equity: float) -> Optional[Entry]:
        row = df.iloc[i]
        price = float(row["close"])
        a = row["atr"]
        if not np.isfinite(a) or a <= 0:
            return None
        if not np.isfinite(row["ma_slow"]) or not np.isfinite(row["ma_fast"]):
            return None

        # Regime gate
        if price <= row["ma_slow"]:
            return None
        if self.require_confirm and row["ma_fast"] <= row["ma_slow"]:
            return None
        if np.isfinite(row["ma_exit"]) and price <= row["ma_exit"]:
            return None

        stop = price - self.stop_atr * a
        if stop <= 0:
            return None

        return Entry(
            stop_price=stop,
            target_price=None,
            size_frac=self._size(row),
            tag="voltarget",
        )

    def exit_signal(self, df: pd.DataFrame, i: int, position) -> Optional[str]:
        row = df.iloc[i]
        price = float(row["close"])
        # Leave on either a break of the exit line or a regime flip
        if np.isfinite(row["ma_exit"]) and price < row["ma_exit"]:
            return "REGIME_EXIT"
        if self.require_confirm and np.isfinite(row["ma_slow"]) and np.isfinite(row["ma_fast"]):
            if row["ma_fast"] < row["ma_slow"] and price < row["ma_slow"]:
                return "REGIME_FLIP"
        return None
