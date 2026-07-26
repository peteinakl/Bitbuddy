"""Sanity checks for the backtest engine.

These are not strategy tests. They verify the engine's accounting so that any
strategy result it produces can be trusted: no lookahead, costs actually
charged, P&L internally consistent, no phantom cash.

Run: python test_backtest.py
"""

import numpy as np
import pandas as pd

from backtest import Backtester, Costs, Entry


def make_bars(closes, start="2024-01-01", freq="5min"):
    idx = pd.date_range(start, periods=len(closes), freq=freq, tz="UTC")
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {
            "timestamp": idx,
            "open": c,
            "high": c * 1.0005,
            "low": c * 0.9995,
            "close": c,
            "volume": np.ones(len(c)),
        }
    )


class BuyOnceStrategy:
    """Enters once at bar `at`, with an explicit stop and target."""

    warmup = 1

    def __init__(self, at, stop, target=None, size_frac=1.0, **kw):
        self.at, self.stop, self.target, self.size_frac = at, stop, target, size_frac
        self.kw = kw
        self.fired = False

    def prepare(self, df):
        return df

    def entry_signal(self, df, i, equity):
        if i == self.at and not self.fired:
            self.fired = True
            return Entry(
                stop_price=self.stop,
                target_price=self.target,
                size_frac=self.size_frac,
                **self.kw,
            )
        return None


def check(label, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}" + (f"  {detail}" if detail else ""))
    return cond


def test_no_lookahead():
    """A signal at bar i must fill at bar i+1's open, not bar i's close."""
    print("no-lookahead fill timing")
    bars = make_bars([100, 100, 100, 200, 200, 200])
    bars.loc[3, "open"] = 200.0  # gap up on the bar after the signal
    bt = Backtester(bars, BuyOnceStrategy(at=2, stop=50), Costs(0, 0, 0), initial_equity=1000)
    r = bt.run()
    pos_entry = r.trades[0].entry_price if r.trades else None
    ok = check(
        "fills at next bar's open (200), not signal bar's close (100)",
        pos_entry == 200.0,
        f"entry_price={pos_entry}",
    )
    return ok


def test_costs_charged():
    """Round-tripping at an unchanged price must lose exactly the costs."""
    print("cost model")
    bars = make_bars([100] * 10)
    costs = Costs(fee_bps=10, slippage_bps=0, stop_slippage_bps=0)
    # Enter at bar 2, force a stop exit by putting the stop above the low
    bars.loc[5:, "low"] = 90.0
    bt = Backtester(bars, BuyOnceStrategy(at=1, stop=95), costs, initial_equity=1000)
    r = bt.run()
    t = r.trades[0]
    # Buy 1000 notional @100, sell @95: gross -5% ; fees 0.1% each side
    expected_gross = t.qty * (95 - 100)
    ok = check("gross P&L matches stop distance", abs(t.gross_pnl - expected_gross) < 1e-6,
               f"{t.gross_pnl:.4f} vs {expected_gross:.4f}")
    ok &= check("fees are non-zero", t.fees > 0, f"fees={t.fees:.4f}")
    ok &= check("net = gross - fees", abs(t.net_pnl - (t.gross_pnl - t.fees)) < 1e-9)

    # Zero-cost round trip that exits at the entry price must be exactly flat.
    # Target sits at 100 and the entry bar's high is 100.05, so it fills at 100.
    bars2 = make_bars([100] * 10)
    bt2 = Backtester(bars2, BuyOnceStrategy(at=1, stop=95, target=100.0),
                     Costs(0, 0, 0), initial_equity=1000)
    r2 = bt2.run()
    ok &= check("zero-cost exit at entry price is break-even",
                r2.trades and abs(r2.trades[0].net_pnl) < 1e-9,
                f"pnl={r2.trades[0].net_pnl:.10f}" if r2.trades else "no trades")

    # A zero-distance stop is nonsensical for risk sizing and must be refused
    bt3 = Backtester(make_bars([100] * 10), BuyOnceStrategy(at=1, stop=100.0),
                     Costs(0, 0, 0), initial_equity=1000)
    r3 = bt3.run()
    ok &= check("degenerate stop at entry price is refused", len(r3.trades) == 0,
                f"n={len(r3.trades)}")
    return ok


def test_equity_conservation():
    """Final equity must equal start + sum of net P&L, with nothing invented."""
    print("equity conservation")
    rng = np.random.default_rng(7)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.002, 3000)))
    bars = make_bars(closes)

    class Chop:
        warmup = 30

        def prepare(self, df):
            df = df.copy()
            df["ma"] = df["close"].rolling(20).mean()
            return df

        def entry_signal(self, df, i, equity):
            row = df.iloc[i]
            if np.isfinite(row["ma"]) and row["close"] > row["ma"]:
                p = float(row["close"])
                return Entry(stop_price=p * 0.98, target_price=p * 1.02, size_frac=0.5)
            return None

    bt = Backtester(bars, Chop(), Costs(), initial_equity=10_000)
    r = bt.run()
    total_pnl = sum(t.net_pnl for t in r.trades)
    ok = check("trades were generated", len(r.trades) > 20, f"n={len(r.trades)}")
    ok &= check(
        "final equity == initial + sum(net P&L)",
        abs(r.final_equity - (10_000 + total_pnl)) < 1e-6,
        f"{r.final_equity:.4f} vs {10_000 + total_pnl:.4f}",
    )
    ok &= check("equity never negative", (r.equity >= 0).all())
    ok &= check("no NaN in equity curve", r.equity.notna().all())
    return ok


def test_no_leverage():
    """The engine must never spend more cash than it has."""
    print("no leverage")
    rng = np.random.default_rng(3)
    closes = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.003, 2000)))
    bars = make_bars(closes)

    class Greedy:
        warmup = 5

        def prepare(self, df):
            return df

        def entry_signal(self, df, i, equity):
            p = float(df.iloc[i]["close"])
            # Ask for a huge position with a very tight stop: risk sizing would
            # demand far more notional than the account holds
            return Entry(stop_price=p * 0.9999, risk_frac=0.5)

    bt = Backtester(bars, Greedy(), Costs(), initial_equity=10_000, max_positions=3,
                    allow_pyramiding=True)
    r = bt.run()
    ok = check("cash never goes negative", bt.cash >= -1e-9, f"cash={bt.cash:.4f}")
    ok &= check("equity stays finite", np.isfinite(r.final_equity), f"{r.final_equity}")
    return ok


def test_pessimistic_intrabar():
    """When a bar spans both stop and target, the stop must win."""
    print("pessimistic intrabar ordering")
    bars = make_bars([100] * 6)
    # Bar 3 is a huge range containing both levels
    bars.loc[3, "high"] = 110.0
    bars.loc[3, "low"] = 90.0
    bt = Backtester(bars, BuyOnceStrategy(at=1, stop=95, target=105),
                    Costs(0, 0, 0), initial_equity=1000)
    r = bt.run()
    reason = r.trades[0].exit_reason
    return check("ambiguous bar resolves to STOP", reason == "STOP", f"reason={reason}")


def test_partial_then_trail():
    """Scale-out books a partial, and the remainder trails."""
    print("partial exit + trailing")
    closes = [100, 100, 100, 102, 104, 106, 103, 100]
    bars = make_bars(closes)
    for i, c in enumerate(closes):
        bars.loc[i, "high"] = c
        bars.loc[i, "low"] = c * 0.999
    bt = Backtester(
        bars,
        BuyOnceStrategy(at=1, stop=99, target=102, size_frac=1.0,
                        partial_frac=0.3, trail_dist=2.0),
        Costs(0, 0, 0),
        initial_equity=1000,
    )
    r = bt.run()
    reasons = [t.exit_reason for t in r.trades]
    ok = check("a partial was booked", "PARTIAL_TARGET" in reasons, f"{reasons}")
    ok &= check("remainder exited on the trail", "TRAIL_STOP" in reasons, f"{reasons}")
    if len(r.trades) >= 2:
        qty_total = sum(t.qty for t in r.trades)
        ok &= check("partial + remainder == original qty",
                    abs(qty_total - r.trades[0].qty / 0.3) < 1e-6,
                    f"total={qty_total:.6f}")
    return ok


def test_metrics_sane():
    """Metrics on a known monotonic curve."""
    print("metric sanity")
    bars = make_bars(np.linspace(100, 200, 1000))
    bt = Backtester(bars, BuyOnceStrategy(at=1, stop=50, size_frac=1.0),
                    Costs(0, 0, 0), initial_equity=1000)
    r = bt.run()
    ok = check("a rising market yields a gain", r.total_return > 0.9, f"{r.total_return:.3f}")
    ok &= check("max drawdown ~0 on a monotonic rise", r.max_drawdown > -0.01,
                f"{r.max_drawdown:.4f}")
    ok &= check("win rate is 100%", r.win_rate == 1.0)
    return ok


if __name__ == "__main__":
    results = []
    for fn in (
        test_no_lookahead,
        test_costs_charged,
        test_equity_conservation,
        test_no_leverage,
        test_pessimistic_intrabar,
        test_partial_then_trail,
        test_metrics_sane,
    ):
        results.append(bool(fn()))
        print()
    passed, total = sum(results), len(results)
    print(f"{passed}/{total} groups passed")
    raise SystemExit(0 if passed == total else 1)
