"""Compare strategies on historical BTC data with realistic costs.

Usage:
    python run_backtest.py                      # all strategies, full history
    python run_backtest.py --strategy legacy    # one strategy
    python run_backtest.py --start 2025-01-01   # subset
    python run_backtest.py --walkforward        # out-of-sample validation
"""

import argparse
import time

import pandas as pd

from backtest import Backtester, Costs, load_bars
from strategies import LegacyBotStrategy, TrendAtrStrategy


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    out = (
        df.set_index("timestamp")
        .resample(rule)
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
        .reset_index()
    )
    return out


def run_one(bars, strategy, interval, costs, equity=10_000.0, **kw):
    t0 = time.time()
    bt = Backtester(bars, strategy, costs, initial_equity=equity, bar_interval=interval, **kw)
    result = bt.run()
    elapsed = time.time() - t0
    print(result.summary(f"{strategy.name}  [{interval} bars]"))
    print(f"  ({len(bars):,} bars in {elapsed:.1f}s)")
    print()
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/BTCUSDT_5m.csv")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--strategy", default="all", choices=["all", "legacy", "trend"])
    ap.add_argument("--fee-bps", type=float, default=10.0)
    ap.add_argument("--slippage-bps", type=float, default=2.0)
    ap.add_argument("--equity", type=float, default=10_000.0)
    args = ap.parse_args()

    costs = Costs(fee_bps=args.fee_bps, slippage_bps=args.slippage_bps)
    bars5 = load_bars(args.data, args.start, args.end)
    print(f"Data: {len(bars5):,} 5m bars, {bars5.timestamp.iloc[0].date()} → {bars5.timestamp.iloc[-1].date()}")
    print(f"Costs: {costs.fee_bps}bps fee/side + {costs.slippage_bps}bps slippage "
          f"({(costs.fee_bps * 2 + costs.slippage_bps) / 100:.2f}% round trip)\n")

    if args.strategy in ("all", "legacy"):
        run_one(bars5, LegacyBotStrategy(), "5m", costs, args.equity)

    if args.strategy in ("all", "trend"):
        bars1h = resample(bars5, "1H")
        run_one(bars1h, TrendAtrStrategy(), "1h", costs, args.equity)


if __name__ == "__main__":
    main()
