"""Bar loading, resampling and downloading.

Canonical form is a DataFrame with columns:
    timestamp (tz-aware UTC), open, high, low, close, volume
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import List, Optional

import pandas as pd
import requests

KLINES_URL = "https://api.binance.com/api/v3/klines"
MAX_LIMIT = 1000
DATA_DIR = "data"

OHLC = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}

# Bars per year, used to annualise volatility per timeframe
BARS_PER_YEAR = {"5m": 288 * 365, "15m": 96 * 365, "1h": 24 * 365,
                 "4h": 6 * 365, "12h": 2 * 365, "1d": 365.0}

INTERVAL_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000,
               "4h": 14_400_000, "12h": 43_200_000, "1d": 86_400_000}

# Pandas resample rule per interval (pandas <2.2 aliases)
RESAMPLE_RULE = {"5m": "5min", "15m": "15min", "1h": "1H", "4h": "4H",
                 "12h": "12H", "1d": "1D"}


def load_bars(interval: str = "1d", start: Optional[str] = None,
              end: Optional[str] = None, symbol: str = "BTCUSDT",
              data_dir: str = DATA_DIR) -> pd.DataFrame:
    """Load bars for an interval from disk, optionally date-filtered."""
    path = os.path.join(data_dir, f"{symbol}_{interval}.csv")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} not found. Run: python fetch_data.py --interval {interval}")
    df = pd.read_csv(path, parse_dates=["timestamp"])
    if start:
        df = df[df["timestamp"] >= pd.Timestamp(start, tz="UTC")]
    if end:
        df = df[df["timestamp"] < pd.Timestamp(end, tz="UTC")]
    return df.reset_index(drop=True)


def resample(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    """Aggregate bars up to a coarser interval."""
    rule = RESAMPLE_RULE.get(interval, interval)
    cols = {k: v for k, v in OHLC.items() if k in df.columns}
    return (df.set_index("timestamp").resample(rule).agg(cols)
            .dropna().reset_index())


def as_utc(value) -> Optional[pd.Timestamp]:
    """Coerce a date-like to a UTC Timestamp, accepting already-aware input.

    Boundaries flow between CLIs (naive strings) and split_blocks (tz-aware
    Timestamps); `pd.Timestamp(x, tz=...)` raises on the latter, so every
    boundary goes through here.
    """
    if value is None:
        return None
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def slice_bars(df: pd.DataFrame, start=None, end=None) -> pd.DataFrame:
    out = df
    lo, hi = as_utc(start), as_utc(end)
    if lo is not None:
        out = out[out["timestamp"] >= lo]
    if hi is not None:
        out = out[out["timestamp"] < hi]
    return out.reset_index(drop=True)


def buy_hold_curve(df: pd.DataFrame, initial: float = 10_000.0) -> pd.Series:
    c = df["close"].to_numpy(dtype=float)
    return pd.Series(c / c[0] * initial, index=pd.DatetimeIndex(df["timestamp"]),
                     name="buy_hold")


# ---------------------------------------------------------------------------
# download
# ---------------------------------------------------------------------------
_COLUMNS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
            "quote_volume", "trades", "tbb", "tbq", "ignore"]


def fetch_klines(symbol: str, interval: str, start_ms: int, end_ms: int,
                 verbose: bool = True) -> pd.DataFrame:
    """Page through Binance klines. Public endpoint, no credentials."""
    step = INTERVAL_MS[interval]
    rows: List[list] = []
    cursor = start_ms
    session = requests.Session()
    n = 0

    while cursor < end_ms:
        params = {"symbol": symbol, "interval": interval, "startTime": cursor,
                  "endTime": end_ms, "limit": MAX_LIMIT}
        try:
            resp = session.get(KLINES_URL, params=params, timeout=20)
        except requests.RequestException as exc:
            logging.warning("request failed (%s), retry in 5s", exc)
            time.sleep(5)
            continue

        if resp.status_code in (429, 418):
            wait = int(resp.headers.get("Retry-After", 30))
            logging.warning("rate limited, sleeping %ss", wait)
            time.sleep(wait)
            continue
        if resp.status_code != 200:
            logging.error("HTTP %s: %s", resp.status_code, resp.text[:200])
            break

        batch = resp.json()
        if not batch:
            break
        rows.extend(batch)
        cursor = batch[-1][0] + step
        n += 1
        if verbose and n % 50 == 0:
            last = datetime.fromtimestamp(batch[-1][0] / 1000, tz=timezone.utc)
            logging.info("  %d requests, %d bars, through %s", n, len(rows), last.date())
        time.sleep(0.05)

    if not rows:
        raise RuntimeError("no data returned")
    return _to_frame(rows)


def _to_frame(rows: List[list]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=_COLUMNS)
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df[["timestamp", "open", "high", "low", "close", "volume"]]
    return (df.drop_duplicates("timestamp").sort_values("timestamp")
            .reset_index(drop=True))


def fetch_recent(interval: str = "1d", bars: int = 420, symbol: str = "BTCUSDT",
                 drop_incomplete: bool = True) -> pd.DataFrame:
    """Most recent `bars` bars, used by the live trader to bootstrap.

    The in-progress bar is dropped: acting on a partial bar means acting on
    information the backtest never had.
    """
    rows: List[list] = []
    cursor_end = int(time.time() * 1000)
    remaining = bars + 5
    while remaining > 0:
        limit = min(MAX_LIMIT, remaining)
        resp = requests.get(KLINES_URL, params={
            "symbol": symbol, "interval": interval,
            "endTime": cursor_end, "limit": limit}, timeout=20)
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        rows = batch + rows
        cursor_end = batch[0][0] - 1
        remaining -= len(batch)
        if len(batch) < limit:
            break

    df = _to_frame(rows)
    if drop_incomplete and len(df):
        span = pd.Timedelta(milliseconds=INTERVAL_MS[interval])
        now = pd.Timestamp.now(tz="UTC")
        df = df[df["timestamp"] + span <= now].reset_index(drop=True)
    return df


def save_bars(df: pd.DataFrame, interval: str, symbol: str = "BTCUSDT",
              data_dir: str = DATA_DIR) -> str:
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, f"{symbol}_{interval}.csv")
    df.to_csv(path, index=False)
    return path
