"""Strategy implementations.

All consume a `Context` of numpy arrays and return `Entry` objects, so the same
object drives both the backtester and the live trader.
"""

from bitbuddy.strategies.base import Strategy, vol_target_size
from bitbuddy.strategies.breakout import Donchian
from bitbuddy.strategies.legacy import LegacyBot
from bitbuddy.strategies.momentum import DualMomentum, TsMomentum
from bitbuddy.strategies.trend import MaTrend

# Registry for CLI lookup by name
REGISTRY = {
    "matrend": MaTrend,
    "tsmom": TsMomentum,
    "dualmom": DualMomentum,
    "donchian": Donchian,
    "legacy": LegacyBot,
}

__all__ = ["Strategy", "MaTrend", "TsMomentum", "DualMomentum", "Donchian",
           "LegacyBot", "REGISTRY", "vol_target_size"]
