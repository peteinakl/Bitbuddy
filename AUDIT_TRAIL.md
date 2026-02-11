# Audit Trail Documentation

This document describes all the logs and data files maintained by the Bitcoin Trading Bot for auditing and learning purposes.

## 📁 Audit Files Created

### 1. **trade_history.json** - All Executed Trades
Complete record of every trade executed by the bot.

**Contains:**
- Action (BUY/SELL)
- Amount in BTC
- Price at execution
- Timestamp (ISO format)
- Portfolio value before/after
- P&L in $ and %
- Entry price reference
- Trade reason
- Market condition at time of trade
- Momentum data
- ML learning fields (momentum_threshold, position_size, volatility, entry_pattern, context)

**Example:**
```json
{
  "action": "BUY",
  "amount": 0.00123456,
  "price": 45123.45,
  "timestamp": "2026-02-11T23:45:30.123456",
  "portfolio_value_before": 10000.00,
  "portfolio_value_after": 10123.45,
  "pnl": 123.45,
  "pnl_pct": 0.0123,
  "entry_price": 45000.00,
  "reason": "MOMENTUM_ENTRY_BULLISH_strength_0.000567",
  "market_condition": "BULLISH",
  "momentum_data": {...},
  "momentum_threshold": 0.0005,
  "position_size": 0.45,
  "market_volatility": 0.012,
  "entry_pattern": {...}
}
```

---

### 2. **decision_audit_log.json** - ALL Trading Decisions ⭐ NEW
Complete audit trail of EVERY decision the bot makes, not just executed trades.

**Contains:**
- **TRADE_EXECUTED** - Successful trades
- **TRADE_REJECTED** - Trades blocked by insufficient capital/BTC
- **HOLD** - Decisions to not trade (with reasons like INSUFFICIENT_MOMENTUM_FOR_BUY)
- **CIRCUIT_BREAKER** - Risk limit activations

**Why This Matters:**
- See WHY the bot didn't trade (rejected entries due to momentum, validation, etc.)
- Understand decision patterns during different market conditions
- Audit circuit breaker activations
- Learn what conditions lead to holds vs entries

**Example:**
```json
{
  "timestamp": "2026-02-11T23:45:30",
  "decision_type": "HOLD",
  "action": "HOLD",
  "amount": 0,
  "price": 45123.45,
  "reason": "INSUFFICIENT_MOMENTUM_FOR_BUY",
  "market_condition": "BULLISH",
  "momentum": {
    "strength": 0.0003,
    "direction": "up"
  },
  "portfolio_value": 10523.45,
  "cash": 5234.56,
  "btc_holdings": 0.12345678,
  "volatility": 0.012,
  "current_parameters": {
    "profit_target": 0.015,
    "stop_loss": -0.008,
    "scalp_threshold": 0.001,
    "max_position_size": 0.60
  }
}
```

---

### 3. **parameter_changes.json** - Trading Parameter Evolution ⭐ NEW
Track how the bot adapts its parameters over time based on performance.

**Contains:**
- Parameter name (profit_target, stop_loss, scalp_threshold, etc.)
- Old value
- New value
- Reason for change (win rate, avg profit/loss, volatility)
- Trade count when changed

**Why This Matters:**
- See how the bot learns and adapts
- Understand if parameters are converging or oscillating
- Audit if the bot is adapting correctly to market conditions

**Example:**
```json
{
  "timestamp": "2026-02-11T23:50:00",
  "parameter": "stop_loss",
  "old_value": -0.008,
  "new_value": -0.005,
  "reason": "Win rate: 32.50%, Avg loss: 0.0035",
  "trade_count": 45
}
```

---

### 4. **bot_state.json** - Complete Bot State Snapshot
Full snapshot of the bot's internal state, saved every 3 minutes.

**Contains:**
- All trading parameters
- Position stack (active positions)
- Learning data structure
- Market trends
- Success rates
- Capital and holdings
- Price history

**Why This Matters:**
- Can resume bot from any point
- Audit internal state at any time
- Debug issues by examining state at failure point

---

### 5. **learning_data.json** - Machine Learning Patterns
Stores patterns the bot has learned from successful and failed trades.

**Contains:**
- Successful patterns that led to profits
- Failed patterns that led to losses
- Market condition-specific success rates
- Time-based patterns
- Position size performance
- Holding period analysis

**Why This Matters:**
- See what the bot is learning
- Understand which patterns are working
- Identify if the bot is overfitting

---

### 6. **market_data.csv** - Continuous Market Data
Time-series data of all market observations.

**Contains:**
- Timestamp
- Bitcoin price
- Price change %
- Market condition
- Momentum strength & direction
- Volatility
- Portfolio value
- BTC holdings
- Cash balance

**Why This Matters:**
- Correlate trades with market conditions
- Analyze bot behavior during different market regimes
- Create charts and visualizations
- Backtest improvements

---

### 7. **trading_bot.log** - Detailed Runtime Log
Complete log of all bot activity with timestamps and log levels.

**Contains:**
- INFO: Status updates, trade executions, decisions
- WARNING: Risk warnings, approaching limits
- ERROR: Circuit breaker activations, failures
- DEBUG: Detailed internal operations

**Log Rotation:**
- Cleaned up hourly by cleanup_old_data()
- Old data archived automatically

**Visual Indicators in Logs:**
- 🚨 = Circuit breaker activated
- ⚠️ = Risk warning
- 📈/📉 = Profit/Loss status
- ✅/❌ = Trade profit/loss
- 🧠 = ML learning update
- 📊 = Adaptive threshold update
- 💾 = Data saved

---

### 8. **performance_history.json** - Performance Snapshots
Periodic snapshots of portfolio performance metrics.

**Contains:**
- Sharpe ratio
- Sortino ratio
- Win rate
- Profit factor
- Max drawdown
- Average win/loss
- Risk/reward ratio
- Market correlation

**Why This Matters:**
- Track performance evolution over time
- Identify when performance degrades
- Compare before/after parameter changes

---

### 9. **current_status.txt** - Human-Readable Status
Latest status update saved to file, refreshed every minute.

**Why This Matters:**
- Quick check of current status without parsing logs
- Can be monitored by external tools
- Human-readable portfolio summary

---

## 📊 Learning from the Audit Trail

### What You Can Analyze:

1. **Win Rate by Market Condition:**
   - Filter decision_audit_log.json by market_condition
   - Calculate success rate in BULLISH vs BEARISH vs RANGING
   - Adjust strategy for conditions that work best

2. **Parameter Effectiveness:**
   - Track parameter_changes.json over time
   - See if changes improved win rate
   - Identify optimal parameter ranges

3. **Entry Rejection Analysis:**
   - Count HOLD decisions with INSUFFICIENT_MOMENTUM_FOR_BUY
   - Determine if momentum threshold is too strict
   - See what % of rejected entries would have been profitable

4. **Circuit Breaker Effectiveness:**
   - Track CIRCUIT_BREAKER entries in decision_audit_log.json
   - See if they prevented catastrophic losses
   - Analyze drawdown recovery after halts

5. **Learning System Progress:**
   - Track learning_data.json evolution
   - See if pattern confidence scores improve
   - Identify which patterns have highest success

6. **Volatility Correlation:**
   - Correlate market_data.csv volatility with trade outcomes
   - Identify if volatility-adjusted sizing is working
   - See performance in high vs low volatility

---

## 🔍 Quick Audit Queries

### How many trades were rejected due to momentum?
```bash
grep "INSUFFICIENT_MOMENTUM_FOR_BUY" decision_audit_log.json | wc -l
```

### What's my win rate?
```bash
# Count profitable trades
grep '"pnl":' trade_history.json | grep -v '"-' | wc -l
# Divide by total trades
```

### When did circuit breakers activate?
```bash
grep "CIRCUIT_BREAKER" decision_audit_log.json
```

### How have parameters evolved?
```bash
cat parameter_changes.json | jq '.[] | select(.parameter=="stop_loss")'
```

### What market conditions are most profitable?
```bash
# Analyze trade_history.json grouped by market_condition
```

---

## 📈 Continuous Improvement

The audit trail enables continuous improvement:

1. **Identify Weaknesses:** See what decisions lead to losses
2. **Test Hypotheses:** Check if rejected entries would have been profitable
3. **Optimize Parameters:** Find parameter values that maximize win rate
4. **Refine ML System:** See if learned patterns are actually working
5. **Risk Management:** Verify circuit breakers and limits are effective

All files are saved in JSON format for easy parsing and analysis with Python, jq, or any analysis tool.
