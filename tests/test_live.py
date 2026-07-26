"""Live paper-trader checks.

Verifies what was structurally broken in the original bot: realized P&L is real,
the books balance, state survives a restart, no startup deadlock, and the live
decision path agrees with the backtester.

Run: python tests/test_live.py
"""

import json
import logging
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bitbuddy.costs import Costs
from bitbuddy.data import load_bars
from bitbuddy.live import Broker, LiveTrader, Lot, Portfolio, replay

logging.disable(logging.INFO)


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
    return bool(cond)


def test_realized_pnl():
    print("realized P&L is real")
    p = Portfolio(10_000.0, 10_000.0, peak_equity=10_000.0)
    b = Broker(p, Costs())
    b.buy(50_000.0, 5_000.0, 45_000.0, "t0")
    ok = check("cash reduced by notional + fee", p.cash < 5_000.0, f"{p.cash:.2f}")
    ok &= check("one open lot", len(p.lots) == 1)
    ok &= check("not flat", not p.is_flat)

    r = b.sell_all(55_000.0, "t1", "TEST")[0]
    ok &= check("P&L non-zero", abs(r.net_pnl) > 1.0, f"{r.net_pnl:.2f}")
    ok &= check("P&L positive on a 10% rise", r.net_pnl > 0, f"{r.net_pnl:+.2f}")
    ok &= check("net == gross - fees", abs(r.net_pnl - (r.gross_pnl - r.fees)) < 1e-9)
    ok &= check("return ~+9.6% after costs", 0.085 < r.return_pct < 0.10,
                f"{r.return_pct * 100:.2f}%")
    ok &= check("flat after selling", p.is_flat and p.btc == 0)

    p2 = Portfolio(10_000.0, 10_000.0, peak_equity=10_000.0)
    b2 = Broker(p2, Costs())
    b2.buy(50_000.0, 5_000.0, 45_000.0, "t0")
    r2 = b2.sell_all(45_000.0, "t1", "STOP", is_stop=True)[0]
    ok &= check("loss is real and negative", r2.net_pnl < -1.0, f"{r2.net_pnl:+.2f}")
    return ok


def test_books_balance():
    print("accounting identity over full history")
    bars = load_bars("1d")
    t = LiveTrader(Costs(), 10_000.0, state_file="/tmp/_t.json", persist=False)
    warm = t.strategy.warmup

    broken = None
    for end in range(warm + 1, len(bars) + 1):
        t.step(bars.iloc[:end].reset_index(drop=True), force=True)
        price = float(bars["close"].iloc[end - 1])
        if not t.portfolio.check_invariant(price):
            broken = end
            break

    p = t.portfolio
    last = float(bars["close"].iloc[-1])
    ok = check("identity holds on every bar", broken is None, f"broke at bar {broken}")
    ok &= check("cash never negative", p.cash >= -1e-6, f"{p.cash:.2f}")
    ok &= check("btc never negative", p.btc >= 0)
    ok &= check("trades generated", len(p.trades) > 20, f"n={len(p.trades)}")
    ok &= check("no zero-P&L trades", all(abs(r.net_pnl) > 1e-6 for r in p.trades),
                f"{sum(1 for r in p.trades if abs(r.net_pnl) <= 1e-6)} zero")
    ok &= check("realized == sum of trade net P&L",
                abs(p.realized_pnl - sum(r.net_pnl for r in p.trades)) < 1e-6)
    ok &= check("final equity positive", p.equity(last) > 0, f"${p.equity(last):,.0f}")
    return ok


def test_state_roundtrip():
    print("state persistence")
    cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as d:
        os.chdir(d)
        try:
            t = LiveTrader(Costs(), 10_000.0, state_file="s.json")
            t.portfolio.cash = 4_321.0
            t.portfolio.realized_pnl = 555.5
            t.portfolio.fees_paid = 12.25
            t.portfolio.lots = [Lot(0.05, 60_000.0, "t0", 3.0, 54_000.0, "x")]
            t.last_bar = "2026-01-01 00:00:00+00:00"
            t.save()

            t2 = LiveTrader(Costs(), 10_000.0, state_file="s.json")
            ok = check("cash restored", abs(t2.portfolio.cash - 4_321.0) < 1e-9)
            ok &= check("realized restored", abs(t2.portfolio.realized_pnl - 555.5) < 1e-9)
            ok &= check("fees restored", abs(t2.portfolio.fees_paid - 12.25) < 1e-9)
            ok &= check("lot restored", len(t2.portfolio.lots) == 1)
            ok &= check("cost basis restored",
                        abs(t2.portfolio.lots[0].entry_price - 60_000.0) < 1e-9)
            ok &= check("last_bar restored", t2.last_bar == "2026-01-01 00:00:00+00:00")
            ok &= check("state file valid JSON", isinstance(json.load(open("s.json")), dict))
            ok &= check("strategy recorded in state",
                        "strategy" in json.load(open("s.json")))
            return ok
        finally:
            os.chdir(cwd)


def test_no_deadlock():
    print("no startup deadlock")
    p = Portfolio(10_000.0, 10_000.0, peak_equity=10_000.0)
    ok = check("starts with deployable cash", p.cash > 0, f"{p.cash:.2f}")
    ok &= check("starts flat", p.is_flat)
    lot = Broker(p, Costs()).buy(60_000.0, 9_500.0, 54_000.0, "t0")
    ok &= check("can open immediately", lot is not None)
    ok &= check("capped by cash", p.cash >= -1e-9, f"{p.cash:.2f}")
    return ok


def test_idempotent():
    print("idempotent bar processing")
    bars = load_bars("1d")
    t = LiveTrader(Costs(), 10_000.0, state_file="/tmp/_t2.json", persist=False)
    sub = bars.iloc[:t.strategy.warmup + 50].reset_index(drop=True)
    t.step(sub)
    n_after_first = len(t.decisions)
    second = t.step(sub)
    ok = check("re-processing same bar is a no-op", second == "ALREADY_PROCESSED", second)
    ok &= check("no extra decision logged", len(t.decisions) == n_after_first)
    ok &= check("--force overrides", t.step(sub, force=True) != "ALREADY_PROCESSED")
    return ok


def test_matches_backtest():
    print("live path agrees with backtester")
    return check("replay within 2%", replay())


if __name__ == "__main__":
    try:
        load_bars("1d")
    except FileNotFoundError as exc:
        raise SystemExit(f"{exc}")
    fns = [test_realized_pnl, test_books_balance, test_state_roundtrip,
           test_no_deadlock, test_idempotent, test_matches_backtest]
    res = []
    for fn in fns:
        res.append(bool(fn()))
        print()
    print(f"{sum(res)}/{len(res)} groups passed")
    raise SystemExit(0 if all(res) else 1)
