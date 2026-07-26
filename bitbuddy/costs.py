"""Execution cost model.

Every fill is charged. Ignoring costs is the most common way a crypto backtest
manufactures an edge that does not exist: at 0.22% round trip, a strategy taking
1,400 trades a year needs +308% gross just to break even.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Costs:
    """Trading costs in basis points (1 bp = 0.01%).

    Defaults are Binance spot taker fees with no BNB discount, plus a modest
    slippage allowance for a liquid book at retail size.
    """

    fee_bps: float = 10.0
    slippage_bps: float = 2.0
    stop_slippage_bps: float = 8.0  # stops are market orders into an adverse move

    @property
    def round_trip_pct(self) -> float:
        return (self.fee_bps * 2 + self.slippage_bps) / 100.0

    def entry_fill(self, price: float) -> float:
        """Market buy: pay up."""
        return price * (1 + self.slippage_bps / 10_000)

    def exit_fill(self, price: float) -> float:
        """Market sell: receive less."""
        return price * (1 - self.slippage_bps / 10_000)

    def limit_exit_fill(self, price: float) -> float:
        """Resting limit order at the target: filled at price, no slippage."""
        return price

    def stop_exit_fill(self, price: float) -> float:
        return price * (1 - self.stop_slippage_bps / 10_000)

    def fee(self, notional: float) -> float:
        return abs(notional) * self.fee_bps / 10_000

    def scaled(self, mult: float) -> "Costs":
        """A copy with all costs multiplied, for cost-shock testing."""
        return Costs(self.fee_bps * mult, self.slippage_bps * mult,
                     self.stop_slippage_bps * mult)
