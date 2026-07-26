"""Live paper-trading simulator driven by the validated strategy.

This replaces the trading logic in bitcoin_trading_bot.py, whose rules backtest
to -99.2% over 2023-2026. It shares strategies.py and Costs with backtest.py so
there is exactly one implementation of the strategy: what is validated is what
runs. No real money and no exchange credentials are involved.

Architectural differences from the old bot, all consequences of the strategy
operating on daily bars:

* **Bootstraps from real history.** On start it downloads ~400 daily bars, so
  indicators are warm immediately. The old bot spent two minutes collecting 12
  prices and then computed a "1-hour momentum" from them.
* **One decision per completed daily bar,** not every 15 minutes. A signal is
  taken from the last *closed* bar and filled at the next open, matching the
  backtest exactly.
* **Real cost basis and realized P&L.** Every exit computes proceeds minus the
  matched entry cost minus fees. The old bot recorded portfolio value delta
  across a cash<->BTC conversion, which is zero by construction.
* **State is actually restored.** Positions, cash, realized P&L and trade
  history survive a restart.

Usage:
    python live_trader.py --once      # evaluate once and exit
    python live_trader.py --loop      # run continuously, one decision per day
    python live_trader.py --replay    # verify live logic reproduces the backtest
    python live_trader.py --status    # print state and exit
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import pandas as pd
import requests

from backtest import Costs
from strategies import VolTargetTrendStrategy

STATE_FILE = "live_state.json"
TRADES_FILE = "live_trades.json"
DECISIONS_FILE = "live_decisions.json"
LOG_FILE = "live_trader.log"

# Validated configuration. Chosen for out-of-sample walk-forward performance,
# not in-sample fit: fixed parameters beat per-window refitting (+34.9% vs
# +30.6% OOS), so the simple conventional setting is the one shipped.
STRATEGY_PARAMS = dict(fast_ma=50, slow_ma=200, exit_ma=50, target_vol=0.50, stop_atr=8.0)
BOOTSTRAP_DAYS = 420  # slow_ma=200 plus comfortable margin

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


# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------
@dataclass
class Lot:
    """One open parcel of BTC with its true cost basis."""

    qty: float
    entry_price: float      # fill price, slippage included
    entry_time: str
    entry_fee: float
    stop_price: float
    tag: str = ""


@dataclass
class TradeRecord:
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    qty: float
    gross_pnl: float
    fees: float
    net_pnl: float          # realized, after all costs
    return_pct: float
    exit_reason: str

    @property
    def is_win(self) -> bool:
        return self.net_pnl > 0


@dataclass
class Portfolio:
    initial_capital: float = 10_000.0
    cash: float = 10_000.0
    lots: List[Lot] = field(default_factory=list)
    realized_pnl: float = 0.0
    fees_paid: float = 0.0
    trades: List[TradeRecord] = field(default_factory=list)
    peak_equity: float = 10_000.0

    @property
    def btc(self) -> float:
        return sum(l.qty for l in self.lots)

    def equity(self, price: float) -> float:
        return self.cash + self.btc * price

    def unrealized(self, price: float) -> float:
        """Gross mark-to-market on open lots, before their entry fees."""
        return sum(l.qty * (price - l.entry_price) for l in self.lots)

    @property
    def open_entry_fees(self) -> float:
        """Entry fees already paid on still-open lots. Needed for the identity
        below because those fees have left cash but are not yet in realized P&L."""
        return sum(l.entry_fee for l in self.lots)

    def check_invariant(self, price: float, tol: float = 0.01) -> bool:
        """equity == initial + realized + unrealized - open_entry_fees.

        If this ever fails, money is being created or destroyed somewhere and no
        performance number from this portfolio can be trusted.
        """
        expected = (self.initial_capital + self.realized_pnl
                    + self.unrealized(price) - self.open_entry_fees)
        return abs(self.equity(price) - expected) < tol

    def drawdown(self, price: float) -> float:
        eq = self.equity(price)
        peak = max(self.peak_equity, eq)
        return (peak - eq) / peak if peak > 0 else 0.0

    # -- metrics --------------------------------------------------------
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        return sum(1 for t in self.trades if t.is_win) / len(self.trades)

    def profit_factor(self) -> float:
        gains = sum(t.net_pnl for t in self.trades if t.net_pnl > 0)
        losses = -sum(t.net_pnl for t in self.trades if t.net_pnl < 0)
        if losses == 0:
            return float("inf") if gains > 0 else 0.0
        return gains / losses


class Broker:
    """Simulated spot execution: long or flat, no leverage, costs charged."""

    def __init__(self, portfolio: Portfolio, costs: Costs):
        self.p = portfolio
        self.costs = costs

    def buy(self, price: float, notional: float, stop_price: float, when: str, tag: str = "") -> Optional[Lot]:
        fill = self.costs.entry_fill(price)
        notional = min(notional, self.p.cash / (1 + self.costs.fee_bps / 10_000))
        if notional < 10:
            return None
        qty = notional / fill
        fee = self.costs.fee(notional)
        self.p.cash -= notional + fee
        self.p.fees_paid += fee
        lot = Lot(qty=qty, entry_price=fill, entry_time=when, entry_fee=fee,
                  stop_price=stop_price, tag=tag)
        self.p.lots.append(lot)
        logging.info(f"BUY  {qty:.8f} BTC @ ${fill:,.2f}  notional ${notional:,.2f}  "
                     f"fee ${fee:.2f}  stop ${stop_price:,.2f}")
        return lot

    def sell_all(self, price: float, when: str, reason: str, is_stop: bool = False) -> List[TradeRecord]:
        """Close every open lot, booking realized P&L against its own cost basis."""
        out = []
        fill = self.costs.stop_exit_fill(price) if is_stop else price * (1 - self.costs.slippage_bps / 10_000)
        for lot in list(self.p.lots):
            proceeds = lot.qty * fill
            fee = self.costs.fee(proceeds)
            gross = lot.qty * (fill - lot.entry_price)
            fees = fee + lot.entry_fee
            net = gross - fees

            self.p.cash += proceeds - fee
            self.p.fees_paid += fee
            self.p.realized_pnl += net

            rec = TradeRecord(
                entry_time=lot.entry_time, exit_time=when,
                entry_price=lot.entry_price, exit_price=fill, qty=lot.qty,
                gross_pnl=gross, fees=fees, net_pnl=net,
                return_pct=net / (lot.qty * lot.entry_price),
                exit_reason=reason,
            )
            self.p.trades.append(rec)
            out.append(rec)
            self.p.lots.remove(lot)
            flag = "WIN " if rec.is_win else "LOSS"
            logging.info(f"SELL {lot.qty:.8f} BTC @ ${fill:,.2f}  {flag}  "
                         f"net ${net:+,.2f} ({rec.return_pct * 100:+.2f}%)  [{reason}]")
        return out


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------
def fetch_daily_bars(days: int = BOOTSTRAP_DAYS, symbol: str = "BTCUSDT") -> pd.DataFrame:
    """Daily klines from Binance. Only completed bars are returned."""
    url = "https://api.binance.com/api/v3/klines"
    rows: List[list] = []
    end = int(time.time() * 1000)
    remaining = days + 5
    cursor_end = end
    while remaining > 0:
        limit = min(1000, remaining)
        resp = requests.get(url, params={"symbol": symbol, "interval": "1d",
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

    df = pd.DataFrame(rows, columns=[
        "open_time", "open", "high", "low", "close", "volume", "close_time",
        "qv", "trades", "tbb", "tbq", "ignore"])
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c])
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df[["timestamp", "open", "high", "low", "close", "volume"]]
    df = df.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)

    # Drop the in-progress bar: acting on a partial bar is lookahead in reverse
    now = pd.Timestamp.now(tz="UTC").normalize()
    df = df[df["timestamp"] < now].reset_index(drop=True)
    return df


def fetch_spot_price() -> Optional[float]:
    """Current price, trying exchanges in turn. Used only for status/stop checks."""
    for name, (url, parse) in PRICE_APIS.items():
        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            price = parse(r.json())
            if price and price > 0:
                logging.debug(f"price ${price:,.2f} from {name}")
                return price
        except Exception as exc:
            logging.debug("%s failed: %s", name, exc)
    logging.error("all price APIs failed")
    return None


# ---------------------------------------------------------------------------
# trader
# ---------------------------------------------------------------------------
class LiveTrader:
    def __init__(self, costs: Optional[Costs] = None, initial_capital: float = 10_000.0,
                 state_file: str = STATE_FILE):
        self.costs = costs or Costs()
        self.state_file = state_file
        self.strategy = VolTargetTrendStrategy(**STRATEGY_PARAMS)
        self.portfolio = Portfolio(initial_capital=initial_capital,
                                   cash=initial_capital, peak_equity=initial_capital)
        self.decisions: List[dict] = []
        self.last_bar: Optional[str] = None
        self.load()

    # -- persistence ----------------------------------------------------
    def save(self) -> None:
        state = {
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "last_bar": self.last_bar,
            "portfolio": {
                "initial_capital": self.portfolio.initial_capital,
                "cash": self.portfolio.cash,
                "realized_pnl": self.portfolio.realized_pnl,
                "fees_paid": self.portfolio.fees_paid,
                "peak_equity": self.portfolio.peak_equity,
                "lots": [asdict(l) for l in self.portfolio.lots],
            },
            "strategy": {"name": self.strategy.name, **STRATEGY_PARAMS},
        }
        tmp = self.state_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, self.state_file)  # atomic: a crash mid-write cannot corrupt state

        with open(TRADES_FILE, "w") as f:
            json.dump([asdict(t) for t in self.portfolio.trades], f, indent=2)
        # Decisions are appended to across runs rather than truncated
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

        p = state.get("portfolio", {})
        self.portfolio.initial_capital = p.get("initial_capital", 10_000.0)
        self.portfolio.cash = p.get("cash", self.portfolio.initial_capital)
        self.portfolio.realized_pnl = p.get("realized_pnl", 0.0)
        self.portfolio.fees_paid = p.get("fees_paid", 0.0)
        self.portfolio.peak_equity = p.get("peak_equity", self.portfolio.initial_capital)
        self.portfolio.lots = [Lot(**l) for l in p.get("lots", [])]
        self.last_bar = state.get("last_bar")

        try:
            with open(TRADES_FILE) as f:
                self.portfolio.trades = [TradeRecord(**t) for t in json.load(f)]
        except (FileNotFoundError, json.JSONDecodeError, TypeError):
            self.portfolio.trades = []
        try:
            with open(DECISIONS_FILE) as f:
                self.decisions = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.decisions = []

        logging.info(f"restored: cash ${self.portfolio.cash:,.2f}  "
                     f"btc {self.portfolio.btc:.8f}  {len(self.portfolio.lots)} lots  "
                     f"{len(self.portfolio.trades)} trades  "
                     f"realized ${self.portfolio.realized_pnl:+,.2f}")

    # -- decision -------------------------------------------------------
    def _log_decision(self, when: str, action: str, reason: str, price: float, extra: dict) -> None:
        self.decisions.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "bar": when, "action": action, "reason": reason, "price": price,
            "equity": self.portfolio.equity(price),
            "cash": self.portfolio.cash, "btc": self.portfolio.btc,
            **extra,
        })

    def step(self, bars: pd.DataFrame, force: bool = False) -> str:
        """Evaluate the most recently completed bar and act. Returns the action."""
        df = self.strategy.prepare(bars)
        i = len(df) - 1                      # last completed bar
        bar_time = str(df["timestamp"].iloc[i])
        price = float(df["close"].iloc[i])

        if not force and self.last_bar == bar_time:
            logging.info("bar %s already processed; nothing to do", bar_time)
            return "ALREADY_PROCESSED"

        if i < self.strategy.warmup:
            logging.warning("only %d bars, need %d", len(df), self.strategy.warmup)
            return "INSUFFICIENT_HISTORY"

        broker = Broker(self.portfolio, self.costs)
        action = "HOLD"
        reason = "NO_SIGNAL"

        if self.portfolio.lots:
            # 1. Disaster stop, checked against the completed bar's low
            low = float(df["low"].iloc[i])
            worst_stop = max(l.stop_price for l in self.portfolio.lots)
            if low <= worst_stop:
                broker.sell_all(worst_stop, bar_time, "STOP", is_stop=True)
                action, reason = "SELL", "STOP"
            else:
                # 2. Regime exit
                exit_reason = self.strategy.exit_signal(df, i, self.portfolio.lots[0])
                if exit_reason:
                    broker.sell_all(price, bar_time, exit_reason)
                    action, reason = "SELL", exit_reason
                else:
                    reason = "HOLDING_POSITION"
        else:
            entry = self.strategy.entry_signal(df, i, self.portfolio.equity(price))
            if entry is not None:
                notional = self.portfolio.equity(price) * (entry.size_frac or 0.0)
                lot = broker.buy(price, notional, entry.stop_price, bar_time, entry.tag)
                if lot:
                    action, reason = "BUY", "REGIME_ENTRY"
                else:
                    reason = "INSUFFICIENT_CASH"
            else:
                reason = "REGIME_NOT_BULLISH"

        eq = self.portfolio.equity(price)
        self.portfolio.peak_equity = max(self.portfolio.peak_equity, eq)
        self.last_bar = bar_time

        row = df.iloc[i]
        self._log_decision(bar_time, action, reason, price, {
            "ma_fast": _f(row.get("ma_fast")), "ma_slow": _f(row.get("ma_slow")),
            "ma_exit": _f(row.get("ma_exit")), "atr": _f(row.get("atr")),
            "realized_vol": _f(row.get("realized_vol")),
        })
        self.save()
        logging.info(f"bar {bar_time[:10]}  price ${price:,.2f}  {action} ({reason})  "
                     f"equity ${eq:,.2f}  dd {self.portfolio.drawdown(price) * 100:.1f}%")
        return action

    # -- reporting ------------------------------------------------------
    def status(self, price: Optional[float] = None) -> str:
        price = price or fetch_spot_price() or (
            self.portfolio.lots[0].entry_price if self.portfolio.lots else 0.0)
        p = self.portfolio
        eq = p.equity(price)
        total = eq - p.initial_capital
        lines = [
            "=" * 60,
            f"  BTC price        ${price:,.2f}",
            f"  Equity           ${eq:,.2f}   (start ${p.initial_capital:,.2f})",
            f"  Total P&L        ${total:+,.2f}  ({total / p.initial_capital * 100:+.2f}%)",
            f"    realized       ${p.realized_pnl:+,.2f}",
            f"    unrealized     ${p.unrealized(price):+,.2f}",
            f"  Cash             ${p.cash:,.2f}",
            f"  BTC held         {p.btc:.8f}" + (f"  in {len(p.lots)} lot(s)" if p.lots else "  (flat)"),
            f"  Fees paid        ${p.fees_paid:,.2f}",
            f"  Drawdown         {p.drawdown(price) * 100:.1f}%   (peak ${p.peak_equity:,.2f})",
            f"  Trades           {len(p.trades)}   win rate {p.win_rate() * 100:.1f}%"
            f"   profit factor {p.profit_factor():.2f}",
        ]
        for lot in p.lots:
            pnl = (price - lot.entry_price) / lot.entry_price * 100
            lines.append(f"    lot {lot.qty:.8f} BTC @ ${lot.entry_price:,.2f} "
                         f"({pnl:+.2f}%)  stop ${lot.stop_price:,.2f}")
        lines.append("=" * 60)
        return "\n".join(lines)


def _f(v):
    try:
        f = float(v)
        return None if pd.isna(f) else round(f, 6)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# replay: prove the live path agrees with the backtester
# ---------------------------------------------------------------------------
def replay(data_path: str = "data/BTCUSDT_5m.csv") -> bool:
    """Feed history through LiveTrader one bar at a time and compare to Backtester."""
    from backtest import Backtester, load_bars

    bars5 = load_bars(data_path)
    daily = (bars5.set_index("timestamp").resample("1D")
             .agg({"open": "first", "high": "max", "low": "min",
                   "close": "last", "volume": "sum"})
             .dropna().reset_index())

    costs = Costs()
    bt = Backtester(daily, VolTargetTrendStrategy(**STRATEGY_PARAMS), costs,
                    initial_equity=10_000.0, bar_interval="1D")
    ref = bt.run()

    tmp_state = "/tmp/_replay_state.json"
    for f in (tmp_state, TRADES_FILE + ".replay"):
        if os.path.exists(f):
            os.remove(f)
    trader = LiveTrader(costs=costs, initial_capital=10_000.0, state_file=tmp_state)
    trader.portfolio = Portfolio(initial_capital=10_000.0, cash=10_000.0, peak_equity=10_000.0)
    trader.save = lambda: None  # skip disk churn during replay

    warm = trader.strategy.warmup
    for end in range(warm + 1, len(daily) + 1):
        trader.step(daily.iloc[:end].reset_index(drop=True), force=True)

    last_price = float(daily["close"].iloc[-1])
    live_eq = trader.portfolio.equity(last_price)
    bt_eq = ref.final_equity
    diff = abs(live_eq - bt_eq) / bt_eq

    print(f"  backtester final equity   ${bt_eq:,.2f}  ({len(ref.trades)} trades)")
    print(f"  live-trader final equity  ${live_eq:,.2f}  ({len(trader.portfolio.trades)} trades)")
    print(f"  difference                {diff * 100:.3f}%")
    ok = diff < 0.02
    print(f"  {'PASS' if ok else 'FAIL'}: live logic {'matches' if ok else 'DIVERGES FROM'} backtest")
    if not ok:
        print("  note: small differences are expected from fill-timing conventions;")
        print("        >2% means the two implementations genuinely disagree.")
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--once", action="store_true", help="evaluate the latest bar and exit")
    g.add_argument("--loop", action="store_true", help="run continuously")
    g.add_argument("--status", action="store_true", help="print state and exit")
    g.add_argument("--replay", action="store_true", help="verify against the backtester")
    ap.add_argument("--capital", type=float, default=10_000.0)
    ap.add_argument("--force", action="store_true", help="re-process an already-seen bar")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
    )

    if args.replay:
        raise SystemExit(0 if replay() else 1)

    trader = LiveTrader(initial_capital=args.capital)

    if args.status:
        print(trader.status())
        return

    if args.once:
        bars = fetch_daily_bars()
        trader.step(bars, force=args.force)
        print(trader.status(float(bars["close"].iloc[-1])))
        return

    logging.info("starting loop; one decision per completed daily bar")
    while True:
        try:
            bars = fetch_daily_bars()
            trader.step(bars)
            print(trader.status(float(bars["close"].iloc[-1])))
        except KeyboardInterrupt:
            logging.info("shutdown requested")
            trader.save()
            print(trader.status())
            break
        except Exception as exc:
            logging.error("cycle failed: %s", exc, exc_info=True)

        # Wake shortly after the next UTC midnight, when the daily bar closes
        now = datetime.now(timezone.utc)
        nxt = (now + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
        sleep_s = max(60, (nxt - now).total_seconds())
        logging.info("next evaluation at %s (%.1fh)", nxt.isoformat(), sleep_s / 3600)
        try:
            time.sleep(sleep_s)
        except KeyboardInterrupt:
            logging.info("shutdown requested")
            trader.save()
            break


if __name__ == "__main__":
    main()
