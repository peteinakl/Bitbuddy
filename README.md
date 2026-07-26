# Bitcoin Trading Bot Simulator

A Python Bitcoin trading simulator with a **backtest-first** workflow: strategies are measured on years of real market data with realistic costs before they are allowed to run, and the live paper trader shares its strategy code with the backtester so that what gets validated is what actually runs.

**⚠️ This is a SIMULATION.** No real money is traded, no exchange credentials are used, no orders are placed. Nothing here is financial advice.

## Why this exists

The original version of this bot traded on 1-hour momentum with a fixed 1.5% profit target and a -0.8% stop, and reported its own results using a P&L calculation that was structurally always zero — so it could not tell whether it was winning or losing.

When those rules were backtested honestly against 3.6 years of BTC data with fees and slippage, they lost **99.2%** of capital. Fees alone consumed 91% of the starting balance across 4,972 trades.

That measurement is what the current design is a response to.

## Measured results

3.6 years of BTCUSDT, 2023-01-01 → 2026-07-26. Costs: 0.1% fee per side plus slippage (0.22% round trip). Signals are taken from a bar's close and filled at the **next** bar's open, so no lookahead.

| | return | CAGR | max DD | Sharpe | Calmar | trades | exposure |
|---|---|---|---|---|---|---|---|
| **current strategy** | **+157.7%** | +37.1% | **-27.4%** | **1.19** | **1.35** | 29 | 48% |
| buy & hold | +288.4% | +46.3% | -53.0% | 1.05 | 0.87 | — | 100% |
| original bot's rules | **-99.2%** | -74.0% | -99.3% | -6.72 | — | 4,972 | 100% |

Split by regime:

| period | strategy | buy & hold |
|---|---|---|
| bull 2023-01 → 2025-06 | +168.7% | +544.8% |
| bear 2025-07 → 2026-07 | **-3.1%** | **-38.9%** |

**Out-of-sample walk-forward** — parameters chosen only on prior data, applied to the next unseen 6 months, stitched together (2024-07 → 2026-06):

| | return | CAGR | max DD | Sharpe |
|---|---|---|---|---|
| fixed parameters | **+34.9%** | +16.2% | **-18.0%** | 0.72 |
| refit each window | +30.6% | +14.3% | -20.1% | 0.66 |
| buy & hold | -6.8% | — | -53.0% | 0.15 |

Fixed parameters beating per-window refitting is the useful signal here: the edge is structural rather than curve-fit, so the shipped configuration is the conventional 50/200 one rather than a tuned one.

### Read this before getting excited

- **It does not beat buy & hold on raw return.** Over a window where BTC rose 4x, a long-only spot strategy essentially cannot. The edge is risk-adjusted: better Sharpe and Calmar, roughly half the drawdown, and only 48% time in market.
- **It cannot profit from a downtrend**, only sit one out. Long or flat, no shorting, no leverage.
- **29 trades is a small sample.** One asset, one market cycle. Treat the numbers as indicative.
- **A 28% win rate is normal here.** Three trades produced nearly all the profit (+53%, +53%, +48%) against many small losses. That is what trend following looks like, and it is why a fixed profit target is so damaging — it caps exactly the trades that pay for everything else.

## Strategy

`VolTargetTrendStrategy` on daily bars — long/flat trend following.

- **Entry** — close > EMA200, EMA50 > EMA200, and close > EMA50
- **Exit** — close < EMA50, a regime flip, or an 8×ATR disaster stop
- **Size** — `min(target_vol / realized_vol, 0.95)` of equity, so position size shrinks as volatility rises

Four design choices, each measured rather than assumed:

1. **Two-condition regime gate.** Requiring the moving-average structure to be bullish (not just price above a line) keeps the bot completely flat through sustained bear markets — 0 trades in H1 2026 while BTC fell 34%. Removing the confirmation turns the bear period from -3.1% into -10.7%.
2. **No profit target.** Trend following pays from a handful of large moves; capping them removes the edge.
3. **ATR-based stops, not percentage stops.** BTC's hourly volatility is ~0.5%, so the original -0.8% stop sat 1.6 sigma away and was triggered by noise. This stop is wide and fires only on crashes.
4. **Low turnover.** 29 trades in 3.6 years, and the edge survives **4× the assumed costs** (Sharpe 0.98). The original strategy died of fees.

## Install

```bash
pip install -r requirements.txt
python fetch_data.py            # downloads history into data/ (~375k bars, ~2 min)
```

## Usage

Paper trading:

```bash
python live_trader.py --once      # evaluate the latest completed daily bar
python live_trader.py --loop      # run continuously, one decision per day
python live_trader.py --status    # portfolio state
python live_trader.py --replay    # verify live logic reproduces the backtest
```

Research:

```bash
python compare.py                    # strategies vs buy & hold, by regime
python compare.py --include-legacy   # include the original bot's rules
python validate.py                   # walk-forward, sensitivity, cost shock
python sweep.py --strategy voltarget --timeframe 1D
python run_backtest.py --strategy legacy
```

Tests:

```bash
python test_backtest.py       # engine accounting (no lookahead, costs, conservation)
python test_live_trader.py    # live accounting, persistence, backtest agreement
```

## How it fits together

One strategy implementation, two consumers:

```
strategies.py  ──┬──  backtest.py   (research: sweep, compare, validate)
                 └──  live_trader.py (paper trading)
```

`live_trader.py --replay` runs the live decision path over history and asserts it matches the backtester — currently within 0.001% on final equity with identical trade counts. Run it after touching strategy or execution code; it is what stops the two paths silently diverging.

### Backtest honesty

The engine is deliberately pessimistic, because the default failure mode of a backtest is inventing profit:

- **No lookahead** — a signal from bar *i*'s close fills at bar *i+1*'s open
- **Costs always charged** — fee per side plus slippage; stop exits pay extra, since they are market orders into a move already against you
- **Pessimistic intrabar ordering** — if a bar's range contains both stop and target, the stop is taken
- **No leverage** — the engine refuses to spend cash it does not have

`test_backtest.py` asserts each of these.

## Project layout

| file | role |
|---|---|
| `strategies.py` | strategy implementations, shared by backtest and live |
| `backtest.py` | event-driven engine, cost model, metrics |
| `live_trader.py` | paper trading: portfolio, broker, persistence |
| `fetch_data.py` | historical data download |
| `compare.py` | head-to-head vs buy & hold by period |
| `validate.py` | walk-forward, parameter sensitivity, cost shock |
| `sweep.py` | parameter grids with train/test split |
| `run_backtest.py` | single-strategy runs |
| `test_backtest.py`, `test_live_trader.py` | test suites |
| `bitcoin_trading_bot.py` | **superseded** original bot, kept for reference |

Runtime files (`data/`, `live_state.json`, `live_trades.json`, `live_decisions.json`, logs) are gitignored.

## Contributing

Genuinely useful directions:

- **Multi-asset.** Trend following works better across several instruments than on one; it is also the cleanest test of whether this edge is real or fitted to BTC.
- **Short side.** The 2026 decline is unexploitable as-is. Adding shorts needs perpetual futures, which means modelling funding rates and liquidation honestly.
- **More history.** 2017-2022 would add two more cycles and materially strengthen the walk-forward evidence.
- **Regime-aware sizing** beyond simple volatility targeting.

If you change the strategy, show `validate.py` output. An improvement that appears in-sample but not out-of-sample is overfitting, and the walk-forward block stops being out-of-sample the moment parameters are chosen against it.

## Disclaimer

Educational and testing purposes only. This does not connect to real accounts, handle real money, or execute real trades. Simulated results are not predictive of live results — real trading adds slippage on size, exchange downtime, API failures, and your own behaviour under drawdown. Do not trade real money on this without substantial additional work and professional advice.

## License

MIT.
