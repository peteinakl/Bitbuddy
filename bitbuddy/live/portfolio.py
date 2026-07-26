"""Portfolio accounting for the paper trader.

Realized P&L is computed per lot against its own cost basis. The original bot
recorded portfolio-value delta across a cash<->BTC conversion, which is zero by
construction, so it could never tell a win from a loss.

The identity `equity == initial + realized + unrealized - open_entry_fees` holds
at all times and is asserted on every bar in the tests. If it breaks, money is
being created or destroyed and no metric here can be trusted.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from bitbuddy.costs import Costs


@dataclass
class Lot:
    """One open parcel of BTC with its true cost basis."""

    qty: float
    entry_price: float          # fill price, slippage included
    entry_time: str
    entry_fee: float
    stop_price: float
    tag: str = ""


@dataclass
class TradeRecord:
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    qty: float
    gross_pnl: float
    fees: float
    net_pnl: float
    return_pct: float
    exit_reason: str

    @property
    def is_win(self) -> bool:
        return self.net_pnl > 0


@dataclass
class Portfolio:
    initial_capital: float = 10_000.0
    cash: float = 10_000.0
    lots: List[Lot] = field(default_factory=list)
    realized_pnl: float = 0.0
    fees_paid: float = 0.0
    trades: List[TradeRecord] = field(default_factory=list)
    peak_equity: float = 10_000.0

    @property
    def btc(self) -> float:
        return sum(l.qty for l in self.lots)

    @property
    def is_flat(self) -> bool:
        return not self.lots

    def equity(self, price: float) -> float:
        return self.cash + self.btc * price

    def unrealized(self, price: float) -> float:
        """Gross mark-to-market on open lots, before their entry fees."""
        return sum(l.qty * (price - l.entry_price) for l in self.lots)

    @property
    def open_entry_fees(self) -> float:
        """Entry fees paid on still-open lots: out of cash, not yet realized."""
        return sum(l.entry_fee for l in self.lots)

    def drawdown(self, price: float) -> float:
        eq = self.equity(price)
        peak = max(self.peak_equity, eq)
        return (peak - eq) / peak if peak > 0 else 0.0

    def check_invariant(self, price: float, tol: float = 0.01) -> bool:
        expected = (self.initial_capital + self.realized_pnl
                    + self.unrealized(price) - self.open_entry_fees)
        return abs(self.equity(price) - expected) < tol

    # -- stats ----------------------------------------------------------
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        return sum(1 for t in self.trades if t.is_win) / len(self.trades)

    def profit_factor(self) -> float:
        gains = sum(t.net_pnl for t in self.trades if t.net_pnl > 0)
        losses = -sum(t.net_pnl for t in self.trades if t.net_pnl < 0)
        if losses == 0:
            return float("inf") if gains > 0 else 0.0
        return gains / losses


class Broker:
    """Simulated spot execution: long or flat, no leverage, costs charged."""

    def __init__(self, portfolio: Portfolio, costs: Costs):
        self.p = portfolio
        self.costs = costs

    def buy(self, price: float, notional: float, stop_price: float,
            when: str, tag: str = "") -> Optional[Lot]:
        fill = self.costs.entry_fill(price)
        # Never spend more than we hold: cap by cash net of the fee
        notional = min(notional, self.p.cash / (1 + self.costs.fee_bps / 10_000))
        if notional < 10:
            return None
        qty = notional / fill
        fee = self.costs.fee(notional)
        self.p.cash -= notional + fee
        self.p.fees_paid += fee
        lot = Lot(qty=qty, entry_price=fill, entry_time=when, entry_fee=fee,
                  stop_price=stop_price, tag=tag)
        self.p.lots.append(lot)
        logging.info(f"BUY  {qty:.8f} BTC @ ${fill:,.2f}  notional ${notional:,.2f}  "
                     f"fee ${fee:.2f}  stop ${stop_price:,.2f}")
        return lot

    def sell_all(self, price: float, when: str, reason: str,
                 is_stop: bool = False) -> List[TradeRecord]:
        """Close every lot, booking realized P&L against its own cost basis."""
        fill = (self.costs.stop_exit_fill(price) if is_stop
                else self.costs.exit_fill(price))
        out = []
        for lot in list(self.p.lots):
            proceeds = lot.qty * fill
            fee = self.costs.fee(proceeds)
            gross = lot.qty * (fill - lot.entry_price)
            fees = fee + lot.entry_fee
            net = gross - fees

            self.p.cash += proceeds - fee
            self.p.fees_paid += fee
            self.p.realized_pnl += net

            rec = TradeRecord(
                entry_time=lot.entry_time, exit_time=when,
                entry_price=lot.entry_price, exit_price=fill, qty=lot.qty,
                gross_pnl=gross, fees=fees, net_pnl=net,
                return_pct=net / (lot.qty * lot.entry_price),
                exit_reason=reason,
            )
            self.p.trades.append(rec)
            out.append(rec)
            self.p.lots.remove(lot)
            logging.info(f"SELL {lot.qty:.8f} BTC @ ${fill:,.2f}  "
                         f"{'WIN ' if rec.is_win else 'LOSS'}  net ${net:+,.2f} "
                         f"({rec.return_pct * 100:+.2f}%)  [{reason}]")
        return out
