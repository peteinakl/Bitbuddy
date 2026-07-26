"""Robustness validation for the long-only trend strategy.

Four independent checks, each able to kill the strategy on its own:

1. **Rolling walk-forward.** Parameters are re-chosen on each training window and
   applied to the next unseen window; the out-of-sample slices are stitched into
   one equity curve. This is the closest thing to an honest "what would have
   happened" answer.

2. **Fixed vs refitted parameters.** The same walk-forward run with one fixed,
   conventional parameter set (50/200). If fixed does as well as refitted, the
   edge is structural rather than fitted -- which is the good outcome, and argues
   for shipping the simpler configuration.

3. **Parameter sensitivity.** Performance across the neighbourhood of the chosen
   values. A broad plateau means the result survives being slightly wrong; a
   lone spike means it was curve-fit.

4. **Cost shock.** Re-run at 2x and 4x the assumed fees. An edge that evaporates
   when costs rise was never an edge, it was a rebate on optimism.

Usage:
    python validate.py                 # all checks
    python validate.py --check wf
"""

import argparse
import itertools
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from backtest import Backtester, Costs, load_bars
from strategies import VolTargetTrendStrategy

# Neighbourhood explored when refitting. Deliberately coarse: a fine grid on
# ~3 years of daily bars would fit noise.
GRID = list(itertools.product(
    [20, 30, 50, 80],     # fast_ma
    [100, 150, 200],      # slow_ma
    [30, 50, 80],         # exit_ma
))
GRID = [p for p in GRID if p[1] > p[0]]

FIXED = (50, 200, 50)


def resample(df, rule="1D"):
    return (
        df.set_index("timestamp")
        .resample(rule)
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
        .reset_index()
    )


def make(params, target_vol=0.5):
    fast, slow, ex = params
    return lambda: VolTargetTrendStrategy(fast_ma=fast, slow_ma=slow, exit_ma=ex,
                                          target_vol=target_vol)


def run_window(
    bars: pd.DataFrame,
    params: Tuple,
    costs: Costs,
    trade_start: Optional[pd.Timestamp] = None,
    trade_end: Optional[pd.Timestamp] = None,
    equity: float = 10_000.0,
):
    """Backtest with full history for warmup but trading confined to a window."""
    sub = bars
    if trade_end is not None:
        sub = bars[bars["timestamp"] < trade_end].reset_index(drop=True)
    bt = Backtester(sub, make(params)(), costs, initial_equity=equity,
                    bar_interval="1D", trade_start=trade_start)
    return bt.run()


def metrics(r) -> Dict:
    return {"ret": r.total_return, "dd": r.max_drawdown, "sharpe": r.sharpe,
            "calmar": r.calmar, "n": len(r.trades)}


def bh_over(bars, start, end) -> Dict:
    m = (bars["timestamp"] >= start) & (bars["timestamp"] < end)
    c = bars.loc[m, "close"].reset_index(drop=True)
    if len(c) < 2:
        return {"ret": 0.0, "dd": 0.0, "sharpe": 0.0}
    ts = pd.DatetimeIndex(bars.loc[m, "timestamp"])
    curve = pd.Series((c / c.iloc[0]).values, index=ts)
    daily = curve.resample("D").last().dropna().pct_change(fill_method=None).dropna()
    return {
        "ret": float(curve.iloc[-1] - 1),
        "dd": float((curve / curve.cummax() - 1).min()),
        "sharpe": float(daily.mean() / daily.std() * np.sqrt(365)) if daily.std() > 0 else 0.0,
    }


# ---------------------------------------------------------------------------
# 1 + 2. walk-forward
# ---------------------------------------------------------------------------
def walk_forward(bars, costs, train_months=18, test_months=6, refit=True, verbose=True):
    start = bars["timestamp"].iloc[0]
    end = bars["timestamp"].iloc[-1]

    windows = []
    test_start = start + pd.DateOffset(months=train_months)
    while test_start < end:
        test_end = min(test_start + pd.DateOffset(months=test_months), end)
        if (test_end - test_start).days < 60:
            break
        windows.append((test_start - pd.DateOffset(months=train_months), test_start, test_end))
        test_start = test_end

    stitched = 1.0
    curve_parts: List[pd.Series] = []
    rows = []

    for train_start, ts, te in windows:
        if refit:
            best, best_score = None, -1e9
            for p in GRID:
                r = run_window(bars, p, costs, trade_start=train_start, trade_end=ts)
                if len(r.trades) < 3:
                    continue          # too few trades to judge
                score = r.sharpe
                if score > best_score:
                    best, best_score = p, score
            if best is None:
                best = FIXED
        else:
            best = FIXED

        r = run_window(bars, best, costs, trade_start=ts, trade_end=te)
        m = metrics(r)
        bh = bh_over(bars, ts, te)
        rows.append({"test": f"{ts.date()} → {te.date()}", "params": best,
                     "oos": m, "bh": bh})
        # Chain the window's return onto the stitched curve
        seg = r.equity / r.equity.iloc[0] * stitched
        curve_parts.append(seg)
        stitched = float(seg.iloc[-1])

        if verbose:
            tag = f"{best[0]}/{best[1]}/{best[2]}"
            print(f"  {ts.date()} → {te.date()}  params {tag:<12} "
                  f"OOS {m['ret']*100:>+7.1f}%  dd {m['dd']*100:>6.1f}%  "
                  f"shp {m['sharpe']:>5.2f}  n={m['n']:<3} | B&H {bh['ret']*100:>+7.1f}%")

    curve = pd.concat(curve_parts) if curve_parts else pd.Series(dtype=float)
    curve = curve[~curve.index.duplicated(keep="last")]
    return rows, curve


def curve_stats(curve: pd.Series) -> Dict:
    if len(curve) < 2:
        return {"ret": 0.0, "dd": 0.0, "sharpe": 0.0, "cagr": 0.0}
    daily = curve.resample("D").last().dropna().pct_change(fill_method=None).dropna()
    years = (curve.index[-1] - curve.index[0]).total_seconds() / (365.25 * 86400)
    total = float(curve.iloc[-1] / curve.iloc[0] - 1)
    return {
        "ret": total,
        "cagr": (1 + total) ** (1 / years) - 1 if years > 0 else 0.0,
        "dd": float((curve / curve.cummax() - 1).min()),
        "sharpe": float(daily.mean() / daily.std() * np.sqrt(365)) if daily.std() > 0 else 0.0,
    }


def check_walk_forward(bars, costs):
    print("\n" + "=" * 96)
    print("1. ROLLING WALK-FORWARD  (parameters refit on each 18m train, applied to next 6m)")
    print("=" * 96)
    rows_refit, curve_refit = walk_forward(bars, costs, refit=True)

    print("\n" + "=" * 96)
    print(f"2. SAME WINDOWS, FIXED PARAMETERS {FIXED[0]}/{FIXED[1]}/{FIXED[2]} (no refitting)")
    print("=" * 96)
    rows_fixed, curve_fixed = walk_forward(bars, costs, refit=False)

    if len(curve_refit) and len(curve_fixed):
        a, b = curve_stats(curve_refit), curve_stats(curve_fixed)
        first = curve_refit.index[0]
        last = curve_refit.index[-1]
        bh = bh_over(bars, first, last + pd.Timedelta(days=1))
        print(f"\nStitched out-of-sample, {first.date()} → {last.date()}:")
        print(f"{'':<26}{'return':>10}{'CAGR':>9}{'maxDD':>9}{'Sharpe':>8}")
        print("-" * 62)
        print(f"{'refitted per window':<26}{a['ret']*100:>9.1f}%{a['cagr']*100:>8.1f}%"
              f"{a['dd']*100:>8.1f}%{a['sharpe']:>8.2f}")
        print(f"{'fixed 50/200/50':<26}{b['ret']*100:>9.1f}%{b['cagr']*100:>8.1f}%"
              f"{b['dd']*100:>8.1f}%{b['sharpe']:>8.2f}")
        print(f"{'buy & hold':<26}{bh['ret']*100:>9.1f}%{'':>9}{bh['dd']*100:>8.1f}%{bh['sharpe']:>8.2f}")
    return curve_refit, curve_fixed


# ---------------------------------------------------------------------------
# 3. parameter sensitivity
# ---------------------------------------------------------------------------
def check_sensitivity(bars, costs):
    print("\n" + "=" * 96)
    print("3. PARAMETER SENSITIVITY  (full period; looking for a plateau, not a spike)")
    print("=" * 96)
    results = []
    for p in GRID:
        r = run_window(bars, p, costs)
        results.append((p, metrics(r)))

    sharpes = [m["sharpe"] for _, m in results]
    print(f"  {len(results)} configurations")
    print(f"  Sharpe   median {np.median(sharpes):.2f}   mean {np.mean(sharpes):.2f}   "
          f"min {min(sharpes):.2f}   max {max(sharpes):.2f}")
    profitable = sum(1 for _, m in results if m["ret"] > 0)
    beat_1 = sum(1 for s in sharpes if s > 1.0)
    print(f"  profitable in {profitable}/{len(results)} configs "
          f"({profitable/len(results)*100:.0f}%)")
    print(f"  Sharpe > 1.0 in {beat_1}/{len(results)} configs "
          f"({beat_1/len(results)*100:.0f}%)")
    print("\n  worst 3 configurations:")
    for p, m in sorted(results, key=lambda x: x[1]["sharpe"])[:3]:
        print(f"    {p[0]}/{p[1]}/{p[2]:<4} ret {m['ret']*100:>+7.1f}%  dd {m['dd']*100:>6.1f}%  "
              f"shp {m['sharpe']:>5.2f}")
    print("  chosen configuration:")
    for p, m in results:
        if p == FIXED:
            print(f"    {p[0]}/{p[1]}/{p[2]:<4} ret {m['ret']*100:>+7.1f}%  dd {m['dd']*100:>6.1f}%  "
                  f"shp {m['sharpe']:>5.2f}  <- shipping this")
    return results


# ---------------------------------------------------------------------------
# 4. cost shock
# ---------------------------------------------------------------------------
def check_costs(bars):
    print("\n" + "=" * 96)
    print("4. COST SHOCK  (does the edge survive worse execution?)")
    print("=" * 96)
    print(f"  {'costs':<34}{'return':>10}{'maxDD':>9}{'Sharpe':>8}{'trades':>8}")
    print("-" * 70)
    for mult in (1, 2, 4, 8):
        c = Costs(fee_bps=10.0 * mult, slippage_bps=2.0 * mult,
                  stop_slippage_bps=8.0 * mult)
        r = run_window(bars, FIXED, c)
        m = metrics(r)
        rt = (c.fee_bps * 2 + c.slippage_bps) / 100
        print(f"  {f'{mult}x  ({rt:.2f}% round trip)':<34}{m['ret']*100:>9.1f}%"
              f"{m['dd']*100:>8.1f}%{m['sharpe']:>8.2f}{m['n']:>8}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/BTCUSDT_5m.csv")
    ap.add_argument("--check", default="all", choices=["all", "wf", "sens", "costs"])
    args = ap.parse_args()

    costs = Costs()
    bars = resample(load_bars(args.data), "1D")
    print(f"Daily bars: {len(bars)}  {bars.timestamp.iloc[0].date()} → {bars.timestamp.iloc[-1].date()}")

    if args.check in ("all", "wf"):
        check_walk_forward(bars, costs)
    if args.check in ("all", "sens"):
        check_sensitivity(bars, costs)
    if args.check in ("all", "costs"):
        check_costs(bars)


if __name__ == "__main__":
    main()
