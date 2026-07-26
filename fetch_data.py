"""Download historical BTCUSDT bars from Binance into data/.

    python fetch_data.py                          # 1d since 2017-08 (Binance listing)
    python fetch_data.py --interval 4h
    python fetch_data.py --interval 1d --start 2020-01-01
"""

import argparse
import logging
import time
from datetime import datetime, timezone

import pandas as pd

from bitbuddy.data import INTERVAL_MS, fetch_klines, save_bars


def to_ms(date_str: str) -> int:
    return int(datetime.strptime(date_str, "%Y-%m-%d")
               .replace(tzinfo=timezone.utc).timestamp() * 1000)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--interval", default="1d", choices=sorted(INTERVAL_MS))
    ap.add_argument("--start", default="2017-08-01", help="UTC date YYYY-MM-DD")
    ap.add_argument("--end", default=None)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S")
    start_ms = to_ms(args.start)
    end_ms = to_ms(args.end) if args.end else int(time.time() * 1000)

    logging.info("fetching %s %s from %s", args.symbol, args.interval, args.start)
    df = fetch_klines(args.symbol, args.interval, start_ms, end_ms)
    path = save_bars(df, args.interval, args.symbol)

    days = (df["timestamp"].iloc[-1] - df["timestamp"].iloc[0]).total_seconds() / 86400
    logging.info("wrote %s: %d bars, %s -> %s (%.0f days)", path, len(df),
                 df["timestamp"].iloc[0].date(), df["timestamp"].iloc[-1].date(), days)

    gaps = df["timestamp"].diff().dropna()
    expected = pd.Timedelta(milliseconds=INTERVAL_MS[args.interval])
    n = int((gaps > expected).sum())
    if n:
        logging.warning("%d gaps in series (largest %s)", n, gaps.max())


if __name__ == "__main__":
    main()
