"""Period analysis and walk-forward evaluation.

Two distinct questions, often conflated:

* **Is this configuration robust?** Run it with fixed parameters across several
  consecutive blocks and look at consistency. A config that is excellent in one
  regime and catastrophic in another is not robust, however good the aggregate.

* **Does refitting help?** Re-choose parameters on each training window and
  apply them forward. If refitting does *not* beat fixed parameters, the
  parameter surface is mostly noise and the simple fixed config should ship.

Both always pass full prior history to the engine and restrict *trading* with
trade_start/trade_end, so indicators are warm at the window boundary. Evaluating
a window in isolation silently burns its first `warmup` bars.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from bitbuddy.costs import Costs
from bitbuddy.data import as_utc
from bitbuddy.engine import Backtester
from bitbuddy.metrics import Result


def evaluate(bars: pd.DataFrame, factory: Callable, costs: Costs,
             interval: str = "1d", start=None, end=None,
             equity: float = 10_000.0) -> Result:
    """Backtest with full history for warmup, trading confined to [start, end)."""
    return Backtester(
        bars, factory(), costs, initial_equity=equity, interval=interval,
        trade_start=as_utc(start), trade_end=as_utc(end),
    ).run()


def split_blocks(bars: pd.DataFrame, n_blocks: int = 4,
                 start=None) -> List[Tuple[pd.Timestamp, pd.Timestamp]]:
    """Consecutive equal-length date ranges covering the data (or from `start`)."""
    ts = pd.DatetimeIndex(bars["timestamp"])
    lo = as_utc(start) or ts[0]
    hi = ts[-1]
    edges = pd.date_range(lo, hi, periods=n_blocks + 1)
    return [(edges[i], edges[i + 1]) for i in range(n_blocks)]


def block_metrics(bars: pd.DataFrame, factory: Callable, costs: Costs,
                  blocks: List[Tuple], interval: str = "1d") -> List[Dict]:
    """Fixed-parameter performance in each block."""
    out = []
    for lo, hi in blocks:
        r = evaluate(bars, factory, costs, interval, lo, hi)
        d = r.as_dict()
        d["start"], d["end"] = lo, hi
        d["bh"] = r.benchmark_return
        out.append(d)
    return out


def robustness(blocks: List[Dict]) -> Dict:
    """Summarise per-block results into robustness statistics.

    median_sharpe is the primary selection criterion: it rewards a config that
    works in most regimes rather than one that is spectacular in a single one.
    worst_sharpe and worst_dd guard against configs with a catastrophic regime.
    """
    sharpes = [b["sharpe"] for b in blocks]
    rets = [b["ret"] for b in blocks]
    return {
        "median_sharpe": float(np.median(sharpes)),
        "mean_sharpe": float(np.mean(sharpes)),
        "worst_sharpe": float(np.min(sharpes)),
        "worst_dd": float(np.min([b["dd"] for b in blocks])),
        "blocks_profitable": sum(1 for r in rets if r > 0),
        "n_blocks": len(blocks),
        "total_trades": sum(b["n"] for b in blocks),
    }


def stitch(curves: List[pd.Series]) -> pd.Series:
    """Chain per-window equity curves into one continuous curve."""
    parts, level = [], 1.0
    for c in curves:
        if len(c) < 2:
            continue
        seg = c / c.iloc[0] * level
        parts.append(seg)
        level = float(seg.iloc[-1])
    if not parts:
        return pd.Series(dtype=float)
    out = pd.concat(parts)
    return out[~out.index.duplicated(keep="last")]


def curve_stats(curve: pd.Series) -> Dict:
    if len(curve) < 2:
        return {"ret": 0.0, "cagr": 0.0, "dd": 0.0, "sharpe": 0.0}
    daily = curve.resample("D").last().dropna().pct_change(fill_method=None).dropna()
    years = (curve.index[-1] - curve.index[0]).total_seconds() / (365.25 * 86400)
    total = float(curve.iloc[-1] / curve.iloc[0] - 1)
    return {
        "ret": total,
        "cagr": (1 + total) ** (1 / years) - 1 if years > 0 else 0.0,
        "dd": float((curve / curve.cummax() - 1).min()),
        "sharpe": float(daily.mean() / daily.std() * np.sqrt(365)) if daily.std() > 0 else 0.0,
    }


def walk_forward_refit(
    bars: pd.DataFrame,
    grid: List,
    build: Callable,
    costs: Costs,
    interval: str = "1d",
    train_months: int = 36,
    test_months: int = 6,
    min_trades: int = 3,
    score: str = "sharpe",
    start=None,
    verbose: bool = False,
) -> Tuple[pd.Series, List[Dict]]:
    """Refit parameters on each training window, apply to the next unseen window."""
    ts = pd.DatetimeIndex(bars["timestamp"])
    first = as_utc(start) or ts[0]
    end = ts[-1]

    rows, curves = [], []
    test_start = first + pd.DateOffset(months=train_months)
    while test_start < end:
        test_end = min(test_start + pd.DateOffset(months=test_months), end)
        if (test_end - test_start).days < 45:
            break
        train_start = test_start - pd.DateOffset(months=train_months)

        best, best_score = None, -1e18
        for p in grid:
            r = evaluate(bars, build(p), costs, interval, train_start, test_start)
            if len(r.trades) < min_trades:
                continue
            s = getattr(r, score)
            if s > best_score:
                best, best_score = p, s
        if best is None:
            test_start = test_end
            continue

        r = evaluate(bars, build(best), costs, interval, test_start, test_end)
        curves.append(r.equity)
        rows.append({"start": test_start, "end": test_end, "params": best,
                     **r.as_dict(), "bh": r.benchmark_return})
        if verbose:
            print(f"    {test_start.date()} → {test_end.date()}  {str(best):<34} "
                  f"OOS {r.total_return * 100:>+7.1f}%  n={len(r.trades):<3} "
                  f"| B&H {r.benchmark_return * 100:>+7.1f}%")
        test_start = test_end

    return stitch(curves), rows
