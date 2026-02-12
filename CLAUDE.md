# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Python-based Bitcoin trading bot simulator that tests trading strategies without real money. The bot uses real-time market data from multiple exchanges (Binance, Kraken, Bitfinex, Bitstamp, CoinGecko) to simulate trades and learn from performance patterns.

**Important**: This is a SIMULATION tool. No real money is traded. All trades are simulated based on real market data.

## Running the Bot

Start the bot:
```bash
python bitcoin_trading_bot.py
```

The bot will:
- Initialize from saved state (bot_state.json) or default settings
- Monitor Bitcoin prices every 5-10 seconds
- Execute trading decisions every 2 minutes
- Display status every minute
- Save state every 3 minutes
- Cleanup old data every hour

## Dependencies

Install required packages:
```bash
pip install -r requirements.txt
```

Required packages: requests, schedule, numpy, pandas

## Architecture

### Single-File Design
The entire bot is implemented in `bitcoin_trading_bot.py` as a single `BitcoinTradingBot` class (~2500 lines). This design choice centralizes all trading logic, state management, and learning algorithms in one place.

### Core Components

**API Management & Data Fetching**:
- `fetch_bitcoin_price()` (line 417): Rotates between 5 exchange APIs with rate limiting and error handling
- `update_price_history()` (line 493): **NEW** - Fetches and stores prices every 5 minutes for momentum calculations (builds 1-hour window)
- `rotate_api()` (line 485): Switches APIs when rate limits or errors occur
- `check_rate_limit()` (line 1065): Enforces per-API rate limits (configurable per exchange)
- API state tracked in `api_rotation` dict (lines 50-86) with cooldown periods and error counts
- **Critical**: Price history must be populated at 5-minute intervals for momentum/volatility calculations to work
- **Design**: 12 prices at 5-min intervals = 1-hour rolling window, aligned with day trading timeframe

**Market Analysis**:
- `analyze_market_condition()` (line 515): Classifies market into STRONG_BULLISH, BULLISH, NEUTRAL, BEARISH, STRONG_BEARISH, VOLATILE_RANGE, or RANGING
- `analyze_market_momentum()` (line 1233): **ENHANCED** - Calculates momentum with diagnostic logging, error handling, and fallback logic
  - Logs: price_history size, price range, raw momentum value, direction, volatility
  - Returns zero momentum safely if calculation fails
  - Requires minimum 2 prices in history
- `calculate_volatility()` (line 1447): Standard deviation-based volatility calculation
- `identify_pattern()` (line 2186): Extracts price patterns from recent history (TREND_REVERSAL, RANGE_BOUND, BREAKOUT)
- `check_data_pipeline_health()` (line 528): **NEW** - Tests if momentum/volatility calculations are working correctly
- Uses rolling windows: 720 data points (1 hour of 5-second data)
- **Warm-up period**: Requires 12 prices (momentum_window) before trading begins

**Position Management**:
- Supports multiple stacked positions (max 3 by default)
- Each position tracked with entry price, size, stop loss, profit target
- `manage_position()` (line 1087): Monitors positions for exits based on profit targets, stop losses, or trailing stops
- `position_stack` list holds active positions
- Dynamic position sizing based on market conditions (20-60% of capital)

**Trading Decision System**:
- `execute_trade_decision()` (line 1005): Main entry point called every 15 minutes (day trading mode)
  - **Warm-up check**: Skips trading if insufficient price history (<12 prices)
  - **Health check**: Monitors data pipeline every 10 decisions
  - Calls circuit breaker check before all trading decisions
- `_get_trade_decision()` (line 1126): **ENHANCED** - Analyzes market and returns BUY/SELL/HOLD decision
  - Tracks entry rejection rate (rejections / attempts)
  - **Fallback logic**: Uses simple trend detection if momentum returns 0
  - Requires positive momentum (0.0005+) AND uptrend for BUY entries
- `_validate_entry_conditions()` (line 2148): Checks if market conditions are suitable for entry
- `check_and_adjust_entry_thresholds()` (line 2168): **NEW** - Auto-reduces thresholds by 20% if rejection rate > 90%
- `calculate_buy_amount()` (line 1771): Determines position size based on market condition and available capital
- Minimum trade size: $100 USD

**Risk Management (Critical - Recently Improved)**:
- `check_risk_limits()` (line 1118): **Circuit breaker system** that halts trading if:
  - Max drawdown exceeds 15% (force liquidates positions)
  - 5 consecutive losing trades occur
  - Daily loss exceeds 5% of initial capital
  - Cash reserve drops below 20% of initial capital
- Position sizes automatically reduced by 50% when drawdown exceeds 10%
- `calculate_var()` (line 1501): Value at Risk calculation at specified confidence level
- `calculate_drawdown()` (line 1875): Current drawdown from peak portfolio value
- `is_bull_market()` (line 1542): Long-term trend detection to adjust risk appetite
- Per-position stop losses (default -0.8%) and profit targets (default 1.5%)

**Parameter Management (Recently Fixed)**:
- `adjust_trading_parameters()` (line 1014): **CRITICAL FIX** - Now correctly WIDENS stops when losing (was backwards)
- When losing (win_rate < 0.4): Stops widen to -0.5% minimum, profit targets increase to 1% minimum
- When winning (win_rate > 0.4): Stops tighten to -0.8%, profit targets optimize to avg_profit * 0.8

**Machine Learning & Adaptation**:
- `learning_data` dict (line 175) stores patterns that led to profits/losses
- `update_learning_data()` (line 1118): Records trade outcomes with market conditions
- `adjust_strategy_weights()` (line 1157): Modifies strategy component weights (momentum, trend, volatility, time_of_day) based on success rates
- `adjust_trading_parameters()` (line 1014): Updates thresholds dynamically based on recent performance
- `update_adaptive_thresholds()` (line 1698): Learns optimal profit targets and stop losses
- `learn_from_trade()` (line 2223): Builds pattern confidence metrics
- Pattern-based learning: extracts price patterns and correlates with trade outcomes

**Performance Tracking**:
- `calculate_performance_metrics()` (line 1851): Comprehensive metrics including Sharpe ratio, Sortino ratio, win rate, profit factor
- `track_metrics()` (line 1800): Called periodically to log current performance
- `generate_report()` (line 637): Creates detailed performance summary
- Metrics saved to performance_history.json
- Trade history saved to trade_history.json

**State Persistence**:
- `save_bot_state()` (line 1300): Serializes entire bot state to bot_state.json
- `load_bot_state()` (line 1365): Restores bot from saved state on startup
- `load_existing_portfolio()` (line 315): Specifically loads capital and BTC holdings
- Separate files: bot_state.json (full state), trade_history.json (trades), learning_data.json (ML data), market_data.csv (price history)

### Main Loop (in main(), line 2900)

Uses `schedule` library for periodic tasks (Day Trading Mode):
- **Every 5 minutes: `update_price_history()`** - **CRITICAL** for momentum calculations (builds 1-hour window)
- Every 5 minutes: `display_status()`
- Every 15 minutes: `execute_trade_decision()` - **96 decisions per day**
- Every 15 minutes: `save_bot_state()`
- Every 1 hour: `cleanup_old_data()`

**Startup Warm-Up (NEW):** Bot collects 12 initial prices (~2 minutes at 10-second intervals) before making first trade decision. This ensures momentum/volatility calculations have sufficient data without excessive API calls.

**Timeframe Rationale:** 15-minute decision cycles align with 1.5% profit targets, as Bitcoin typically needs 2-6 hours to move 1.5% in normal conditions. This provides 96 decision opportunities per day while reducing API calls by 87% compared to 2-minute cycles.

Graceful shutdown on KeyboardInterrupt: saves state, displays final status, generates report.

## Key Data Files

### Core State Files
- `bot_state.json`: Complete bot state including capital, positions, parameters, learning data
- `trade_history.json`: Array of all executed trades with timestamps and outcomes
- `learning_data.json`: Machine learning patterns and success rates
- `market_data.csv`: Historical price data with timestamps
- `performance_history.json`: Periodic performance snapshots
- `trading_bot.log`: Detailed logging output
- `current_status.txt`: Latest human-readable status (updated every minute)

### Audit Trail Files (NEW - 2026)
- `decision_audit_log.json`: **Complete audit of ALL decisions** - executed trades, rejected trades, holds, circuit breakers
- `parameter_changes.json`: **Parameter evolution tracking** - logs how bot adapts parameters based on performance
- See `AUDIT_TRAIL.md` for comprehensive documentation of the audit system

## Trading Parameters (Recently Updated)

Key adjustable parameters in `__init__` (lines 114-227):
- `min_position_size = 0.20`, `max_position_size = 0.60`: Position sizing bounds (20-60%, hard capped)
- `position_step = 0.10`: Incremental adjustment size (10%)
- `scalp_threshold = 0.001`: Minimum movement to trigger trades (0.1%)
- `profit_target = 0.015`: Default profit target (1.5%, realistic for Bitcoin)
- `stop_loss = -0.008`: Default stop loss (-0.8%, accounts for Bitcoin volatility)
- `trailing_stop = 0.005`: Trailing stop distance (0.5%)
- `max_positions = 3`: Maximum concurrent positions
- `max_drawdown = 0.15`: Circuit breaker activates at 15% drawdown

These parameters are dynamically adjusted by the learning system during operation.

## Recent Critical Improvements (2026)

### Week 1 Loss Prevention Fixes

1. **Backwards Logic Bug Fix** (line 1039):
   - **CRITICAL**: Fixed `min()` → `max()` bug that was tightening stops when losing
   - Now correctly WIDENS stops to -0.5% when losing (gives positions room to recover)
   - Expected to reduce stop loss hit rate from 60-80% to 20-30%

2. **Realistic Parameters** (lines 121-123):
   - Increased profit targets from 0.3% to 1.5% (5x increase)
   - Increased stop losses from -0.2% to -0.8% (4x increase)
   - Increased scalp threshold from 0.02% to 0.1% (5x increase)
   - Now realistic for Bitcoin's 0.5-2% hourly volatility

3. **Position Size Caps** (lines 117-118, 789, 2374-2383):
   - Reduced max position from 90% to 60% (prevents overexposure)
   - Reduced min position from 50% to 20% (more flexible)
   - Hard cap at 60% portfolio in any single BTC position
   - Expected to limit max loss per trade to ~1.5%

4. **Circuit Breakers** (lines 1118-1220, 842-875):
   - New `check_risk_limits()` method monitors all risk metrics
   - Halts trading if: drawdown > 15%, 5 consecutive losses, daily loss > 5%
   - Force liquidates positions on max drawdown breach
   - Reduces position sizes by 50% when drawdown > 10%
   - Expected to prevent portfolio wipeout during severe drawdowns

## Development Notes

### When Modifying Trading Logic:
1. Always read the current implementation first - don't assume behavior
2. Be aware of the backwards logic bug that was recently fixed (line 1039)
3. Position sizes are now hard-capped at 60% - respect this limit
4. Circuit breakers will halt trading during severe drawdowns - this is intentional
5. Parameters have been calibrated for Bitcoin's volatility - don't make them tighter without justification

### When Adding New Features:
1. Consider integration with the circuit breaker system (check_risk_limits)
2. Ensure new logic respects the 60% position cap
3. Add appropriate logging for transparency
4. Update learning_data structure if adding new learning mechanisms
5. Test with simulated drawdown scenarios

### Testing Approach:
1. Unit test individual methods in isolation
2. Integration test with simulated price data
3. Backtest on historical Bitcoin data from 2024
4. Monitor these metrics post-deployment:
   - Stop loss hit rate (target: <30%)
   - Max drawdown (target: <15%)
   - Win rate (target: 50-60%)
   - Circuit breaker activations (should see warnings approaching limits)

## Time-Based Behavior

The bot respects market hours and avoids trading during low-liquidity periods (2-6 AM UTC by default in `_validate_entry_conditions()`).

## Logging

Comprehensive logging to both file (trading_bot.log) and console:
- INFO: Status updates, trade executions, performance metrics
- WARNING: Risk limit warnings (approaching 10% drawdown, 3+ consecutive losses)
- ERROR: Circuit breaker activations, critical failures

Circuit breaker activations logged with 🚨 emoji for visibility.
Risk warnings logged with ⚠️  emoji for attention.

Log rotation handled manually via `cleanup_old_data()` every hour.

## Known Limitations

1. **Single asset only**: Bot only trades Bitcoin, no diversification
2. **No slippage modeling**: Assumes perfect execution at fetched prices
3. **No transaction fees**: Simulated trades don't account for exchange fees
4. **Limited learning sample**: Requires 10-20 trades per market condition before learning optimizes
5. **No regime detection**: Doesn't detect when market structure fundamentally changes

## Performance Expectations (Post-Week 1 Fixes)

- **Win rate:** 50-60% (up from 30-40%)
- **Sharpe ratio:** 1.2-1.5 (up from 0.5)
- **Max drawdown:** 10-15% (down from 20-30%)
- **Stop hit rate:** 20-30% (down from 60-80%)
- **Profit factor:** 1.5-2.0 (up from 0.8-1.0)

## Recent Improvements (2026)

### ✅ Week 2: Risk Refinement (COMPLETED)
- Trailing stop implementation in manage_position()
- VaR-based position limits
- Dynamic position sizing based on volatility

### ✅ Week 3: Entry Quality (COMPLETED)
- Momentum-based entry validation
- Signal quality improvements (reduce noise)
- Trend confirmation requirements

### ✅ Week 4: ML Integration (COMPLETED)
- Connect learning systems to trading loop
- Use learned optimal position sizes
- Pattern-based position scaling

### ✅ Week 5: Data Pipeline & Adaptability (Feb 2026)
**Critical Issue Fixed:** Bot had zero trades due to empty price_history. Root cause: No continuous price tracking.

**Fixes Applied:**
1. **Continuous Price Tracking** (`update_price_history()`)
   - Runs every 30 seconds to build price history
   - Required for momentum/volatility calculations
   - Logs warm-up progress

2. **Startup Warm-Up** (in `main()`)
   - Collects 12 prices (24 seconds) before first trade
   - Prevents trading with insufficient data
   - Clear status logging

3. **Enhanced Diagnostics** (`analyze_market_momentum()`)
   - Detailed logging of calculations
   - Error handling with safe fallback
   - Logs price range, raw momentum, direction

4. **Fallback Logic** (in `_get_trade_decision()`)
   - Simple trend detection when momentum = 0
   - Uses 5-price comparison as backup
   - Prevents complete paralysis

5. **Auto-Adjustment** (`check_and_adjust_entry_thresholds()`)
   - Tracks rejection rate (rejections / attempts)
   - Reduces thresholds by 20% if > 90% rejection
   - Logs parameter changes to audit trail

6. **Health Monitoring** (`check_data_pipeline_health()`)
   - Tests momentum/volatility calculations
   - Reports issues every 10 decisions
   - Self-diagnostic capability

**Result:** Bot now trades instead of being paralyzed. Expects 5-10 trades/day with quality entries.

## Future Enhancements

### Potential Improvements:
- Additional technical indicators (RSI, MACD, Bollinger Bands)
- Multi-asset support (Ethereum, other cryptos)
- Backtesting framework with historical data
- Web dashboard for monitoring
- Multi-timeframe analysis
