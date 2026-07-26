"""Event-driven backtest engine.

Honesty properties, each asserted in tests/test_engine.py:

1. **No lookahead.** A signal derived from bar i's close fills at bar i+1's open.
2. **Costs always charged**, with extra slippage on stop exits.
3. **Pessimistic intrabar ordering.** If a bar's range spans both stop and
   target, the stop is taken; without tick data you cannot know the order, so
   assume the worse one.
4. **No leverage.** The engine refuses to spend cash it does not hold.
5. **Conservation.** final equity == initial + sum of net P&L, exactly.

Performance: strategies receive a `Context` of numpy arrays rather than a
DataFrame. Pandas scalar access (`df.iloc[i]["col"]`) costs ~40us; array
indexing costs ~50ns. Over a 78k-bar series and a few thousand parameter
combinations that difference decides whether a search takes minutes or hours.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from bitbuddy.costs import Costs
from bitbuddy.metrics import Result, Trade


@dataclass
class Context:
    """Numpy views of the bar series plus whatever a strategy precomputed."""

    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    timestamp: pd.DatetimeIndex
    feats: Dict[str, np.ndarray] = field(default_factory=dict)
    interval: str = "1d"

    def __len__(self) -> int:
        return len(self.close)

    def f(self, name: str, i: int) -> float:
        """Feature value at bar i. NaN if absent."""
        a = self.feats.get(name)
        return float(a[i]) if a is not None else float("nan")


def make_context(bars: pd.DataFrame, strategy, interval: str = "1d") -> Context:
    """Build a Context from bars using a strategy's prepare().

    Shared by the backtester and the live trader so both feed strategies
    identically -- the live path cannot drift from what was validated.
    """
    return Context(
        open=bars["open"].to_numpy(dtype=float),
        high=bars["high"].to_numpy(dtype=float),
        low=bars["low"].to_numpy(dtype=float),
        close=bars["close"].to_numpy(dtype=float),
        timestamp=pd.DatetimeIndex(bars["timestamp"]),
        feats=strategy.prepare(bars),
        interval=interval,
    )


@dataclass
class Entry:
    """A strategy's request to open a long position."""

    stop_price: float
    target_price: Optional[float] = None
    risk_frac: float = 0.01           # fraction of equity risked to the stop
    size_frac: Optional[float] = None  # or: notional as a fraction of equity
    trail_trigger: Optional[float] = None
    trail_dist: Optional[float] = None
    partial_frac: float = 0.0
    tag: str = ""


@dataclass
class Position:
    entry_time: pd.Timestamp
    entry_price: float
    qty: float
    initial_qty: float
    stop_price: float
    initial_stop: float
    target_price: Optional[float]
    trail_trigger: Optional[float]
    trail_dist: Optional[float]
    partial_frac: float
    entry_bar: int
    entry_fees: float
    tag: str = ""
    partial_done: bool = False
    highest: float = 0.0

    @property
    def stop_advanced(self) -> bool:
        return self.stop_price > self.initial_stop + 1e-12


class Backtester:
    def __init__(
        self,
        bars: pd.DataFrame,
        strategy,
        costs: Optional[Costs] = None,
        initial_equity: float = 10_000.0,
        interval: str = "1d",
        trade_start: Optional[pd.Timestamp] = None,
        trade_end: Optional[pd.Timestamp] = None,
    ):
        missing = {"timestamp", "open", "high", "low", "close"} - set(bars.columns)
        if missing:
            raise ValueError(f"bars missing columns: {missing}")

        self.bars = bars.reset_index(drop=True)
        self.strategy = strategy
        self.costs = costs or Costs()
        self.initial_equity = initial_equity
        self.interval = interval
        # Bars before trade_start warm indicators only. Evaluating a held-out
        # window in isolation otherwise burns its first `warmup` bars and
        # fabricates a "no trades" result.
        self.trade_start = trade_start
        self.trade_end = trade_end

        self.cash = initial_equity
        self.positions: List[Position] = []
        self.trades: List[Trade] = []
        self._bars_in_market = 0

    # -- accounting -----------------------------------------------------
    def _equity(self, price: float) -> float:
        return self.cash + sum(p.qty * price for p in self.positions)

    def _open(self, ctx: Context, i: int, entry: Entry) -> None:
        fill = self.costs.entry_fill(float(ctx.open[i]))
        if entry.stop_price >= fill:
            return  # zero or negative risk distance: refuse

        equity = self._equity(fill)
        if entry.size_frac is not None:
            notional = equity * entry.size_frac
        else:
            notional = equity * entry.risk_frac * fill / (fill - entry.stop_price)

        notional = min(notional, self.cash / (1 + self.costs.fee_bps / 10_000))
        if notional <= 10:
            return

        qty = notional / fill
        fee = self.costs.fee(notional)
        self.cash -= notional + fee
        self.positions.append(Position(
            entry_time=ctx.timestamp[i], entry_price=fill, qty=qty, initial_qty=qty,
            stop_price=entry.stop_price, initial_stop=entry.stop_price,
            target_price=entry.target_price, trail_trigger=entry.trail_trigger,
            trail_dist=entry.trail_dist, partial_frac=entry.partial_frac,
            entry_bar=i, entry_fees=fee, tag=entry.tag, highest=fill,
        ))

    def _close(self, ctx: Context, pos: Position, i: int, qty: float,
               fill: float, reason: str) -> None:
        qty = min(qty, pos.qty)
        if qty <= 0:
            return
        proceeds = qty * fill
        fee = self.costs.fee(proceeds)
        self.cash += proceeds - fee

        share = qty / pos.initial_qty if pos.initial_qty else 1.0
        gross = qty * (fill - pos.entry_price)
        fees = fee + pos.entry_fees * share

        self.trades.append(Trade(
            entry_time=pos.entry_time, exit_time=ctx.timestamp[i],
            entry_price=pos.entry_price, exit_price=fill, qty=qty,
            gross_pnl=gross, fees=fees, net_pnl=gross - fees,
            return_pct=(gross - fees) / (qty * pos.entry_price),
            bars_held=i - pos.entry_bar, exit_reason=reason, tag=pos.tag,
        ))
        pos.qty -= qty

    # -- position management --------------------------------------------
    def _manage(self, ctx: Context, i: int) -> None:
        high, low = float(ctx.high[i]), float(ctx.low[i])
        max_bars = getattr(self.strategy, "max_bars_held", None)

        for pos in list(self.positions):
            # 1. stop first, using the level as it stood entering the bar
            if low <= pos.stop_price:
                reason = "TRAIL_STOP" if pos.stop_advanced else "STOP"
                self._close(ctx, pos, i, pos.qty,
                            self.costs.stop_exit_fill(pos.stop_price), reason)
                self.positions.remove(pos)
                continue

            # 2. target, with optional partial scale-out
            if pos.target_price is not None and high >= pos.target_price:
                fill = self.costs.limit_exit_fill(pos.target_price)
                if pos.partial_frac > 0 and not pos.partial_done:
                    self._close(ctx, pos, i, pos.initial_qty * pos.partial_frac,
                                fill, "PARTIAL_TARGET")
                    pos.partial_done = True
                    if pos.qty <= 1e-12:
                        self.positions.remove(pos)
                        continue
                    if pos.trail_dist:
                        pos.highest = max(pos.highest, high)
                        pos.stop_price = max(pos.stop_price, pos.highest - pos.trail_dist)
                    pos.target_price = None
                else:
                    self._close(ctx, pos, i, pos.qty, fill, "TARGET")
                    self.positions.remove(pos)
                    continue

            # 3. advance the trail
            if pos.trail_dist:
                armed = pos.trail_trigger is None or high >= pos.trail_trigger
                if armed:
                    pos.highest = max(pos.highest, high)
                    pos.stop_price = max(pos.stop_price, pos.highest - pos.trail_dist)

            # 4. optional time stop
            if max_bars and (i - pos.entry_bar) >= max_bars:
                self._close(ctx, pos, i, pos.qty,
                            self.costs.exit_fill(float(ctx.close[i])), "TIME_EXIT")
                self.positions.remove(pos)

    # -- main loop ------------------------------------------------------
    def run(self) -> Result:
        ctx = make_context(self.bars, self.strategy, self.interval)
        n = len(ctx)
        warmup = max(int(getattr(self.strategy, "warmup", 1)), 1)
        exit_hook = getattr(self.strategy, "exit_signal", None)

        lo = warmup
        if self.trade_start is not None:
            lo = max(lo, int(ctx.timestamp.searchsorted(self.trade_start)))
        hi = n - 1
        if self.trade_end is not None:
            hi = min(hi, int(ctx.timestamp.searchsorted(self.trade_end)))

        equity = np.empty(n)
        pending: Optional[Entry] = None
        pending_exit: Optional[str] = None

        for i in range(n):
            # 1. strategy exit decided last bar fills at this open
            if pending_exit is not None:
                fill = self.costs.exit_fill(float(ctx.open[i]))
                for pos in list(self.positions):
                    self._close(ctx, pos, i, pos.qty, fill, pending_exit)
                    self.positions.remove(pos)
                pending_exit = None

            # 2. entry decided last bar fills at this open
            if pending is not None:
                self._open(ctx, i, pending)
                pending = None

            # 3. stops / targets / trails against this bar's range
            if self.positions:
                self._manage(ctx, i)
                self._bars_in_market += 1

            equity[i] = self._equity(float(ctx.close[i]))

            if i < lo or i >= hi:
                continue

            # 4. decide for the next bar, using only data up to this close
            if self.positions:
                if exit_hook is not None:
                    reason = exit_hook(ctx, i, self.positions[0])
                    if reason:
                        pending_exit = reason
                        continue
                if getattr(self.strategy, "allow_pyramiding", False):
                    add = self.strategy.entry_signal(ctx, i, equity[i])
                    if add is not None and len(self.positions) < getattr(
                            self.strategy, "max_positions", 1):
                        pending = add
            else:
                e = self.strategy.entry_signal(ctx, i, equity[i])
                if e is not None:
                    pending = e

        # liquidate at the last close
        if self.positions:
            last = n - 1
            fill = self.costs.exit_fill(float(ctx.close[last]))
            for pos in list(self.positions):
                self._close(ctx, pos, last, pos.qty, fill, "END_OF_DATA")
                self.positions.remove(pos)
            equity[last] = self._equity(float(ctx.close[last]))

        start = min(lo, n - 1)
        idx = ctx.timestamp
        eq = pd.Series(equity[start:], index=idx[start:], name="equity")
        bench = pd.Series(ctx.close[start:] / ctx.close[start] * self.initial_equity,
                          index=idx[start:], name="buy_hold")
        evaluated = max(n - start, 1)
        return Result(self.trades, eq, self.interval, bench,
                      exposure=min(self._bars_in_market / evaluated, 1.0))
