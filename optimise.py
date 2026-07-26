"""Search strategy families for the best BTC configuration.

Protocol:

    2017-08 ────────── development ────────── 2024-07 ──── holdout ──── 2026-07
              search and select here                    scored once at the end

Development is split into consecutive blocks and configs are ranked by median
Sharpe across them, so a config has to work in several regimes rather than one.
The holdout is only scored after selection is final.

Usage:
    python optimise.py                    # full search, all families
    python optimise.py --family matrend
    python optimise.py --timeframe 4h
    python optimise.py --quick            # coarse grids
"""

import argparse
import time

import pandas as pd

from bitbuddy.costs import Costs
from bitbuddy.data import load_bars, slice_bars
from bitbuddy.research.search import grid, rank, report, search
from bitbuddy.research.walkforward import curve_stats, evaluate, split_blocks
from bitbuddy.strategies import Donchian, DualMomentum, MaTrend, TsMomentum

DEV_END = "2024-07-01"      # everything after this is untouched until the end
HOLDOUT_END = None


def scale(values, factor):
    """Scale bar-count parameters when moving to a finer timeframe."""
    return [max(2, int(round(v * factor))) for v in values]


def build_grids(timeframe: str, quick: bool):
    # 1d is the native design timeframe; finer timeframes scale the windows so
    # each config spans a comparable amount of wall-clock time.
    f = {"1d": 1, "4h": 6, "1h": 24}[timeframe]

    if quick:
        ma_entry, ma_exit, ma_slow = [30, 50, 100], [30, 50, 100], [150, 200]
        mom_lb = [60, 90, 120]
        dc_bo, dc_ex = [30, 50], [15, 25]
        vols = [0.5, None]
    else:
        ma_entry = [20, 30, 50, 80, 100]
        ma_exit = [20, 30, 50, 80, 100, 150]
        ma_slow = [100, 150, 200, 300]
        mom_lb = [30, 60, 90, 120, 180, 270]
        dc_bo, dc_ex = [20, 30, 50, 80], [10, 15, 25, 40]
        vols = [0.4, 0.5, 0.7, None]

    return {
        "matrend": (MaTrend, grid(
            entry_ma=scale(ma_entry, f), exit_ma=scale(ma_exit, f),
            slow_ma=scale(ma_slow, f), target_vol=vols, confirm=[True, False],
        ), {"stop_atr": 8.0, "atr_period": max(2, int(14 * f)),
            "vol_window": max(5, int(30 * f))}),

        "tsmom": (TsMomentum, grid(
            lookback=scale(mom_lb, f), target_vol=vols,
            exit_thresh=[0.0, -0.02, -0.05],
        ), {"stop_atr": 8.0, "atr_period": max(2, int(14 * f)),
            "vol_window": max(5, int(30 * f))}),

        "dualmom": (DualMomentum, grid(
            lookback=scale(mom_lb, f), slow_ma=scale(ma_slow, f),
            exit_ma=scale(ma_exit, f), target_vol=vols,
        ), {"stop_atr": 8.0, "atr_period": max(2, int(14 * f)),
            "vol_window": max(5, int(30 * f))}),

        "donchian": (Donchian, grid(
            breakout=scale(dc_bo, f), exit_window=scale(dc_ex, f),
            trend_ma=scale(ma_slow, f) + [None], target_vol=vols,
        ), {"stop_atr": 3.0, "trail_atr": 6.0, "atr_period": max(2, int(14 * f)),
            "vol_window": max(5, int(30 * f))}),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeframe", default="1d", choices=["1d", "4h", "1h"])
    ap.add_argument("--family", default="all")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--blocks", type=int, default=4)
    ap.add_argument("--top", type=int, default=8)
    args = ap.parse_args()

    costs = Costs()
    bars = load_bars(args.timeframe)
    dev = slice_bars(bars, None, DEV_END)

    print(f"Data      {len(bars):,} {args.timeframe} bars  "
          f"{bars.timestamp.iloc[0].date()} → {bars.timestamp.iloc[-1].date()}")
    print(f"Dev set   {len(dev):,} bars  → {DEV_END}   (search here)")
    print(f"Holdout   {len(bars) - len(dev):,} bars  {DEV_END} → "
          f"{bars.timestamp.iloc[-1].date()}   (scored once)")
    print(f"Costs     {costs.round_trip_pct:.2f}% round trip")

    grids = build_grids(args.timeframe, args.quick)
    families = list(grids) if args.family == "all" else [args.family]

    all_cands = []
    for fam in families:
        cls, pgrid, fixed = grids[fam]
        t0 = time.time()
        cands = search(bars, fam, cls, pgrid, costs, interval=args.timeframe,
                       n_blocks=args.blocks, dev_end=DEV_END, min_trades=8,
                       fixed=fixed)
        ranked = rank(cands, "median_sharpe")
        print(f"\n{'=' * 90}")
        print(f"{fam}: {len(pgrid)} configs, {len(cands)} scored in {time.time() - t0:.1f}s")
        report(ranked, args.top)
        all_cands.extend(ranked[:args.top])

    # ---- cross-family shortlist, still development-only -------------------
    best = rank(all_cands, "median_sharpe")
    print(f"\n{'=' * 90}")
    print("CROSS-FAMILY SHORTLIST (development set only)")
    report(best, 12)

    # ---- score the shortlist on the holdout, once -------------------------
    print(f"\n{'=' * 90}")
    print(f"HOLDOUT {DEV_END} → {bars.timestamp.iloc[-1].date()}  (first and only look)")
    print(f"  {'configuration':<52}{'ret':>9}{'CAGR':>8}{'maxDD':>8}{'Sharpe':>8}{'n':>5}")
    print("  " + "-" * 88)
    rows = []
    for c in best[:12]:
        r = evaluate(bars, c.build, costs, args.timeframe, DEV_END, HOLDOUT_END)
        rows.append((c, r))
        print(f"  {c.label:<52}{r.total_return * 100:>8.1f}%{r.cagr * 100:>7.1f}%"
              f"{r.max_drawdown * 100:>7.1f}%{r.sharpe:>8.2f}{len(r.trades):>5}")
    if rows:
        bh = rows[0][1]
        print(f"  {'buy & hold':<52}{bh.benchmark_return * 100:>8.1f}%{'':>8}"
              f"{bh.benchmark_max_drawdown * 100:>7.1f}%")
    print("\n  Development ranking is the honest selection. Holdout column shows"
          "\n  whether that selection generalised; do not re-pick using it.")


if __name__ == "__main__":
    main()
