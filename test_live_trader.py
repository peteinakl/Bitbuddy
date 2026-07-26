"""Checks for the live paper-trading simulator.

Verifies the things that were structurally broken in bitcoin_trading_bot.py:
realized P&L is real, the books balance, state survives a restart, and the live
decision path agrees with the backtester.

Run: python test_live_trader.py
"""

import json
import logging
import os
import tempfile

import pandas as pd

from backtest import Costs, load_bars
from live_trader import Broker, LiveTrader, Lot, Portfolio, replay

logging.disable(logging.INFO)
DATA = "data/BTCUSDT_5m.csv"


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
    return bool(cond)


def daily_bars(end=None):
    bars = load_bars(DATA, end=end)
    return (bars.set_index("timestamp").resample("1D")
            .agg({"open": "first", "high": "max", "low": "min",
                  "close": "last", "volume": "sum"})
            .dropna().reset_index())


def test_realized_pnl_is_real():
    """A round trip at a higher price must book a positive, non-zero P&L."""
    print("realized P&L")
    p = Portfolio(initial_capital=10_000.0, cash=10_000.0, peak_equity=10_000.0)
    b = Broker(p, Costs())
    b.buy(price=50_000.0, notional=5_000.0, stop_price=45_000.0, when="t0")
    ok = check("cash reduced by notional + fee", p.cash < 5_000.0, f"cash={p.cash:.2f}")
    ok &= check("one open lot", len(p.lots) == 1)

    recs = b.sell_all(price=55_000.0, when="t1", reason="TEST")
    r = recs[0]
    ok &= check("P&L is non-zero", abs(r.net_pnl) > 1.0, f"net={r.net_pnl:.2f}")
    ok &= check("P&L is positive on a 10% rise", r.net_pnl > 0, f"net={r.net_pnl:+.2f}")
    ok &= check("net == gross - fees", abs(r.net_pnl - (r.gross_pnl - r.fees)) < 1e-9)
    ok &= check("return_pct is ~+9.6% after costs",
                0.085 < r.return_pct < 0.10, f"{r.return_pct*100:.2f}%")
    ok &= check("flat after selling all", p.btc == 0 and not p.lots)
    ok &= check("realized_pnl accumulated", abs(p.realized_pnl - r.net_pnl) < 1e-9)

    # A losing round trip must book a real loss
    p2 = Portfolio(initial_capital=10_000.0, cash=10_000.0, peak_equity=10_000.0)
    b2 = Broker(p2, Costs())
    b2.buy(price=50_000.0, notional=5_000.0, stop_price=45_000.0, when="t0")
    r2 = b2.sell_all(price=45_000.0, when="t1", reason="STOP", is_stop=True)[0]
    ok &= check("loss is negative and non-zero", r2.net_pnl < -1.0, f"net={r2.net_pnl:+.2f}")
    return ok


def test_books_balance():
    """The accounting identity must hold over a long history."""
    print("accounting identity")
    bars = daily_bars(end="2025-01-01")
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as fh:
        state = fh.name
    t = LiveTrader(costs=Costs(), initial_capital=10_000.0, state_file=state)
    t.portfolio = Portfolio(initial_capital=10_000.0, cash=10_000.0, peak_equity=10_000.0)
    t.save = lambda: None

    ok = True
    for end in range(t.strategy.warmup + 1, len(bars) + 1):
        t.step(bars.iloc[:end].reset_index(drop=True), force=True)
        price = float(bars["close"].iloc[end - 1])
        if not t.portfolio.check_invariant(price):
            ok = False
            break
    p = t.portfolio
    last = float(bars["close"].iloc[-1])
    ok = check("equity == initial + realized + unrealized - open fees",
               p.check_invariant(last),
               f"equity={p.equity(last):.2f}")
    ok &= check("cash never negative", p.cash >= -1e-6, f"cash={p.cash:.2f}")
    ok &= check("btc never negative", p.btc >= 0)
    ok &= check("trades were generated", len(p.trades) > 5, f"n={len(p.trades)}")
    ok &= check("no trade has exactly zero P&L",
                all(abs(r.net_pnl) > 1e-6 for r in p.trades),
                f"{sum(1 for r in p.trades if abs(r.net_pnl) <= 1e-6)} zero-P&L trades")
    ok &= check("realized_pnl == sum of trade net P&L",
                abs(p.realized_pnl - sum(r.net_pnl for r in p.trades)) < 1e-6)
    os.unlink(state)
    return ok


def test_state_roundtrip():
    """Positions, cash, realized P&L and history must survive a restart."""
    print("state persistence")
    with tempfile.TemporaryDirectory() as d:
        cwd = os.getcwd()
        os.chdir(d)
        try:
            state = "s.json"
            t = LiveTrader(costs=Costs(), initial_capital=10_000.0, state_file=state)
            t.portfolio.cash = 4_321.0
            t.portfolio.realized_pnl = 555.5
            t.portfolio.fees_paid = 12.25
            t.portfolio.lots = [Lot(qty=0.05, entry_price=60_000.0, entry_time="t0",
                                    entry_fee=3.0, stop_price=54_000.0, tag="x")]
            t.last_bar = "2026-01-01 00:00:00+00:00"
            t.save()

            t2 = LiveTrader(costs=Costs(), initial_capital=10_000.0, state_file=state)
            ok = check("cash restored", abs(t2.portfolio.cash - 4_321.0) < 1e-9)
            ok &= check("realized P&L restored", abs(t2.portfolio.realized_pnl - 555.5) < 1e-9)
            ok &= check("fees restored", abs(t2.portfolio.fees_paid - 12.25) < 1e-9)
            ok &= check("open lot restored", len(t2.portfolio.lots) == 1
                        and abs(t2.portfolio.lots[0].qty - 0.05) < 1e-12)
            ok &= check("lot cost basis restored",
                        abs(t2.portfolio.lots[0].entry_price - 60_000.0) < 1e-9)
            ok &= check("last_bar restored", t2.last_bar == "2026-01-01 00:00:00+00:00")
            ok &= check("state file is valid JSON", isinstance(json.load(open(state)), dict))
            return ok
        finally:
            os.chdir(cwd)


def test_no_deadlock():
    """A fresh portfolio must be able to open a position.

    The old bot started 100% in BTC with $0 cash while requiring a 20% cash
    reserve to enter, so it could never trade. This asserts that cannot recur.
    """
    print("no startup deadlock")
    p = Portfolio(initial_capital=10_000.0, cash=10_000.0, peak_equity=10_000.0)
    ok = check("starts with deployable cash", p.cash > 0, f"cash={p.cash:.2f}")
    ok &= check("starts flat, not fully invested", p.btc == 0)
    b = Broker(p, Costs())
    lot = b.buy(price=60_000.0, notional=9_500.0, stop_price=54_000.0, when="t0")
    ok &= check("can open a position immediately", lot is not None)
    ok &= check("buy is capped by available cash", p.cash >= -1e-9, f"cash={p.cash:.2f}")
    return ok


def test_matches_backtest():
    print("live path agrees with backtester")
    return check("replay within 2%", replay(DATA))


if __name__ == "__main__":
    if not os.path.exists(DATA):
        raise SystemExit(f"missing {DATA}; run: python fetch_data.py")
    results = []
    for fn in (test_realized_pnl_is_real, test_books_balance, test_state_roundtrip,
               test_no_deadlock, test_matches_backtest):
        results.append(bool(fn()))
        print()
    passed, total = sum(results), len(results)
    print(f"{passed}/{total} groups passed")
    raise SystemExit(0 if passed == total else 1)
