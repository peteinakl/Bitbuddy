"""Trade records, results and performance statistics."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

import numpy as np
import pandas as pd

BARS_PER_DAY = {"5m": 288, "15m": 96, "1h": 24, "4h": 6, "12h": 2, "1d": 1}


@dataclass
class Trade:
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    qty: float
    gross_pnl: float
    fees: float
    net_pnl: float
    return_pct: float
    bars_held: int
    exit_reason: str
    tag: str = ""

    @property
    def is_win(self) -> bool:
        return self.net_pnl > 0


@dataclass
class Result:
    trades: List[Trade]
    equity: pd.Series
    interval: str = "1d"
    benchmark: pd.Series = field(default_factory=pd.Series)
    exposure: float = 0.0

    # -- basics ---------------------------------------------------------
    @property
    def initial_equity(self) -> float:
        return float(self.equity.iloc[0])

    @property
    def final_equity(self) -> float:
        return float(self.equity.iloc[-1])

    @property
    def total_return(self) -> float:
        return self.final_equity / self.initial_equity - 1

    @property
    def years(self) -> float:
        span = self.equity.index[-1] - self.equity.index[0]
        return span.total_seconds() / (365.25 * 86400)

    @property
    def cagr(self) -> float:
        if self.years <= 0 or self.final_equity <= 0:
            return -1.0
        return (self.final_equity / self.initial_equity) ** (1 / self.years) - 1

    def _daily(self) -> pd.Series:
        d = self.equity.resample("D").last().dropna()
        return d.pct_change(fill_method=None).dropna()

    # -- risk -----------------------------------------------------------
    @property
    def sharpe(self) -> float:
        r = self._daily()
        if len(r) < 2 or r.std() == 0:
            return 0.0
        return float(r.mean() / r.std() * np.sqrt(365))

    @property
    def sortino(self) -> float:
        r = self._daily()
        down = r[r < 0]
        if len(down) < 2 or down.std() == 0:
            return 0.0
        return float(r.mean() / down.std() * np.sqrt(365))

    @property
    def max_drawdown(self) -> float:
        return float((self.equity / self.equity.cummax() - 1).min())

    @property
    def calmar(self) -> float:
        dd = abs(self.max_drawdown)
        return self.cagr / dd if dd > 0 else 0.0

    @property
    def ulcer_index(self) -> float:
        """RMS drawdown. Penalises long shallow pain, which max-DD hides."""
        dd = self.equity / self.equity.cummax() - 1
        return float(np.sqrt((dd ** 2).mean()))

    @property
    def longest_drawdown_days(self) -> int:
        eq = self.equity
        peak = eq.cummax()
        under = eq < peak * 0.9999
        if not under.any():
            return 0
        best = cur = 0
        start = None
        for ts, flag in under.items():
            if flag:
                start = start or ts
                cur = (ts - start).days
                best = max(best, cur)
            else:
                start, cur = None, 0
        return int(best)

    # -- trade stats ----------------------------------------------------
    @property
    def win_rate(self) -> float:
        return sum(1 for t in self.trades if t.is_win) / len(self.trades) if self.trades else 0.0

    @property
    def profit_factor(self) -> float:
        gains = sum(t.net_pnl for t in self.trades if t.net_pnl > 0)
        losses = -sum(t.net_pnl for t in self.trades if t.net_pnl < 0)
        if losses == 0:
            return float("inf") if gains > 0 else 0.0
        return gains / losses

    @property
    def expectancy_dollar(self) -> float:
        """Size-weighted mean P&L per trade; reconciles with the equity curve."""
        return float(np.mean([t.net_pnl for t in self.trades])) if self.trades else 0.0

    @property
    def avg_win_pct(self) -> float:
        w = [t.return_pct for t in self.trades if t.is_win]
        return float(np.mean(w)) if w else 0.0

    @property
    def avg_loss_pct(self) -> float:
        l = [t.return_pct for t in self.trades if not t.is_win]
        return float(np.mean(l)) if l else 0.0

    @property
    def largest_win_pct(self) -> float:
        return max((t.return_pct for t in self.trades), default=0.0)

    @property
    def largest_loss_pct(self) -> float:
        return min((t.return_pct for t in self.trades), default=0.0)

    @property
    def total_fees(self) -> float:
        return sum(t.fees for t in self.trades)

    @property
    def avg_bars_held(self) -> float:
        return float(np.mean([t.bars_held for t in self.trades])) if self.trades else 0.0

    @property
    def trades_per_year(self) -> float:
        return len(self.trades) / self.years if self.years > 0 else 0.0

    @property
    def exit_breakdown(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for t in self.trades:
            out[t.exit_reason] = out.get(t.exit_reason, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    # -- benchmark ------------------------------------------------------
    @property
    def benchmark_return(self) -> float:
        if len(self.benchmark) < 2:
            return 0.0
        return float(self.benchmark.iloc[-1] / self.benchmark.iloc[0] - 1)

    @property
    def benchmark_max_drawdown(self) -> float:
        if len(self.benchmark) < 2:
            return 0.0
        return float((self.benchmark / self.benchmark.cummax() - 1).min())

    def as_dict(self) -> Dict:
        return {
            "ret": self.total_return, "cagr": self.cagr, "dd": self.max_drawdown,
            "sharpe": self.sharpe, "sortino": self.sortino, "calmar": self.calmar,
            "ulcer": self.ulcer_index, "n": len(self.trades),
            "win": self.win_rate, "pf": self.profit_factor,
            "exposure": self.exposure, "fees": self.total_fees,
        }

    def summary(self, name: str = "strategy") -> str:
        bpd = BARS_PER_DAY.get(self.interval, 1)
        hold_days = self.avg_bars_held / bpd
        lines = [
            f"── {name} ──",
            f"  Period            {self.equity.index[0].date()} → {self.equity.index[-1].date()}  ({self.years:.2f}y)",
            f"  Final equity      ${self.final_equity:,.0f}  from ${self.initial_equity:,.0f}",
            f"  Total return      {self.total_return * 100:+.1f}%      CAGR {self.cagr * 100:+.1f}%",
            f"  Max drawdown      {self.max_drawdown * 100:.1f}%       Ulcer {self.ulcer_index * 100:.1f}%",
            f"  Longest DD        {self.longest_drawdown_days} days",
            f"  Sharpe / Sortino  {self.sharpe:.2f} / {self.sortino:.2f}      Calmar {self.calmar:.2f}",
            f"  Exposure          {self.exposure * 100:.0f}% of bars in market",
            f"  Trades            {len(self.trades)}  ({self.trades_per_year:.0f}/yr)  avg hold {hold_days:.1f}d",
            f"  Win rate          {self.win_rate * 100:.1f}%       Profit factor {self.profit_factor:.2f}",
            f"  Avg win / loss    {self.avg_win_pct * 100:+.2f}% / {self.avg_loss_pct * 100:+.2f}%",
            f"  Best / worst      {self.largest_win_pct * 100:+.1f}% / {self.largest_loss_pct * 100:+.1f}%",
            f"  Fees paid         ${self.total_fees:,.0f}",
            f"  Exits             {self.exit_breakdown}",
        ]
        if len(self.benchmark) > 1:
            lines.append(f"  vs buy & hold     {self.benchmark_return * 100:+.1f}% return, "
                         f"{self.benchmark_max_drawdown * 100:.1f}% DD")
        return "\n".join(lines)
