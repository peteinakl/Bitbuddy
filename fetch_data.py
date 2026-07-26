"""Download historical BTCUSDT klines from Binance for backtesting.

Writes a single parquet/csv to data/ (gitignored). Public endpoint, no API key.

Usage:
    python fetch_data.py                    # 5m bars, 2023-01-01 -> now
    python fetch_data.py --interval 1h --start 2020-01-01
"""

import argparse
import logging
import os
import time
from datetime import datetime, timezone

import pandas as pd
import requests

KLINES_URL = "https://api.binance.com/api/v3/klines"
MAX_LIMIT = 1000

# Binance kline column layout
COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore",
]

INTERVAL_MS = {
    "1m": 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
}


def to_ms(date_str: str) -> int:
    return int(datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def fetch_klines(symbol: str, interval: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    """Page through the klines endpoint until end_ms, respecting rate limits."""
    step = INTERVAL_MS[interval]
    rows = []
    cursor = start_ms
    session = requests.Session()
    requests_made = 0

    while cursor < end_ms:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": cursor,
            "endTime": end_ms,
            "limit": MAX_LIMIT,
        }
        try:
            resp = session.get(KLINES_URL, params=params, timeout=20)
        except requests.RequestException as exc:
            logging.warning("Request failed (%s), retrying in 5s", exc)
            time.sleep(5)
            continue

        if resp.status_code == 429 or resp.status_code == 418:
            wait = int(resp.headers.get("Retry-After", 30))
            logging.warning("Rate limited, sleeping %ss", wait)
            time.sleep(wait)
            continue
        if resp.status_code != 200:
            logging.error("HTTP %s: %s", resp.status_code, resp.text[:200])
            break

        batch = resp.json()
        if not batch:
            break

        rows.extend(batch)
        # Advance past the last bar we received
        cursor = batch[-1][0] + step
        requests_made += 1

        if requests_made % 25 == 0:
            last = datetime.fromtimestamp(batch[-1][0] / 1000, tz=timezone.utc)
            logging.info("  %d requests, %d bars, through %s", requests_made, len(rows), last.date())

        # Klines with limit=1000 cost weight 5; the 1200/min budget allows ~240 req/min.
        time.sleep(0.05)

    if not rows:
        raise RuntimeError("No data returned")

    df = pd.DataFrame(rows, columns=COLUMNS)
    df = df[["open_time", "open", "high", "low", "close", "volume", "trades"]]
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["trades"] = pd.to_numeric(df["trades"], errors="coerce").astype("int64")
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.drop(columns=["open_time"])

    # Binance can return overlapping bars across page boundaries
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)
    return df[["timestamp", "open", "high", "low", "close", "volume", "trades"]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="5m", choices=sorted(INTERVAL_MS))
    parser.add_argument("--start", default="2023-01-01", help="UTC date, YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="UTC date, YYYY-MM-DD (default: now)")
    parser.add_argument("--outdir", default="data")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    start_ms = to_ms(args.start)
    end_ms = to_ms(args.end) if args.end else int(time.time() * 1000)

    logging.info("Fetching %s %s from %s to %s", args.symbol, args.interval, args.start, args.end or "now")
    df = fetch_klines(args.symbol, args.interval, start_ms, end_ms)

    os.makedirs(args.outdir, exist_ok=True)
    path = os.path.join(args.outdir, f"{args.symbol}_{args.interval}.csv")
    df.to_csv(path, index=False)

    span_days = (df["timestamp"].iloc[-1] - df["timestamp"].iloc[0]).total_seconds() / 86400
    logging.info(
        "Wrote %s: %d bars, %s -> %s (%.0f days)",
        path, len(df), df["timestamp"].iloc[0].date(), df["timestamp"].iloc[-1].date(), span_days,
    )
    # Gap check - missing bars distort indicator windows
    gaps = df["timestamp"].diff().dropna()
    expected = pd.Timedelta(milliseconds=INTERVAL_MS[args.interval])
    n_gaps = int((gaps > expected).sum())
    if n_gaps:
        logging.warning("%d gaps in series (largest %s)", n_gaps, gaps.max())


if __name__ == "__main__":
    main()
