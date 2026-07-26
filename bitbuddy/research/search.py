"""Parameter search across strategy families.

Selection protocol, designed so the reported holdout number means something:

    2017-08 ─────────── development ──────────── 2024-07 ── holdout ── 2026-07
              all searching and selection here              touched once

Within development, a configuration is scored by its **median Sharpe across
consecutive blocks**, not by its aggregate. Aggregate performance over a long
window rewards a config that made all its money in one regime; median-across-
blocks rewards one that works repeatedly. `worst_sharpe` and a minimum trade
count filter out configs that survive on a single lucky run.

The holdout is evaluated only after selection is final. Looking at it before
that turns it into just another training set.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import pandas as pd

from bitbuddy.costs import Costs
from bitbuddy.research.walkforward import (as_utc, block_metrics, evaluate,
                                            robustness, split_blocks)


@dataclass
class Candidate:
    family: str
    params: Dict
    build: Callable = field(repr=False)
    dev: Dict = field(default_factory=dict)     # per-block robustness stats
    full: Dict = field(default_factory=dict)    # aggregate over the whole dev set
    blocks: List[Dict] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.family} " + " ".join(
            f"{k}={v:g}" if isinstance(v, (int, float)) else f"{k}={v}"
            for k, v in self.params.items())

    @property
    def full_dd(self) -> float:
        """Drawdown over the entire development set.

        Per-block drawdown understates risk: a decline spanning a block boundary
        is split in two and each half looks survivable. Selection must use this.
        """
        return self.full.get("dd", -1.0)


def grid(**axes) -> List[Dict]:
    """Cartesian product of keyword axes as a list of parameter dicts."""
    keys = list(axes)
    return [dict(zip(keys, combo)) for combo in itertools.product(*(axes[k] for k in keys))]


def search(
    bars: pd.DataFrame,
    family: str,
    cls,
    param_grid: List[Dict],
    costs: Costs,
    interval: str = "1d",
    n_blocks: int = 4,
    dev_start=None,
    dev_end=None,
    min_trades: int = 8,
    max_dd: float = -0.45,
    min_blocks_profitable: int = 3,
    fixed: Optional[Dict] = None,
    progress: bool = False,
) -> List[Candidate]:
    """Score every config in `param_grid` on the development set.

    `max_dd` is a hard constraint on aggregate development drawdown. A config
    that draws down 76% is not shippable regardless of its Sharpe -- it barely
    improves on buy & hold and no one holds through it -- so it is rejected here
    rather than left to be filtered by eye later.
    """
    fixed = fixed or {}
    dev_bars = bars[bars["timestamp"] < as_utc(dev_end)] if dev_end else bars
    blocks = split_blocks(dev_bars, n_blocks, dev_start)

    out: List[Candidate] = []
    errors: List = []
    for k, params in enumerate(param_grid):
        kwargs = {**fixed, **params, "interval": interval}

        def build(kw=kwargs):
            return lambda: cls(**kw)

        try:
            per_block = block_metrics(bars, build(), costs, blocks, interval)
        except Exception as exc:
            # Surface the first failure rather than silently returning nothing;
            # a swallowed error here looks identical to "no config qualified".
            if not errors:
                print(f"    [warn] {family} {params} failed: "
                      f"{type(exc).__name__}: {exc}")
            errors.append((params, exc))
            continue
        rob = robustness(per_block)
        if rob["total_trades"] < min_trades:
            continue
        if rob["blocks_profitable"] < min_blocks_profitable:
            continue

        # Aggregate development performance, for the drawdown constraint
        full = evaluate(bars, build(), costs, interval, dev_start, dev_end).as_dict()
        if full["dd"] < max_dd:
            continue

        out.append(Candidate(family=family, params=params, build=build(),
                             dev=rob, full=full, blocks=per_block))
        if progress and (k + 1) % 50 == 0:
            print(f"    {k + 1}/{len(param_grid)} configs")
    if errors and not out:
        print(f"    [warn] all {len(errors)} configs failed for {family}")
    return out


def rank(cands: List[Candidate], by: str = "median_sharpe",
         require_all_blocks_profitable: bool = False) -> List[Candidate]:
    out = cands
    if require_all_blocks_profitable:
        out = [c for c in out if c.dev["blocks_profitable"] == c.dev["n_blocks"]]
    return sorted(out, key=lambda c: -c.dev.get(by, 0.0))


HEADER = (f"  {'configuration':<50}{'devRet':>9}{'devDD':>8}{'devSh':>7}"
          f"{'medSh':>7}{'minSh':>7}{'blk+':>6}{'n':>5}")


def report(cands: List[Candidate], top: int = 10, title: str = "") -> None:
    if title:
        print(f"\n{title}")
    print(HEADER)
    print("  " + "-" * 97)
    for c in cands[:top]:
        d, f = c.dev, c.full
        print(f"  {c.label:<50}{f.get('ret', 0) * 100:>8.0f}%{f.get('dd', 0) * 100:>7.0f}%"
              f"{f.get('sharpe', 0):>7.2f}{d['median_sharpe']:>7.2f}{d['worst_sharpe']:>7.2f}"
              f"{d['blocks_profitable']:>4}/{d['n_blocks']}{d['total_trades']:>5}")
