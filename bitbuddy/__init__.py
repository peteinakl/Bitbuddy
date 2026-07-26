"""Bitbuddy — a backtest-first Bitcoin trading simulator.

Layout:

    bitbuddy.data          loading, resampling, downloading bars
    bitbuddy.costs         the execution cost model
    bitbuddy.indicators    numpy indicator primitives
    bitbuddy.engine        the backtest engine
    bitbuddy.metrics       results and performance statistics
    bitbuddy.strategies    strategy implementations
    bitbuddy.research      walk-forward and parameter search
    bitbuddy.live          paper trading

The engine and the live trader consume the same strategy objects, so a
configuration that has been validated is the configuration that runs.
"""

from bitbuddy.costs import Costs
from bitbuddy.engine import Backtester
from bitbuddy.metrics import Result

__all__ = ["Costs", "Backtester", "Result"]
__version__ = "2.0.0"
