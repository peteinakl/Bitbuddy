"""Event-driven backtest engine for Bitcoin trading strategies.

Design decisions that keep results honest:

1. **No lookahead.** A signal computed from bar i's close is filled at bar i+1's
   open. Filling at the close of the bar that generated the signal is the single
   most common way backtests invent profit that does not exist.

2. **Costs are always charged.** Taker fee per side plus slippage. Stop exits pay
   extra slippage because they are market orders into a move that is already
   against you.

3. **Pessimistic intrabar ordering.** When a bar's range contains both the stop
   and the target, the stop is taken. Without tick data you cannot know which
   came first, so assume the worse one.

4. **Risk-based sizing.** Quantity is derived from the distance to the stop, so
   every trade risks the same fraction of equity. This volatility-scales
   position size automatically: wide stop in a violent market means fewer coins.

Usage:
    from backtest import Backtester, Costs
    from strategies import TrendAtrStrategy

    bt = Backtester(df, TrendAtrStrategy(), Costs())
    result = bt.run()
    print(result.summary())
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np
import pandas as pd

BARS_PER_DAY = {"1m": 1440, "5m": 288, "15m": 96, "1h": 24, "4h": 6, "1d": 1}


@dataclass
class Costs:
    """Round-trip trading costs in basis points (1 bp = 0.01%)."""

    fee_bps: float = 10.0          # Binance spot taker, no BNB discount
    slippage_bps: float = 2.0      # market order into a liquid book
    stop_slippage_bps: float = 8.0 # extra for stop-outs: you cross a moving spread

    def entry_fill(self, price: float) -> float:
        return price * (1 + self.slippage_bps / 10_000)

    def limit_exit_fill(self, price: float) -> float:
        # Resting limit order at the target: filled at price, no slippage
        return price

    def stop_exit_fill(self, price: float) -> float:
        return price * (1 - self.stop_slippage_bps / 10_000)

    def fee(self, notional: float) -> float:
        return abs(notional) * self.fee_bps / 10_000


@dataclass
class Entry:
    """A strategy's request to open a position."""

    stop_price: float
    target_price: Optional[float] = None
    risk_frac: float = 0.01          # fraction of equity risked to the stop
    size_frac: Optional[float] = None  # alternative: fixed fraction of equity as notional
    trail_trigger: Optional[float] = None  # price at which trailing begins
    trail_dist: Optional[float] = None     # absolute trailing distance
    partial_frac: float = 0.0        # fraction to scale out at target
    tag: str = ""


@dataclass
class Position:
    entry_time: pd.Timestamp
    entry_price: float
    qty: float
    stop_price: float
    target_price: Optional[float]
    trail_trigger: Optional[float]
    trail_dist: Optional[float]
    partial_frac: float
    tag: str
    entry_bar: int
    initial_qty: float
    entry_fees: float
    initial_stop: float = 0.0
    partial_done: bool = False
    trailing: bool = False
    highest: float = 0.0

    @property
    def stop_has_advanced(self) -> bool:
        """True once the trail has lifted the stop above where it started.
        Distinguishes 'gave back an open profit' from 'initial stop was hit'."""
        return self.stop_price > self.initial_stop + 1e-12

    @property
    def risk_per_unit(self) -> float:
        return self.entry_price - self.stop_price


@dataclass
class Trade:
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    qty: float
    gross_pnl: float
    fees: float
    net_pnl: float
    return_pct: float
    bars_held: int
    exit_reason: str
    tag: str


@dataclass
class Result:
    trades: List[Trade]
    equity: pd.Series
    bar_interval: str
    benchmark: pd.Series = field(default_factory=pd.Series)

    # ---- metrics -------------------------------------------------------
    @property
    def initial_equity(self) -> float:
        return float(self.equity.iloc[0])

    @property
    def final_equity(self) -> float:
        return float(self.equity.iloc[-1])

    @property
    def total_return(self) -> float:
        return self.final_equity / self.initial_equity - 1

    @property
    def years(self) -> float:
        span = self.equity.index[-1] - self.equity.index[0]
        return span.total_seconds() / (365.25 * 86400)

    @property
    def cagr(self) -> float:
        if self.years <= 0 or self.final_equity <= 0:
            return 0.0
        return (self.final_equity / self.initial_equity) ** (1 / self.years) - 1

    def _daily_returns(self) -> pd.Series:
        daily = self.equity.resample("D").last().dropna()
        return daily.pct_change(fill_method=None).dropna()

    @property
    def sharpe(self) -> float:
        r = self._daily_returns()
        if len(r) < 2 or r.std() == 0:
            return 0.0
        return float(r.mean() / r.std() * np.sqrt(365))

    @property
    def sortino(self) -> float:
        r = self._daily_returns()
        downside = r[r < 0]
        if len(downside) < 2 or downside.std() == 0:
            return 0.0
        return float(r.mean() / downside.std() * np.sqrt(365))

    @property
    def max_drawdown(self) -> float:
        curve = self.equity / self.equity.cummax() - 1
        return float(curve.min())

    @property
    def calmar(self) -> float:
        dd = abs(self.max_drawdown)
        return self.cagr / dd if dd > 0 else 0.0

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        return sum(1 for t in self.trades if t.net_pnl > 0) / len(self.trades)

    @property
    def profit_factor(self) -> float:
        gains = sum(t.net_pnl for t in self.trades if t.net_pnl > 0)
        losses = -sum(t.net_pnl for t in self.trades if t.net_pnl < 0)
        if losses == 0:
            return float("inf") if gains > 0 else 0.0
        return gains / losses

    @property
    def expectancy_pct(self) -> float:
        """Mean per-trade return. Weights every trade equally regardless of size,
        so partial scale-outs inflate it; read expectancy_dollar alongside it."""
        if not self.trades:
            return 0.0
        return float(np.mean([t.return_pct for t in self.trades]))

    @property
    def expectancy_dollar(self) -> float:
        """Mean net P&L per trade in currency. Size-weighted, so this is the
        one that reconciles with the equity curve."""
        if not self.trades:
            return 0.0
        return float(np.mean([t.net_pnl for t in self.trades]))

    @property
    def avg_win_pct(self) -> float:
        wins = [t.return_pct for t in self.trades if t.net_pnl > 0]
        return float(np.mean(wins)) if wins else 0.0

    @property
    def avg_loss_pct(self) -> float:
        losses = [t.return_pct for t in self.trades if t.net_pnl < 0]
        return float(np.mean(losses)) if losses else 0.0

    @property
    def total_fees(self) -> float:
        return sum(t.fees for t in self.trades)

    @property
    def avg_bars_held(self) -> float:
        return float(np.mean([t.bars_held for t in self.trades])) if self.trades else 0.0

    @property
    def exit_breakdown(self) -> dict:
        out: dict = {}
        for t in self.trades:
            out[t.exit_reason] = out.get(t.exit_reason, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    @property
    def trades_per_year(self) -> float:
        return len(self.trades) / self.years if self.years > 0 else 0.0

    def summary(self, name: str = "strategy") -> str:
        bars_day = BARS_PER_DAY.get(self.bar_interval, 288)
        hours_held = self.avg_bars_held * 24 / bars_day
        lines = [
            f"── {name} ──",
            f"  Period            {self.equity.index[0].date()} → {self.equity.index[-1].date()}  ({self.years:.2f}y)",
            f"  Final equity      ${self.final_equity:,.0f}  from ${self.initial_equity:,.0f}",
            f"  Total return      {self.total_return * 100:+.1f}%",
            f"  CAGR              {self.cagr * 100:+.1f}%",
            f"  Max drawdown      {self.max_drawdown * 100:.1f}%",
            f"  Sharpe / Sortino  {self.sharpe:.2f} / {self.sortino:.2f}",
            f"  Calmar            {self.calmar:.2f}",
            f"  Trades            {len(self.trades)}  ({self.trades_per_year:.0f}/yr)",
            f"  Win rate          {self.win_rate * 100:.1f}%",
            f"  Profit factor     {self.profit_factor:.2f}",
            f"  Expectancy/trade  ${self.expectancy_dollar:+,.2f}  ({self.expectancy_pct * 100:+.3f}% unweighted)",
            f"  Avg win / loss    {self.avg_win_pct * 100:+.2f}% / {self.avg_loss_pct * 100:+.2f}%",
            f"  Avg hold          {hours_held:.1f}h",
            f"  Fees paid         ${self.total_fees:,.0f}  ({self.total_fees / self.initial_equity * 100:.0f}% of start equity)",
            f"  Exits             {self.exit_breakdown}",
        ]
        if len(self.benchmark) > 0:
            bh_ret = self.benchmark.iloc[-1] / self.benchmark.iloc[0] - 1
            bh_dd = float((self.benchmark / self.benchmark.cummax() - 1).min())
            lines.append(f"  vs buy&hold       {bh_ret * 100:+.1f}% return, {bh_dd * 100:.1f}% DD")
        return "\n".join(lines)


class Backtester:
    """Runs a strategy over OHLCV bars with costs and no lookahead."""

    def __init__(
        self,
        df: pd.DataFrame,
        strategy,
        costs: Optional[Costs] = None,
        initial_equity: float = 10_000.0,
        bar_interval: str = "5m",
        max_positions: int = 1,
        allow_pyramiding: bool = False,
        trade_start: Optional[pd.Timestamp] = None,
    ):
        required = {"timestamp", "open", "high", "low", "close"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"df missing columns: {missing}")

        self.df = df.reset_index(drop=True).copy()
        self.strategy = strategy
        self.costs = costs or Costs()
        self.initial_equity = initial_equity
        self.bar_interval = bar_interval
        self.max_positions = max_positions
        self.allow_pyramiding = allow_pyramiding
        # Bars before trade_start are used only to warm up indicators. Without
        # this, evaluating a held-out period in isolation burns the first
        # `warmup` bars of it and fabricates a "no trades" result.
        self.trade_start = trade_start

        self.cash = initial_equity
        self.positions: List[Position] = []
        self.trades: List[Trade] = []

    # -- accounting -------------------------------------------------------
    def _equity(self, price: float) -> float:
        return self.cash + sum(p.qty * price for p in self.positions)

    def _open_position(self, i: int, entry: Entry) -> None:
        """Fill a market entry at bar i's open."""
        row = self.df.iloc[i]
        fill = self.costs.entry_fill(float(row["open"]))

        if entry.stop_price >= fill:
            return  # nonsensical stop, skip rather than divide by ~zero

        equity = self._equity(fill)

        if entry.size_frac is not None:
            notional = equity * entry.size_frac
        else:
            risk_amount = equity * entry.risk_frac
            notional = risk_amount * fill / (fill - entry.stop_price)

        # Never commit more than available cash (no leverage, no margin)
        notional = min(notional, self.cash * 0.999)
        if notional <= 0:
            return

        qty = notional / fill
        fee = self.costs.fee(notional)
        if notional + fee > self.cash:
            notional = self.cash / (1 + self.costs.fee_bps / 10_000)
            qty = notional / fill
            fee = self.costs.fee(notional)
        if qty * fill < 10:  # dust
            return

        self.cash -= notional + fee
        self.positions.append(
            Position(
                entry_time=row["timestamp"],
                entry_price=fill,
                qty=qty,
                initial_qty=qty,
                stop_price=entry.stop_price,
                target_price=entry.target_price,
                trail_trigger=entry.trail_trigger,
                trail_dist=entry.trail_dist,
                partial_frac=entry.partial_frac,
                tag=entry.tag,
                entry_bar=i,
                entry_fees=fee,
                initial_stop=entry.stop_price,
                highest=fill,
            )
        )

    def _close(self, pos: Position, i: int, qty: float, fill: float, reason: str) -> None:
        row = self.df.iloc[i]
        qty = min(qty, pos.qty)
        proceeds = qty * fill
        fee = self.costs.fee(proceeds)
        self.cash += proceeds - fee

        # Entry fees are attributed proportionally to the quantity being closed
        share = qty / pos.initial_qty if pos.initial_qty else 1.0
        entry_fee_share = pos.entry_fees * share
        gross = qty * (fill - pos.entry_price)
        total_fees = fee + entry_fee_share

        self.trades.append(
            Trade(
                entry_time=pos.entry_time,
                exit_time=row["timestamp"],
                entry_price=pos.entry_price,
                exit_price=fill,
                qty=qty,
                gross_pnl=gross,
                fees=total_fees,
                net_pnl=gross - total_fees,
                return_pct=(gross - total_fees) / (qty * pos.entry_price),
                bars_held=i - pos.entry_bar,
                exit_reason=reason,
                tag=pos.tag,
            )
        )
        pos.qty -= qty

    # -- position management ---------------------------------------------
    def _manage(self, i: int) -> None:
        """Check stops, targets and trailing for every open position on bar i."""
        row = self.df.iloc[i]
        high, low = float(row["high"]), float(row["low"])

        for pos in list(self.positions):
            # 1. Stop first (pessimistic): use the stop as it stood entering the bar
            if low <= pos.stop_price:
                fill = self.costs.stop_exit_fill(pos.stop_price)
                reason = "TRAIL_STOP" if pos.stop_has_advanced else "STOP"
                self._close(pos, i, pos.qty, fill, reason)
                self.positions.remove(pos)
                continue

            # 2. Target / partial scale-out
            if pos.target_price is not None and high >= pos.target_price:
                if pos.partial_frac > 0 and not pos.partial_done:
                    qty = pos.initial_qty * pos.partial_frac
                    self._close(pos, i, qty, self.costs.limit_exit_fill(pos.target_price), "PARTIAL_TARGET")
                    pos.partial_done = True
                    if pos.qty <= 1e-12:
                        self.positions.remove(pos)
                        continue
                    # Remaining quantity rides a trailing stop
                    if pos.trail_dist:
                        pos.trailing = True
                        pos.highest = max(pos.highest, high)
                        pos.stop_price = max(pos.stop_price, pos.highest - pos.trail_dist)
                    pos.target_price = None
                else:
                    self._close(pos, i, pos.qty, self.costs.limit_exit_fill(pos.target_price), "TARGET")
                    self.positions.remove(pos)
                    continue

            # 3. Arm / advance the trailing stop
            if pos.trail_dist:
                if not pos.trailing and pos.trail_trigger is not None and high >= pos.trail_trigger:
                    pos.trailing = True
                if pos.trailing:
                    pos.highest = max(pos.highest, high)
                    pos.stop_price = max(pos.stop_price, pos.highest - pos.trail_dist)

            # 4. Optional strategy-driven time exit
            max_bars = getattr(self.strategy, "max_bars_held", None)
            if max_bars and (i - pos.entry_bar) >= max_bars:
                fill = self.costs.entry_fill(float(row["close"])) * (1 - self.costs.slippage_bps / 10_000)
                self._close(pos, i, pos.qty, fill, "TIME_EXIT")
                self.positions.remove(pos)

    # -- main loop --------------------------------------------------------
    def run(self) -> Result:
        df = self.strategy.prepare(self.df)
        self.df = df
        warmup = max(int(getattr(self.strategy, "warmup", 0)), 1)
        exit_hook = getattr(self.strategy, "exit_signal", None)

        # First bar on which trading is permitted
        first_tradable = warmup
        if self.trade_start is not None:
            ts = pd.DatetimeIndex(df["timestamp"])
            first_tradable = max(warmup, int(ts.searchsorted(self.trade_start)))

        equity_vals = np.empty(len(df))
        equity_vals[:] = np.nan
        pending: Optional[Entry] = None
        pending_exit: Optional[str] = None

        closes = df["close"].to_numpy(dtype=float)
        opens = df["open"].to_numpy(dtype=float)

        for i in range(len(df)):
            # 1. A strategy exit decided on the previous close fills at this open
            if pending_exit is not None:
                fill = opens[i] * (1 - self.costs.slippage_bps / 10_000)
                for pos in list(self.positions):
                    self._close(pos, i, pos.qty, fill, pending_exit)
                    self.positions.remove(pos)
                pending_exit = None

            # 2. An entry decided on the previous close fills at this open
            if pending is not None:
                if len(self.positions) < self.max_positions:
                    self._open_position(i, pending)
                pending = None

            # 3. Stops, targets and trails act against this bar's range
            if self.positions:
                self._manage(i)

            equity_vals[i] = self._equity(closes[i])

            if i < first_tradable or i >= len(df) - 1:
                continue

            # 4. Decide for the next bar, using only information up to this close
            if self.positions and exit_hook is not None:
                reason = exit_hook(df, i, self.positions[0])
                if reason:
                    pending_exit = reason
                    continue

            has_room = len(self.positions) < self.max_positions
            if has_room and (self.allow_pyramiding or not self.positions):
                entry = self.strategy.entry_signal(df, i, self._equity(closes[i]))
                if entry is not None:
                    pending = entry

        # Liquidate anything still open at the final close
        if self.positions:
            last = len(df) - 1
            for pos in list(self.positions):
                fill = float(df.iloc[last]["close"]) * (1 - self.costs.slippage_bps / 10_000)
                self._close(pos, last, pos.qty, fill, "END_OF_DATA")
                self.positions.remove(pos)
            equity_vals[last] = self._equity(closes[last])

        # Report only the evaluated window: warmup bars carry no positions and
        # would otherwise pad the curve with a flat prefix, deflating CAGR and
        # making the benchmark start at the wrong price.
        lo = min(first_tradable, len(df) - 1)
        idx = pd.DatetimeIndex(df["timestamp"])
        equity = pd.Series(equity_vals[lo:], index=idx[lo:], name="equity")
        benchmark = pd.Series(
            closes[lo:] / closes[lo] * self.initial_equity,
            index=idx[lo:],
            name="buy_hold",
        )
        return Result(self.trades, equity, self.bar_interval, benchmark)


def load_bars(path: str = "data/BTCUSDT_5m.csv", start: Optional[str] = None, end: Optional[str] = None) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["timestamp"])
    if start:
        df = df[df["timestamp"] >= pd.Timestamp(start, tz="UTC")]
    if end:
        df = df[df["timestamp"] < pd.Timestamp(end, tz="UTC")]
    return df.reset_index(drop=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    bars = load_bars()
    logging.info("Loaded %d bars %s → %s", len(bars), bars["timestamp"].iloc[0], bars["timestamp"].iloc[-1])
