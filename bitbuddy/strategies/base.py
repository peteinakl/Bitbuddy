"""Strategy protocol and shared helpers.

A strategy implements:

    warmup: int
        Bars required before signals are meaningful.

    prepare(bars: DataFrame) -> Dict[str, np.ndarray]
        Vectorised, called once. Returns feature arrays aligned to bars.
        Must be causal: element i may only use data up to bar i.

    entry_signal(ctx: Context, i: int, equity: float) -> Optional[Entry]
        Called on bar i's close while flat. The fill happens at bar i+1's open.

    exit_signal(ctx, i, position) -> Optional[str]     (optional)
        Called on bar i's close while in a position. Returning a reason string
        closes at bar i+1's open. Stops and targets are handled by the engine.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from bitbuddy.engine import Entry


def vol_target_size(realized_vol: float, target_vol: float,
                    max_frac: float = 0.95, min_frac: float = 0.10) -> float:
    """Fraction of equity to deploy so position vol approaches target_vol.

    Capped at max_frac because spot cannot use leverage: this can only ever
    reduce exposure relative to fully invested, never increase it.
    """
    if not np.isfinite(realized_vol) or realized_vol <= 0:
        return min_frac
    return float(np.clip(target_vol / realized_vol, min_frac, max_frac))


def finite(*vals: float) -> bool:
    return all(np.isfinite(v) for v in vals)


class Strategy:
    """Base class supplying a name and a no-op prepare."""

    name = "strategy"
    warmup = 1
    max_bars_held: Optional[int] = None
    allow_pyramiding = False
    max_positions = 1

    def prepare(self, bars):
        return {}

    def entry_signal(self, ctx, i: int, equity: float) -> Optional[Entry]:
        raise NotImplementedError

    def describe(self) -> str:
        return self.name
