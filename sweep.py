"""Parameter exploration with an honest train/test split.

The split is deliberate: the out-of-sample block contains BTC's 2026 bear market
(-54% peak to trough). A long-only strategy tuned on 2023-2025 (mostly up) and
then tested there has nowhere to hide.

Ranking is on in-sample results only. Out-of-sample numbers are reported but
never used to pick parameters -- otherwise the "out-of-sample" test is just a
slower form of overfitting.

Usage:
    python sweep.py --strategy regime --timeframe 1D
    python sweep.py --strategy regime --all-timeframes
    python sweep.py --strategy trend
"""

import argparse
import itertools
from typing import Dict, List

import pandas as pd

from backtest import Backtester, Costs, load_bars
from strategies import RegimeTrendStrategy, TrendAtrStrategy, VolTargetTrendStrategy

SPLIT = "2025-07-01"  # train before, test after


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    return (
        df.set_index("timestamp")
        .resample(rule)
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
        .reset_index()
    )


def evaluate(bars, factory, interval, costs, equity=10_000.0, trade_start=None) -> Dict:
    bt = Backtester(bars, factory(), costs, initial_equity=equity, bar_interval=interval,
                    trade_start=trade_start)
    r = bt.run()
    return {
        "ret": r.total_return,
        "cagr": r.cagr,
        "dd": r.max_drawdown,
        "sharpe": r.sharpe,
        "calmar": r.calmar,
        "trades": len(r.trades),
        "win": r.win_rate,
        "pf": r.profit_factor,
        "result": r,
    }


def buy_hold(bars) -> Dict:
    c = bars["close"]
    curve = c / c.iloc[0]
    dd = float((curve / curve.cummax() - 1).min())
    return {"ret": float(curve.iloc[-1] - 1), "dd": dd}


def sweep(strategy_name: str, timeframe: str, bars5: pd.DataFrame, costs: Costs) -> List[Dict]:
    bars = resample(bars5, timeframe)
    split_ts = pd.Timestamp(SPLIT, tz="UTC")
    train = bars[bars["timestamp"] < split_ts].reset_index(drop=True)
    # The test set carries the full preceding history so indicators are already
    # warm at the split; trade_start stops it from trading in that prefix.
    test = bars.reset_index(drop=True)

    if strategy_name == "voltarget":
        bpy = {"4H": 6 * 365.0, "12H": 2 * 365.0, "1D": 365.0}.get(timeframe, 365.0)
        grid = list(itertools.product(
            [20, 30, 50, 80],          # fast_ma
            [100, 150, 200],           # slow_ma
            [30, 50, 80, 100, 150],    # exit_ma
            [0.50, 0.70, 1.00, 2.00],  # target_vol (2.0 ~= always max size)
        ))
        grid = [p for p in grid if p[1] > p[0]]

        def make(p):
            fast, slow, ex, tv = p
            return lambda: VolTargetTrendStrategy(
                fast_ma=fast, slow_ma=slow, exit_ma=ex, target_vol=tv, bars_per_year=bpy
            )

        label = lambda p: f"fast={p[0]:<4} slow={p[1]:<4} exit={p[2]:<4} tgt_vol={p[3]:<5}"
    elif strategy_name == "regime":
        grid = list(itertools.product(
            [20, 30, 50, 80, 100, 150],   # entry_ma
            [30, 50, 80, 100, 150, 200],  # exit_ma
            [4.0, 6.0, 10.0],             # stop_atr
        ))
        def make(p):
            entry_ma, exit_ma, stop_atr = p
            return lambda: RegimeTrendStrategy(entry_ma=entry_ma, exit_ma=exit_ma, stop_atr=stop_atr)
        label = lambda p: f"entry_ma={p[0]:<4} exit_ma={p[1]:<4} stop_atr={p[2]:<5}"
        # exit line slower than entry line is the sane configuration
        grid = [p for p in grid if p[1] >= p[0]]
    else:
        grid = list(itertools.product(
            [20, 50, 100],      # breakout_window
            [100, 200, 400],    # trend_window
            [2.0, 3.0],         # stop_atr
            [4.0, 6.0, 8.0],    # trail_atr
        ))
        def make(p):
            bw, tw, sa, ta = p
            return lambda: TrendAtrStrategy(breakout_window=bw, trend_window=tw,
                                            stop_atr=sa, trail_atr=ta)
        label = lambda p: f"breakout={p[0]:<4} trend={p[1]:<4} stop={p[2]:<4} trail={p[3]:<4}"

    rows = []
    for p in grid:
        factory = make(p)
        try:
            tr = evaluate(train, factory, timeframe, costs)
            te = evaluate(test, factory, timeframe, costs, trade_start=split_ts)
        except Exception as exc:  # a degenerate parameter set should not stop the sweep
            print(f"  skip {label(p)}: {exc}")
            continue
        rows.append({"params": p, "label": label(p), "train": tr, "test": te})
    return rows


def report(rows: List[Dict], train_bh: Dict, test_bh: Dict, top: int = 12) -> None:
    # Rank on in-sample Calmar (return per unit of drawdown), never on test data
    rows = sorted(rows, key=lambda r: -r["train"]["calmar"])
    print(f"{'parameters':<44} {'IS ret':>8} {'IS dd':>7} {'IS shp':>7} {'IS n':>5} "
          f"| {'OOS ret':>8} {'OOS dd':>7} {'OOS shp':>7} {'OOS n':>5}")
    print("-" * 118)
    for r in rows[:top]:
        t, s = r["train"], r["test"]
        print(f"{r['label']:<44} {t['ret']*100:>7.1f}% {t['dd']*100:>6.1f}% {t['sharpe']:>7.2f} "
              f"{t['trades']:>5} | {s['ret']*100:>7.1f}% {s['dd']*100:>6.1f}% {s['sharpe']:>7.2f} {s['trades']:>5}")
    print("-" * 118)
    print(f"{'buy & hold':<44} {train_bh['ret']*100:>7.1f}% {train_bh['dd']*100:>6.1f}% "
          f"{'':>7} {'':>5} | {test_bh['ret']*100:>7.1f}% {test_bh['dd']*100:>6.1f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/BTCUSDT_5m.csv")
    ap.add_argument("--strategy", default="regime", choices=["regime", "trend", "voltarget"])
    ap.add_argument("--timeframe", default="1D")
    ap.add_argument("--all-timeframes", action="store_true")
    ap.add_argument("--top", type=int, default=12)
    args = ap.parse_args()

    costs = Costs()
    bars5 = load_bars(args.data)
    timeframes = ["4H", "12H", "1D"] if args.all_timeframes else [args.timeframe]

    for tf in timeframes:
        bars = resample(bars5, tf)
        train = bars[bars["timestamp"] < pd.Timestamp(SPLIT, tz="UTC")]
        test = bars[bars["timestamp"] >= pd.Timestamp(SPLIT, tz="UTC")]
        print(f"\n{'=' * 118}")
        print(f"{args.strategy} on {tf} bars   train={len(train):,} bars  test={len(test):,} bars  "
              f"(split {SPLIT})")
        print("=" * 118)
        rows = sweep(args.strategy, tf, bars5, costs)
        report(rows, buy_hold(train), buy_hold(test), args.top)


if __name__ == "__main__":
    main()
