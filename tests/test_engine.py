"""Backtest engine honesty checks.

Not strategy tests. These verify the engine's accounting so any result it
produces can be trusted: no lookahead, costs charged, conservation of money,
no leverage, pessimistic intrabar ordering.

Run: python tests/test_engine.py
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bitbuddy.costs import Costs
from bitbuddy.engine import Backtester, Entry
from bitbuddy.strategies.base import Strategy


def bars(closes, start="2024-01-01", freq="1D", high_mult=1.0005, low_mult=0.9995):
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({
        "timestamp": pd.date_range(start, periods=len(c), freq=freq, tz="UTC"),
        "open": c, "high": c * high_mult, "low": c * low_mult,
        "close": c, "volume": np.ones(len(c)),
    })


class BuyOnce(Strategy):
    """Enter once at bar `at` with an explicit stop and optional target."""

    warmup = 1

    def __init__(self, at, stop, target=None, size_frac=1.0, **kw):
        self.at, self.stop, self.target, self.size_frac = at, stop, target, size_frac
        self.kw, self.fired = kw, False

    def entry_signal(self, ctx, i, equity):
        if i == self.at and not self.fired:
            self.fired = True
            return Entry(stop_price=self.stop, target_price=self.target,
                         size_frac=self.size_frac, **self.kw)
        return None


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
    return bool(cond)


def test_no_lookahead():
    print("no-lookahead fill timing")
    b = bars([100, 100, 100, 200, 200, 200])
    b.loc[3, "open"] = 200.0   # gap up on the bar after the signal
    r = Backtester(b, BuyOnce(at=2, stop=50), Costs(0, 0, 0), 1000).run()
    got = r.trades[0].entry_price if r.trades else None
    return check("fills at next open (200), not signal-bar close (100)",
                 got == 200.0, f"entry={got}")


def test_costs():
    print("cost model")
    b = bars([100] * 10)
    b.loc[5:, "low"] = 90.0
    r = Backtester(b, BuyOnce(at=1, stop=95), Costs(fee_bps=10, slippage_bps=0,
                                                   stop_slippage_bps=0), 1000).run()
    t = r.trades[0]
    ok = check("gross matches stop distance",
               abs(t.gross_pnl - t.qty * (95 - 100)) < 1e-6, f"{t.gross_pnl:.4f}")
    ok &= check("fees non-zero", t.fees > 0, f"{t.fees:.4f}")
    ok &= check("net == gross - fees", abs(t.net_pnl - (t.gross_pnl - t.fees)) < 1e-9)

    r2 = Backtester(bars([100] * 10), BuyOnce(at=1, stop=95, target=100.0),
                    Costs(0, 0, 0), 1000).run()
    ok &= check("zero-cost exit at entry is break-even",
                r2.trades and abs(r2.trades[0].net_pnl) < 1e-9)

    r3 = Backtester(bars([100] * 10), BuyOnce(at=1, stop=100.0),
                    Costs(0, 0, 0), 1000).run()
    ok &= check("degenerate stop at entry refused", len(r3.trades) == 0)

    c = Costs()
    ok &= check("round_trip_pct is 0.22%", abs(c.round_trip_pct - 0.22) < 1e-9,
                f"{c.round_trip_pct}")
    ok &= check("scaled(2) doubles fees", c.scaled(2).fee_bps == 2 * c.fee_bps)
    return ok


def test_conservation():
    print("conservation of money")
    rng = np.random.default_rng(7)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 2000)))

    class Chop(Strategy):
        warmup = 30

        def prepare(self, b):
            return {"ma": b["close"].rolling(20).mean().to_numpy(dtype=float)}

        def entry_signal(self, ctx, i, equity):
            ma = ctx.feats["ma"][i]
            if np.isfinite(ma) and ctx.close[i] > ma:
                p = float(ctx.close[i])
                return Entry(stop_price=p * 0.95, target_price=p * 1.05, size_frac=0.5)
            return None

    r = Backtester(bars(closes), Chop(), Costs(), 10_000).run()
    total = sum(t.net_pnl for t in r.trades)
    ok = check("trades generated", len(r.trades) > 20, f"n={len(r.trades)}")
    ok &= check("final == initial + sum(net P&L)",
                abs(r.final_equity - (10_000 + total)) < 1e-6,
                f"{r.final_equity:.4f} vs {10_000 + total:.4f}")
    ok &= check("equity never negative", (r.equity >= 0).all())
    ok &= check("no NaN in equity", r.equity.notna().all())
    ok &= check("exposure in [0,1]", 0.0 <= r.exposure <= 1.0, f"{r.exposure:.2f}")
    return ok


def test_no_leverage():
    print("no leverage")

    class Greedy(Strategy):
        warmup = 5

        def entry_signal(self, ctx, i, equity):
            p = float(ctx.close[i])
            # Tight stop + big risk_frac demands far more notional than we hold
            return Entry(stop_price=p * 0.9999, risk_frac=0.5)

    rng = np.random.default_rng(3)
    closes = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.01, 1500)))
    bt = Backtester(bars(closes), Greedy(), Costs(), 10_000)
    r = bt.run()
    ok = check("cash never negative", bt.cash >= -1e-9, f"cash={bt.cash:.4f}")
    ok &= check("equity finite", np.isfinite(r.final_equity))
    return ok


def test_pessimistic_intrabar():
    print("pessimistic intrabar ordering")
    b = bars([100] * 6)
    b.loc[3, "high"] = 110.0
    b.loc[3, "low"] = 90.0
    r = Backtester(b, BuyOnce(at=1, stop=95, target=105), Costs(0, 0, 0), 1000).run()
    got = r.trades[0].exit_reason
    return check("ambiguous bar resolves to STOP", got == "STOP", f"reason={got}")


def test_trail_labelling():
    print("stop labelling")
    closes = [100, 100, 100, 110, 120, 115, 100]
    b = bars(closes)
    for i, c in enumerate(closes):
        b.loc[i, "high"] = c
        b.loc[i, "low"] = c * 0.999
    r = Backtester(b, BuyOnce(at=1, stop=95, trail_dist=5.0), Costs(0, 0, 0), 1000).run()
    reasons = [t.exit_reason for t in r.trades]
    ok = check("advanced stop labelled TRAIL_STOP", "TRAIL_STOP" in reasons, f"{reasons}")

    # With trail_trigger=None the trail is live from entry, so any high above
    # entry legitimately advances the stop. To exercise the un-advanced case the
    # high must never exceed entry: high_mult=1.0 pins it to the close.
    b2 = bars([100] * 6, high_mult=1.0)
    b2.loc[3:, "low"] = 90.0
    r2 = Backtester(b2, BuyOnce(at=1, stop=95, trail_dist=5.0), Costs(0, 0, 0), 1000).run()
    ok &= check("un-advanced stop labelled STOP",
                r2.trades[0].exit_reason == "STOP", r2.trades[0].exit_reason)

    # And a plain stop with no trail at all is always STOP
    b3 = bars([100] * 6)
    b3.loc[3:, "low"] = 90.0
    r3 = Backtester(b3, BuyOnce(at=1, stop=95), Costs(0, 0, 0), 1000).run()
    ok &= check("stop without a trail is STOP",
                r3.trades[0].exit_reason == "STOP", r3.trades[0].exit_reason)
    return ok


def test_trade_window():
    print("trade_start / trade_end")
    closes = np.linspace(100, 200, 400)
    b = bars(closes)
    start = b["timestamp"].iloc[200]
    r = Backtester(b, BuyOnce(at=210, stop=50), Costs(0, 0, 0), 1000,
                   trade_start=start).run()
    ok = check("equity curve starts at trade_start", r.equity.index[0] >= start,
               str(r.equity.index[0]))
    ok &= check("benchmark rebased to window start",
                abs(r.benchmark.iloc[0] - 1000) < 1e-6, f"{r.benchmark.iloc[0]:.2f}")

    # An entry before trade_start must not fire
    r2 = Backtester(b, BuyOnce(at=10, stop=50), Costs(0, 0, 0), 1000,
                    trade_start=start).run()
    ok &= check("no trades before trade_start", len(r2.trades) == 0, f"n={len(r2.trades)}")
    return ok


def test_metrics():
    print("metric sanity")
    r = Backtester(bars(np.linspace(100, 200, 500)), BuyOnce(at=1, stop=50),
                   Costs(0, 0, 0), 1000).run()
    ok = check("rising market gains", r.total_return > 0.9, f"{r.total_return:.3f}")
    ok &= check("maxDD ~0 on monotonic rise", r.max_drawdown > -0.01, f"{r.max_drawdown:.4f}")
    ok &= check("win rate 100%", r.win_rate == 1.0)
    ok &= check("ulcer ~0", r.ulcer_index < 0.01, f"{r.ulcer_index:.4f}")
    ok &= check("summary renders", "Total return" in r.summary("x"))
    return ok


if __name__ == "__main__":
    fns = [test_no_lookahead, test_costs, test_conservation, test_no_leverage,
           test_pessimistic_intrabar, test_trail_labelling, test_trade_window,
           test_metrics]
    res = []
    for fn in fns:
        res.append(bool(fn()))
        print()
    print(f"{sum(res)}/{len(res)} groups passed")
    raise SystemExit(0 if all(res) else 1)
