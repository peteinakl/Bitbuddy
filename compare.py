"""Head-to-head comparison over the full history and each sub-period.

Reports the buy & hold benchmark on identical footing, because for a long-only
spot bot on a single asset that is the benchmark that actually matters.
"""

import argparse

import numpy as np
import pandas as pd

from backtest import Backtester, Costs, Result, load_bars
from strategies import (
    LegacyBotStrategy,
    RegimeTrendStrategy,
    TrendAtrStrategy,
    VolTargetTrendStrategy,
)

PERIODS = [
    ("full          2023-01 → 2026-07", None, None),
    ("bull  2023-01 → 2025-06", None, "2025-07-01"),
    ("bear  2025-07 → 2026-07", "2025-07-01", None),
]


def resample(df, rule):
    return (
        df.set_index("timestamp")
        .resample(rule)
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
        .reset_index()
    )


def bh_metrics(bars, start=None):
    c = bars["close"].reset_index(drop=True)
    if start is not None:
        mask = bars["timestamp"] >= pd.Timestamp(start, tz="UTC")
        c = bars.loc[mask, "close"].reset_index(drop=True)
        ts = bars.loc[mask, "timestamp"].reset_index(drop=True)
    else:
        ts = bars["timestamp"].reset_index(drop=True)
    curve = pd.Series((c / c.iloc[0]).values, index=pd.DatetimeIndex(ts))
    daily = curve.resample("D").last().dropna().pct_change(fill_method=None).dropna()
    years = (ts.iloc[-1] - ts.iloc[0]).total_seconds() / (365.25 * 86400)
    cagr = curve.iloc[-1] ** (1 / years) - 1 if years > 0 else 0.0
    dd = float((curve / curve.cummax() - 1).min())
    sharpe = float(daily.mean() / daily.std() * np.sqrt(365)) if daily.std() > 0 else 0.0
    return {
        "ret": float(curve.iloc[-1] - 1), "cagr": cagr, "dd": dd, "sharpe": sharpe,
        "calmar": cagr / abs(dd) if dd else 0.0, "n": 0, "exposure": 1.0,
    }


def strat_metrics(r: Result, bars_total: int) -> dict:
    # Fraction of bars with a position on, inferred from trade spans
    held = sum(t.bars_held for t in r.trades)
    return {
        "ret": r.total_return, "cagr": r.cagr, "dd": r.max_drawdown,
        "sharpe": r.sharpe, "calmar": r.calmar, "n": len(r.trades),
        "exposure": min(held / bars_total, 1.0) if bars_total else 0.0,
    }


def row(label, m):
    return (f"{label:<34} {m['ret']*100:>9.1f}% {m['cagr']*100:>8.1f}% {m['dd']*100:>8.1f}% "
            f"{m['sharpe']:>7.2f} {m['calmar']:>7.2f} {m['n']:>6} {m['exposure']*100:>8.0f}%")


def header(title):
    print(f"\n{title}")
    print(f"{'':<34} {'return':>10} {'CAGR':>9} {'maxDD':>9} {'Sharpe':>7} {'Calmar':>7} "
          f"{'trades':>6} {'exposure':>9}")
    print("-" * 104)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/BTCUSDT_5m.csv")
    ap.add_argument("--include-legacy", action="store_true",
                    help="also run the current bot's rules (slow: 375k 5m bars)")
    args = ap.parse_args()

    costs = Costs()
    bars5 = load_bars(args.data)
    bars1d = resample(bars5, "1D")
    bars4h = resample(bars5, "4H")

    candidates = [
        ("vol-target 50/200 exit50", bars1d, "1D",
         lambda: VolTargetTrendStrategy(fast_ma=50, slow_ma=200, exit_ma=50, target_vol=0.5)),
        ("vol-target 50/200 exit100", bars1d, "1D",
         lambda: VolTargetTrendStrategy(fast_ma=50, slow_ma=200, exit_ma=100, target_vol=0.5)),
        ("vol-target no vol cap", bars1d, "1D",
         lambda: VolTargetTrendStrategy(fast_ma=50, slow_ma=200, exit_ma=50, target_vol=9.0)),
        ("vol-target no confirm", bars1d, "1D",
         lambda: VolTargetTrendStrategy(fast_ma=50, slow_ma=200, exit_ma=50, target_vol=0.5,
                                        require_confirm=False)),
        ("regime trend 20/100", bars1d, "1D",
         lambda: RegimeTrendStrategy(entry_ma=20, exit_ma=100)),
        ("vol-target on 4h 300/1200", bars4h, "4H",
         lambda: VolTargetTrendStrategy(fast_ma=300, slow_ma=1200, exit_ma=300,
                                        target_vol=0.5, bars_per_year=6 * 365)),
        ("donchian trend+ATR", bars1d, "1D",
         lambda: TrendAtrStrategy(breakout_window=20, trend_window=100, stop_atr=3.0,
                                  trail_atr=6.0, atr_period=14)),
    ]
    if args.include_legacy:
        candidates.append(("legacy (current bot)", bars5, "5m", LegacyBotStrategy))

    for title, start, end in PERIODS:
        header(title)
        for name, bars, tf, factory in candidates:
            sub = bars
            trade_start = None
            if start:
                trade_start = pd.Timestamp(start, tz="UTC")
            if end:
                sub = bars[bars["timestamp"] < pd.Timestamp(end, tz="UTC")].reset_index(drop=True)
            bt = Backtester(sub, factory(), costs, initial_equity=10_000.0,
                            bar_interval=tf, trade_start=trade_start)
            r = bt.run()
            print(row(name, strat_metrics(r, len(r.equity))))
        print(row("buy & hold", bh_metrics(
            bars1d if not end else bars1d[bars1d["timestamp"] < pd.Timestamp(end, tz="UTC")], start)))


if __name__ == "__main__":
    main()
