# Bitcoin Trading Bot Simulator

A Python Bitcoin trading simulator with a **backtest-first** workflow: strategies are measured across 9 years of real market data with realistic costs before they run, and the live paper trader shares its strategy code with the backtester so what gets validated is what actually runs.

**⚠️ This is a SIMULATION.** No real money, no exchange credentials, no orders placed. Nothing here is financial advice.

## Why this exists

The original bot traded 1-hour momentum with a fixed 1.5% target and -0.8% stop, and computed its own P&L as portfolio-value delta across a cash↔BTC conversion — a quantity that is zero by construction, so it could not tell winning from losing.

Backtested honestly with fees and slippage, those rules lose **99.2%** of capital. Fees alone consumed 91% of the starting balance across 4,972 trades.

Everything here is a response to that measurement.

## Results

9 years of BTCUSDT daily bars, 2017-08-17 → 2026-07-26. Costs 0.22% round trip. Signals from a bar's close fill at the **next** bar's open, so no lookahead.

| | return | CAGR | max DD | Sharpe | Calmar | exposure |
|---|---|---|---|---|---|---|
| **shipped strategy** | +801% | 31.2% | **-29%** | **1.18** | **1.07** | 35% |
| higher-risk variant | **+1759%** | 39.2% | -41% | 1.19 | 0.94 | 51% |
| buy & hold | +1406% | 35.4% | -83% | 0.79 | — | 100% |
| original bot's rules | **-99.2%** | -74.0% | -99.3% | -6.72 | — | 100% |

**True out-of-sample walk-forward, 2020-08 → 2026-07** — parameters chosen only on prior data, applied forward, stitched:

| | return | CAGR | max DD | Sharpe |
|---|---|---|---|---|
| fixed parameters | **+1014%** | 50.1% | **-39%** | **0.88** |
| refit each window | +614% | 39.2% | -47% | 0.75 |
| buy & hold | +424% | 32.2% | -77% | 0.77 |

Fixed parameters beating refitting is the important signal: the parameter surface is mostly noise and the edge is structural, so the simple conventional config ships.

**Per calendar year (%)** — the strategy sits bears out rather than fighting them:

| | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|---|
| shipped | **-7** | +32 | +360 | +34 | **-1** | +48 | +49 | **0** | **0** |
| buy & hold | -72 | +89 | +302 | +58 | -65 | +154 | +112 | -7 | -27 |

### Read this before getting excited

- **It loses to buy & hold on raw return** (+801% vs +1406%). It wins on drawdown (-29% vs -83%), Sharpe and Calmar, holding a position only 35% of the time. A long-only spot strategy on an asset that rose 14x cannot beat holding it on raw return; the win is risk-adjusted.
- **It cannot profit from a downtrend**, only avoid one. Long or flat, no shorting, no leverage.
- **76 trades in 9 years.** Small sample. Treat point estimates as indicative.
- **Roughly 40% win rate is normal.** A handful of trades produce nearly all the profit. That is what trend following is, and it is why the original bot's fixed 1.5% target was so damaging — it capped exactly the trades that pay for everything else.
- One asset, three cycles. The cleanest remaining test is running these rules on ETH/SOL.

## Strategy

`DualMomentum(lookback=30, slow_ma=300, exit_ma=20, target_vol=0.4, stop_atr=8.0)` on daily bars.

- **Entry** — 30-day return > 0, close > EMA300, close > EMA20
- **Exit** — close < EMA20, momentum turning negative below EMA300, or an 8×ATR crash stop
- **Size** — `min(0.4 / realized_vol, 0.95)` of equity, so exposure shrinks as volatility rises

Trade characteristics: **36% win rate**, profit factor 2.44, average win +19.4% against average loss -3.2%. The top 3 trades produced **74%** of all net profit — which is exactly why the original bot's fixed 1.5% target was fatal, since it capped the trades that pay for everything else.

Robustness, all measured:

- **80/80** neighbouring configurations profitable on development data (Sharpe median 1.07, min 0.73) — a plateau, not a spike
- Survives **4× assumed costs** (+509% full period), because it only trades ~8 times a year

### The Sharpe ceiling, and choosing your point on it

Ablating the two signals reveals something more useful than a single winner:

| variant | return | max DD | Sharpe | Calmar |
|---|---|---|---|---|
| momentum only (`TsMomentum 30`) | **+2242%** | -52% | 1.17 | 0.83 |
| `MaTrend 20/30` no confirmation | +1759% | -41% | 1.19 | 0.94 |
| **both (shipped `DualMomentum`)** | +801% | **-29%** | 1.18 | **1.07** |
| buy & hold | +1406% | -83% | 0.79 | — |

Every trend variant lands at **Sharpe ~1.17-1.19**. They are not better or worse than each other in risk-adjusted terms — they are the *same edge* dialled to different exposure. What you actually choose is how much drawdown to accept: -29% for +801%, or -52% for +2242%.

So "momentum alone is worse" would be false. It earns nearly 3x more; it just hurts more on the way. The shipped config maximises Calmar (return per unit of drawdown) per the risk-adjusted objective. Both alternatives are one-line switches to `STRATEGY`/`STRATEGY_PARAMS` in `bitbuddy/live/trader.py`.

## Install

```bash
pip install -r requirements.txt
python fetch_data.py --interval 1d        # ~3,300 bars back to Binance's 2017 listing
```

## Usage

```bash
# paper trading
python live_trader.py --once              # evaluate the latest completed bar
python live_trader.py --loop              # one decision per day
python live_trader.py --status
python live_trader.py --replay            # verify live logic reproduces the backtest

# research
python optimise.py                        # search families with a held-out block
python validate.py                        # finalist comparison + robustness
python validate.py --check wf             # walk-forward only

# tests
python tests/test_engine.py               # engine honesty: 8 groups
python tests/test_live.py                 # live accounting + replay: 6 groups
```

## Architecture

```
bitbuddy/
  data.py         load / resample / download bars
  costs.py        fee + slippage model
  indicators.py   causal numpy primitives
  engine.py       backtest engine (Backtester, Context, make_context)
  metrics.py      Result: returns, Sharpe/Sortino/Calmar, ulcer, exposure
  strategies/     MaTrend, TsMomentum, DualMomentum, Donchian, LegacyBot
  research/       walk-forward, parameter search
  live/           portfolio accounting, simulated broker, live loop
```

One strategy implementation feeds both the backtester and the live trader through the same `make_context()`. `live_trader.py --replay` asserts they agree over the full history — currently **0.009%** on final equity with identical trade counts. It is what stops the two paths silently diverging.

The engine runs at ~544k bars/s because strategies receive numpy arrays rather than DataFrame rows, which is what makes searching thousands of configurations practical.

### Backtest honesty

The engine is deliberately pessimistic, since the default failure mode of a backtest is inventing profit:

- **No lookahead** — a signal from bar *i*'s close fills at bar *i+1*'s open
- **Costs always charged** — fee per side plus slippage, extra on stop exits
- **Pessimistic intrabar ordering** — a bar spanning both stop and target takes the stop
- **No leverage** — the engine refuses to spend cash it does not hold
- **Conservation** — final equity equals initial plus the sum of net P&L, exactly

`tests/test_engine.py` asserts each one.

### Selection protocol

```
2017-08 ───────── development ───────── 2024-07 ──── holdout ──── 2026-07
          all searching and selection            scored once at the end
```

Within development, configurations are ranked by **median Sharpe across consecutive blocks** — aggregate performance rewards a config that made all its money in one regime — subject to a hard aggregate-drawdown constraint. The holdout is scored only after selection is final.

If you change the strategy, show `validate.py` output. An improvement that appears in-sample but not in walk-forward is overfitting, and the holdout stops being a holdout the moment parameters are chosen against it.

## Project layout

| path | role |
|---|---|
| `bitbuddy/` | the package (see above) |
| `tests/` | engine and live test suites |
| `fetch_data.py` | historical data download |
| `optimise.py` | strategy/parameter search |
| `validate.py` | robustness validation |
| `live_trader.py` | paper-trading CLI |
| `bitcoin_trading_bot.py` | **superseded** original bot, kept for reference |

Runtime files (`data/`, `live_state.json`, `live_trades.json`, `live_decisions.json`, logs) are gitignored.

## Contributing

Genuinely useful directions:

- **Multi-asset.** Trend following works better across instruments than on one, and it is the cleanest test of whether this edge is real or fitted to BTC.
- **Short side.** Downtrends are unexploitable as-is. Adding shorts needs perpetual futures, so funding rates and liquidation must be modelled honestly.
- **Regime-aware sizing** beyond simple volatility targeting.
- **Execution realism** — order-book depth, partial fills, exchange downtime.

## Disclaimer

Educational and testing purposes only. This does not connect to real accounts, handle real money, or execute real trades. Simulated results are not predictive of live results — real trading adds slippage on size, exchange downtime, API failures, and your own behaviour under drawdown. Do not trade real money on this without substantial additional work and professional advice.

## License

MIT.
