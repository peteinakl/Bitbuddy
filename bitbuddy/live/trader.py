"""Paper trading driven by the validated strategy.

Uses the same strategy objects and the same `make_context` as the backtester, so
the live decision path cannot drift from what was validated. `replay()` asserts
that agreement over the full history and should be run after any change to
strategy or execution code.

Signals come from the last *completed* bar and are executed at the next open,
exactly as in the backtest.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd
import requests

from bitbuddy.costs import Costs
from bitbuddy.data import fetch_recent
from bitbuddy.engine import make_context
from bitbuddy.live.portfolio import Broker, Lot, Portfolio, TradeRecord
from bitbuddy.strategies import REGISTRY

STATE_FILE = "live_state.json"
TRADES_FILE = "live_trades.json"
DECISIONS_FILE = "live_decisions.json"

# ---------------------------------------------------------------------------
# Shipped configuration.
#
# Selected on development data (2017-08 → 2024-07) only, then confirmed on a
# held-out block. Rationale for this family and these values:
#
#   * out-of-sample walk-forward 2020-08 → 2026-07: +1014% vs buy & hold +424%,
#     drawdown -39% vs -77%
#   * fixed parameters beat per-window refitting (+1014% vs +614%), so the edge
#     is structural rather than fitted -- ship the simple fixed config
#   * 80/80 neighbouring configurations are profitable on development data
#     (Sharpe median 1.07, min 0.73): a plateau, not a spike
#   * survives 4x assumed costs (+509% full period)
#
# The higher-return, higher-risk alternative is MaTrend(entry_ma=20, exit_ma=30,
# confirm=False, target_vol=0.4): +1759% full period but -41% drawdown versus
# -29% here. Swap STRATEGY/STRATEGY_PARAMS to switch.
# ---------------------------------------------------------------------------
STRATEGY = "dualmom"
STRATEGY_PARAMS: Dict = dict(
    lookback=30, slow_ma=300, exit_ma=20, target_vol=0.4,
    stop_atr=8.0, atr_period=14, vol_window=30, interval="1d",
)
INTERVAL = "1d"
BOOTSTRAP_BARS = 600  # slow_ma=300 plus margin

PRICE_APIS = {
    "Binance": ("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT",
                lambda j: float(j["price"])),
    "Coinbase": ("https://api.coinbase.com/v2/prices/BTC-USD/spot",
                 lambda j: float(j["data"]["amount"])),
    "Kraken": ("https://api.kraken.com/0/public/Ticker?pair=XBTUSD",
               lambda j: float(next(iter(j["result"].values()))["c"][0])),
    "Bitstamp": ("https://www.bitstamp.net/api/v2/ticker/btcusd/",
                 lambda j: float(j["last"])),
}


def fetch_spot_price() -> Optional[float]:
    """Current price, trying exchanges in turn. Display and stop checks only."""
    for name, (url, parse) in PRICE_APIS.items():
        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            price = parse(r.json())
            if price and price > 0:
                return price
        except Exception as exc:
            logging.debug("%s failed: %s", name, exc)
    logging.error("all price APIs failed")
    return None


def _num(v) -> Optional[float]:
    try:
        f = float(v)
        return None if pd.isna(f) else round(f, 6)
    except (TypeError, ValueError):
        return None


class LiveTrader:
    def __init__(self, costs: Optional[Costs] = None, initial_capital: float = 10_000.0,
                 state_file: str = STATE_FILE, persist: bool = True):
        self.costs = costs or Costs()
        self.state_file = state_file
        self.persist = persist
        self.strategy = REGISTRY[STRATEGY](**STRATEGY_PARAMS)
        self.portfolio = Portfolio(initial_capital=initial_capital,
                                   cash=initial_capital, peak_equity=initial_capital)
        self.decisions: List[dict] = []
        self.last_bar: Optional[str] = None
        if persist:
            self.load()

    # -- persistence ----------------------------------------------------
    def save(self) -> None:
        if not self.persist:
            return
        state = {
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "last_bar": self.last_bar,
            "strategy": {"name": STRATEGY, **STRATEGY_PARAMS},
            "portfolio": {
                "initial_capital": self.portfolio.initial_capital,
                "cash": self.portfolio.cash,
                "realized_pnl": self.portfolio.realized_pnl,
                "fees_paid": self.portfolio.fees_paid,
                "peak_equity": self.portfolio.peak_equity,
                "lots": [asdict(l) for l in self.portfolio.lots],
            },
        }
        tmp = self.state_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, self.state_file)  # atomic: a crash cannot corrupt state

        with open(TRADES_FILE, "w") as f:
            json.dump([asdict(t) for t in self.portfolio.trades], f, indent=2)
        with open(DECISIONS_FILE, "w") as f:
            json.dump(self.decisions[-2000:], f, indent=2)

    def load(self) -> None:
        try:
            with open(self.state_file) as f:
                state = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            logging.info(f"no saved state; starting fresh at "
                         f"${self.portfolio.initial_capital:,.2f}")
            return

        saved = state.get("strategy", {}).get("name")
        if saved and saved != STRATEGY:
            logging.warning(f"saved state was produced by '{saved}' but the shipped "
                            f"strategy is '{STRATEGY}'; continuing with existing "
                            f"positions under the new rules")

        p = state.get("portfolio", {})
        self.portfolio.initial_capital = p.get("initial_capital", 10_000.0)
        self.portfolio.cash = p.get("cash", self.portfolio.initial_capital)
        self.portfolio.realized_pnl = p.get("realized_pnl", 0.0)
        self.portfolio.fees_paid = p.get("fees_paid", 0.0)
        self.portfolio.peak_equity = p.get("peak_equity", self.portfolio.initial_capital)
        self.portfolio.lots = [Lot(**l) for l in p.get("lots", [])]
        self.last_bar = state.get("last_bar")

        for path, attr, cls in ((TRADES_FILE, "trades", TradeRecord),
                                (DECISIONS_FILE, "decisions", None)):
            try:
                with open(path) as f:
                    data = json.load(f)
                if cls:
                    self.portfolio.trades = [cls(**d) for d in data]
                else:
                    self.decisions = data
            except (FileNotFoundError, json.JSONDecodeError, TypeError):
                pass

        logging.info(f"restored: cash ${self.portfolio.cash:,.2f}  "
                     f"btc {self.portfolio.btc:.8f}  {len(self.portfolio.lots)} lots  "
                     f"{len(self.portfolio.trades)} trades  "
                     f"realized ${self.portfolio.realized_pnl:+,.2f}")

    # -- decision -------------------------------------------------------
    def step(self, bars: pd.DataFrame, force: bool = False) -> str:
        """Evaluate the most recently completed bar and act."""
        ctx = make_context(bars, self.strategy, INTERVAL)
        i = len(ctx) - 1
        bar_time = str(ctx.timestamp[i])
        price = float(ctx.close[i])

        if not force and self.last_bar == bar_time:
            logging.info(f"bar {bar_time} already processed; nothing to do")
            return "ALREADY_PROCESSED"
        if i < self.strategy.warmup:
            logging.warning(f"only {len(ctx)} bars, need {self.strategy.warmup}")
            return "INSUFFICIENT_HISTORY"

        broker = Broker(self.portfolio, self.costs)
        action, reason = "HOLD", "NO_SIGNAL"

        if not self.portfolio.is_flat:
            worst_stop = max(l.stop_price for l in self.portfolio.lots)
            if float(ctx.low[i]) <= worst_stop:
                broker.sell_all(worst_stop, bar_time, "STOP", is_stop=True)
                action, reason = "SELL", "STOP"
            else:
                hook = getattr(self.strategy, "exit_signal", None)
                exit_reason = hook(ctx, i, self.portfolio.lots[0]) if hook else None
                if exit_reason:
                    broker.sell_all(price, bar_time, exit_reason)
                    action, reason = "SELL", exit_reason
                else:
                    reason = "HOLDING_POSITION"
        else:
            entry = self.strategy.entry_signal(ctx, i, self.portfolio.equity(price))
            if entry is not None:
                notional = self.portfolio.equity(price) * (entry.size_frac or 0.0)
                if broker.buy(price, notional, entry.stop_price, bar_time, entry.tag):
                    action, reason = "BUY", "TREND_ENTRY"
                else:
                    reason = "INSUFFICIENT_CASH"
            else:
                reason = "TREND_NOT_UP"

        eq = self.portfolio.equity(price)
        self.portfolio.peak_equity = max(self.portfolio.peak_equity, eq)
        self.last_bar = bar_time

        self.decisions.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "bar": bar_time, "action": action, "reason": reason, "price": price,
            "equity": eq, "cash": self.portfolio.cash, "btc": self.portfolio.btc,
            "features": {k: _num(v[i]) for k, v in ctx.feats.items()},
        })
        self.save()
        logging.info(f"bar {bar_time[:10]}  ${price:,.2f}  {action} ({reason})  "
                     f"equity ${eq:,.2f}  dd {self.portfolio.drawdown(price) * 100:.1f}%")
        return action

    # -- reporting ------------------------------------------------------
    def status(self, price: Optional[float] = None) -> str:
        if price is None:
            price = fetch_spot_price() or (
                self.portfolio.lots[0].entry_price if self.portfolio.lots else 0.0)
        p = self.portfolio
        eq = p.equity(price)
        total = eq - p.initial_capital
        lines = [
            "=" * 62,
            f"  Strategy         {self.strategy.name}",
            f"  BTC price        ${price:,.2f}",
            f"  Equity           ${eq:,.2f}   (start ${p.initial_capital:,.2f})",
            f"  Total P&L        ${total:+,.2f}  ({total / p.initial_capital * 100:+.2f}%)",
            f"    realized       ${p.realized_pnl:+,.2f}",
            f"    unrealized     ${p.unrealized(price):+,.2f}",
            f"  Cash             ${p.cash:,.2f}",
            f"  BTC held         {p.btc:.8f}" + ("  (flat)" if p.is_flat else
                                                 f"  in {len(p.lots)} lot(s)"),
            f"  Fees paid        ${p.fees_paid:,.2f}",
            f"  Drawdown         {p.drawdown(price) * 100:.1f}%   (peak ${p.peak_equity:,.2f})",
            f"  Trades           {len(p.trades)}   win rate {p.win_rate() * 100:.1f}%"
            f"   profit factor {p.profit_factor():.2f}",
        ]
        for lot in p.lots:
            pnl = (price - lot.entry_price) / lot.entry_price * 100
            lines.append(f"    lot {lot.qty:.8f} BTC @ ${lot.entry_price:,.2f} "
                         f"({pnl:+.2f}%)  stop ${lot.stop_price:,.2f}")
        lines.append("=" * 62)
        return "\n".join(lines)

    def fetch_bars(self) -> pd.DataFrame:
        return fetch_recent(INTERVAL, BOOTSTRAP_BARS)


# ---------------------------------------------------------------------------
def replay(bars: Optional[pd.DataFrame] = None, tolerance: float = 0.02) -> bool:
    """Feed history through LiveTrader bar by bar and compare to the Backtester."""
    from bitbuddy.data import load_bars
    from bitbuddy.engine import Backtester

    if bars is None:
        bars = load_bars(INTERVAL)

    costs = Costs()
    ref = Backtester(bars, REGISTRY[STRATEGY](**STRATEGY_PARAMS), costs,
                     initial_equity=10_000.0, interval=INTERVAL).run()

    trader = LiveTrader(costs=costs, initial_capital=10_000.0,
                        state_file="/tmp/_replay_state.json", persist=False)
    warm = trader.strategy.warmup
    for end in range(warm + 1, len(bars) + 1):
        trader.step(bars.iloc[:end].reset_index(drop=True), force=True)

    last = float(bars["close"].iloc[-1])
    live_eq = trader.portfolio.equity(last)
    diff = abs(live_eq - ref.final_equity) / ref.final_equity

    print(f"  backtester   ${ref.final_equity:,.2f}  ({len(ref.trades)} trades)")
    print(f"  live trader  ${live_eq:,.2f}  ({len(trader.portfolio.trades)} trades)")
    print(f"  difference   {diff * 100:.3f}%")
    ok = diff < tolerance
    print(f"  {'PASS' if ok else 'FAIL'}: live logic "
          f"{'matches' if ok else 'DIVERGES FROM'} backtest")
    return ok
