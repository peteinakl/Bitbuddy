"""Validate finalist configurations before shipping one.

Finalists were selected on development-set statistics only (see optimise.py).
The holdout appears here for confirmation, never for selection.

Checks:
    1. Head-to-head across development, holdout, full period
    2. Per-calendar-year, so a single lucky regime cannot hide
    3. Parameter sensitivity around each finalist
    4. Cost shock at 1x/2x/4x/8x
    5. Refit vs fixed walk-forward

Usage:
    python validate.py
    python validate.py --check sens
"""

import argparse

import numpy as np
import pandas as pd

from bitbuddy.costs import Costs
from bitbuddy.data import load_bars, slice_bars
from bitbuddy.research.search import grid
from bitbuddy.research.walkforward import (curve_stats, evaluate, split_blocks,
                                           stitch, walk_forward_refit)
from bitbuddy.strategies import DualMomentum, MaTrend

DEV_END = "2024-07-01"
FIXED = {"stop_atr": 8.0, "atr_period": 14, "vol_window": 30, "interval": "1d"}

# Selected on development statistics only.
FINALISTS = {
    "A dualmom 30/300/20 vt0.4": lambda: DualMomentum(
        lookback=30, slow_ma=300, exit_ma=20, target_vol=0.4, **FIXED),
    "B matrend 20/30 noconfirm vt0.4": lambda: MaTrend(
        entry_ma=20, exit_ma=30, slow_ma=200, confirm=False, target_vol=0.4, **FIXED),
    "C matrend 50/50/200 confirm vt0.5 (shipped)": lambda: MaTrend(
        entry_ma=50, exit_ma=50, slow_ma=200, confirm=True, target_vol=0.5, **FIXED),
}


def bh(bars, start=None, end=None):
    d = slice_bars(bars, start, end)
    c = d.set_index("timestamp")["close"]
    curve = c / c.iloc[0]
    daily = curve.resample("D").last().dropna().pct_change(fill_method=None).dropna()
    years = (c.index[-1] - c.index[0]).total_seconds() / (365.25 * 86400)
    total = float(curve.iloc[-1] - 1)
    return {
        "ret": total, "cagr": (1 + total) ** (1 / years) - 1 if years > 0 else 0,
        "dd": float((curve / curve.cummax() - 1).min()),
        "sharpe": float(daily.mean() / daily.std() * np.sqrt(365)) if daily.std() else 0,
        "n": 0, "exposure": 1.0,
    }


def row(label, m):
    return (f"  {label:<44}{m['ret'] * 100:>9.0f}%{m['cagr'] * 100:>8.1f}%"
            f"{m['dd'] * 100:>8.0f}%{m['sharpe']:>8.2f}"
            f"{m.get('calmar', 0):>8.2f}{m['n']:>6}{m['exposure'] * 100:>8.0f}%")


def head(title):
    print(f"\n{title}")
    print(f"  {'':<44}{'return':>10}{'CAGR':>8}{'maxDD':>8}{'Sharpe':>8}{'Calmar':>8}"
          f"{'n':>6}{'expo':>8}")
    print("  " + "-" * 100)


def check_headline(bars, costs):
    print("=" * 104)
    print("1. HEAD-TO-HEAD")
    print("=" * 104)
    for title, (s, e) in [
        (f"development  2017-08 → {DEV_END[:7]}  (selection happened here)", (None, DEV_END)),
        (f"holdout      {DEV_END[:7]} → 2026-07  (confirmation only)", (DEV_END, None)),
        ("full         2017-08 → 2026-07", (None, None)),
    ]:
        head(title)
        for name, factory in FINALISTS.items():
            r = evaluate(bars, factory, costs, "1d", s, e)
            print(row(name, r.as_dict()))
        print(row("buy & hold", bh(bars, s, e)))


def check_years(bars, costs):
    print("\n" + "=" * 104)
    print("2. PER CALENDAR YEAR  (return %, so one good regime cannot hide)")
    print("=" * 104)
    years = sorted({t.year for t in bars["timestamp"]})
    hdr = "  " + f"{'':<44}" + "".join(f"{y:>7}" for y in years)
    print(hdr)
    print("  " + "-" * (44 + 7 * len(years)))
    for name, factory in FINALISTS.items():
        cells = []
        for y in years:
            r = evaluate(bars, factory, costs, "1d", f"{y}-01-01", f"{y + 1}-01-01")
            cells.append(f"{r.total_return * 100:>+7.0f}")
        print(f"  {name:<44}" + "".join(cells))
    cells = []
    for y in years:
        cells.append(f"{bh(bars, f'{y}-01-01', f'{y + 1}-01-01')['ret'] * 100:>+7.0f}")
    print(f"  {'buy & hold':<44}" + "".join(cells))


def check_sensitivity(bars, costs):
    print("\n" + "=" * 104)
    print("3. PARAMETER SENSITIVITY on the development set (plateau or spike?)")
    print("=" * 104)
    specs = [
        ("dualmom", DualMomentum, grid(lookback=[20, 30, 45, 60, 90],
                                       slow_ma=[200, 250, 300, 400],
                                       exit_ma=[15, 20, 30, 50],
                                       target_vol=[0.4])),
        ("matrend noconfirm", MaTrend, grid(entry_ma=[15, 20, 30, 40],
                                            exit_ma=[20, 30, 50],
                                            confirm=[False], target_vol=[0.4])),
    ]
    for label, cls, g in specs:
        rets, sharpes, dds = [], [], []
        for p in g:
            r = evaluate(bars, lambda p=p: cls(**{**FIXED, **p}), costs, "1d", None, DEV_END)
            rets.append(r.total_return)
            sharpes.append(r.sharpe)
            dds.append(r.max_drawdown)
        pos = sum(1 for x in rets if x > 0)
        print(f"\n  {label}: {len(g)} configs")
        print(f"    profitable        {pos}/{len(g)} ({pos / len(g) * 100:.0f}%)")
        print(f"    Sharpe            median {np.median(sharpes):.2f}  "
              f"min {min(sharpes):.2f}  max {max(sharpes):.2f}")
        print(f"    return            median {np.median(rets) * 100:.0f}%  "
              f"min {min(rets) * 100:.0f}%  max {max(rets) * 100:.0f}%")
        print(f"    worst drawdown    {min(dds) * 100:.0f}%")


def check_costs(bars, costs):
    print("\n" + "=" * 104)
    print("4. COST SHOCK (full period)")
    print("=" * 104)
    print(f"  {'':<44}{'1x':>12}{'2x':>12}{'4x':>12}{'8x':>12}")
    print("  " + "-" * 92)
    for name, factory in FINALISTS.items():
        cells = []
        for m in (1, 2, 4, 8):
            r = evaluate(bars, factory, costs.scaled(m), "1d")
            cells.append(f"{r.total_return * 100:>+11.0f}%")
        print(f"  {name:<44}" + "".join(cells))
    print("\n  (a strategy whose edge vanishes at 4x costs never had one)")


def check_walkforward(bars, costs):
    print("\n" + "=" * 104)
    print("5. WALK-FORWARD: does refitting beat fixed parameters?")
    print("=" * 104)
    g = grid(lookback=[20, 30, 45, 60, 90], slow_ma=[200, 300, 400],
             exit_ma=[15, 20, 30, 50], target_vol=[0.4])

    def build(p):
        return lambda: DualMomentum(**{**FIXED, **p})

    curve, rows = walk_forward_refit(bars, g, build, costs, "1d",
                                     train_months=36, test_months=6, verbose=True)
    if len(curve) < 2:
        print("  not enough windows")
        return
    first, last = curve.index[0], curve.index[-1]

    fixed_curves = []
    ts = pd.DatetimeIndex(bars["timestamp"])
    cur = first
    while cur < last:
        nxt = min(cur + pd.DateOffset(months=6), last)
        fixed_curves.append(
            evaluate(bars, FINALISTS["A dualmom 30/300/20 vt0.4"], costs, "1d", cur, nxt).equity)
        cur = nxt
    fixed_curve = stitch(fixed_curves)

    a, b = curve_stats(curve), curve_stats(fixed_curve)
    ref = bh(bars, first, last)
    print(f"\n  Stitched out-of-sample {first.date()} → {last.date()}")
    print(f"  {'':<30}{'return':>10}{'CAGR':>8}{'maxDD':>8}{'Sharpe':>8}")
    print("  " + "-" * 64)
    print(f"  {'refit each window':<30}{a['ret'] * 100:>9.0f}%{a['cagr'] * 100:>7.1f}%"
          f"{a['dd'] * 100:>7.0f}%{a['sharpe']:>8.2f}")
    print(f"  {'fixed 30/300/20':<30}{b['ret'] * 100:>9.0f}%{b['cagr'] * 100:>7.1f}%"
          f"{b['dd'] * 100:>7.0f}%{b['sharpe']:>8.2f}")
    print(f"  {'buy & hold':<30}{ref['ret'] * 100:>9.0f}%{ref['cagr'] * 100:>7.1f}%"
          f"{ref['dd'] * 100:>7.0f}%{ref['sharpe']:>8.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", default="all",
                    choices=["all", "headline", "years", "sens", "costs", "wf"])
    args = ap.parse_args()
    costs = Costs()
    bars = load_bars("1d")
    print(f"Daily bars {len(bars):,}  {bars.timestamp.iloc[0].date()} → "
          f"{bars.timestamp.iloc[-1].date()}   costs {costs.round_trip_pct:.2f}% round trip")

    if args.check in ("all", "headline"):
        check_headline(bars, costs)
    if args.check in ("all", "years"):
        check_years(bars, costs)
    if args.check in ("all", "sens"):
        check_sensitivity(bars, costs)
    if args.check in ("all", "costs"):
        check_costs(bars, costs)
    if args.check in ("all", "wf"):
        check_walkforward(bars, costs)


if __name__ == "__main__":
    main()
