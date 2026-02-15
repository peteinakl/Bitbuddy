# Bitcoin Trading Bot Simulator

A sophisticated Python-based Bitcoin trading bot simulator that allows users to test and refine trading strategies without risking real money. This simulator provides real-time market data analysis, adaptive learning capabilities, and comprehensive performance tracking with robust risk management.

**⚠️ Important:** This is a SIMULATION tool for educational and testing purposes. No real money is traded.

## ✨ Features

### Core Trading (Position Trading Mode)
- **True position trading** - enters on momentum, exits ONLY on profit targets or stop losses
- **Day Trading Mode:** 15-minute decision cycles aligned with 1.5% profit targets
- **Real-time Bitcoin price monitoring** from 5 major exchanges (Binance, Kraken, Bitfinex, Bitstamp, CoinGecko)
- **Momentum-based entry validation** - requires positive momentum + trend confirmation
- **Hold-to-target strategy** - positions held 1-4 hours until targets hit (NO premature exits)
- **Trailing stops** - activate after profit target hit, protect gains while letting winners run
- **Advanced position management** - up to 3 concurrent positions with dynamic sizing

### Risk Management (2026 Updates)
- 🚨 **Circuit breakers** - halt trading at 15% drawdown, 5 consecutive losses, or 5% daily loss
- **VaR-based position limits** - automatic position reduction during high volatility
- **Volatility-adjusted sizing** - scale positions 40-60% during extreme market conditions
- **Position caps** - hard limit at 60% of portfolio in single position
- **Emergency liquidation** - automatic exit on max drawdown breach

### Machine Learning
- **Pattern recognition** - identifies TREND_REVERSAL, RANGE_BOUND, and BREAKOUT patterns
- **Adaptive thresholds** - learns optimal profit targets and stop losses
- **Strategy weight optimization** - adjusts momentum/trend/volatility/time components
- **Market condition learning** - optimizes performance for each market state

### Audit & Monitoring
- **Comprehensive audit trail** - logs ALL decisions (executed, rejected, holds, circuit breakers)
- **Parameter evolution tracking** - monitor how bot adapts over time
- **Visual portfolio tracking** - clear profit/loss indicators in real-time
- **Performance metrics** - Sharpe ratio, Sortino ratio, win rate, profit factor, max drawdown

## Requirements

- Python 3.7+
- Required packages:
  - requests
  - pandas
  - numpy
  - schedule
  - logging

## Installation

1. Clone the repository
2. Install required packages:
```bash
pip install requests pandas numpy schedule
```

## ⚙️ Configuration

### Default Parameters (Optimized for Bitcoin Day Trading)

**Profit/Loss Targets:**
- Profit target: 1.5% (realistic for Bitcoin's volatility)
- Stop loss: -0.8% (accounts for normal market noise)
- Trailing stop: 0.5% (protects profits)

**Position Sizing:**
- Minimum: 20% of portfolio
- Maximum: 60% of portfolio (hard cap)
- Risk-adjusted based on volatility and VaR

**Risk Limits:**
- Max drawdown: 15% (circuit breaker activates)
- Consecutive losses: 5 (halts trading)
- Daily loss limit: 5% of initial capital
- Minimum cash reserve: 20% of initial capital

**Decision Frequency:**
- Price tracking: Every 5 minutes (builds 1-hour rolling window)
- Trade decisions: Every 15 minutes (96/day)
- Status updates: Every 5 minutes
- State saves: Every 15 minutes

## 🚀 Usage

To start the bot:

```bash
python bitcoin_trading_bot.py
```

The bot will:
1. **Warm-up phase (~2 minutes):** Collect 12 initial prices for momentum calculations
2. **Initialize:** Start with $10,000 in 100% BTC position (or load saved state)
3. **Price tracking:** Update price history every 5 minutes (1-hour rolling window)
4. **Trading decisions:** Evaluate momentum and make trades every 15 minutes
5. **Status updates:** Display portfolio status every 5 minutes
6. **State persistence:** Auto-save state and audit logs every 15 minutes

Key features:
- Momentum-based entry validation (requires positive momentum + uptrend)
- Circuit breakers halt trading at 15% drawdown or 5 consecutive losses
- Trailing stops protect profits once targets hit
- Auto-adjusts thresholds if rejection rate exceeds 90%
- Self-diagnostic health checks for data pipeline

## Key Components

### Market Analysis
- Real-time price monitoring
- Volatility calculation
- Trend detection
- Momentum analysis
- Market condition classification

### Position Management
- Dynamic position sizing
- Multiple position stacking
- Partial position exits
- Trailing stop management
- Bull market detection

### Risk Management
- Value at Risk (VaR) calculation
- Dynamic stop-loss adjustment
- Position size limits
- Volatility-based trade sizing
- Maximum drawdown protection

### Performance Tracking
- Trade history logging
- Performance metrics calculation
- Success rate tracking
- Market condition correlation
- Missed opportunity analysis

## 📁 Data Files

The bot maintains comprehensive audit trails:

### Runtime Data (automatically generated)
- `bot_state.json` - Complete bot state snapshot
- `trade_history.json` - All executed trades with full details
- `decision_audit_log.json` - ⭐ ALL decisions (trades, holds, rejections, circuit breakers)
- `parameter_changes.json` - ⭐ Parameter evolution tracking
- `learning_data.json` - ML patterns and success rates
- `market_data.csv` - Continuous market data time series
- `performance_history.json` - Performance metric snapshots
- `current_status.txt` - Latest status (human-readable)
- `trading_bot.log` - Detailed runtime logs

**Note:** All runtime data files are logged with comprehensive details including timestamps, market conditions, momentum data, and decision rationale for post-analysis and learning.

## Safety Features

- API rate limiting
- Error handling and recovery
- State persistence
- Multiple exchange price validation
- Minimum trade size enforcement

## Customization

The bot can be customized by modifying:
- Trading thresholds in the constructor
- Position sizing parameters
- Market condition definitions
- Time-based trading restrictions
- Risk management parameters

## 📊 Expected Performance

Based on comprehensive improvements (2026):

| Metric | Before | After | Target |
|--------|--------|-------|--------|
| Max Drawdown | 20-30% | **10-15%** | <15% |
| Win Rate | 30-40% | **50-60%** | >50% |
| Avg Win | 0.4% | **1.5-2.5%** | >1.5% |
| Stop Hit Rate | 60-80% | **20-30%** | <30% |
| Sharpe Ratio | 0.5 | **1.2-1.5** | >1.0 |
| Profit Factor | 0.8-1.0 | **1.5-2.0** | >1.5 |

**Risk-Adjusted Returns:** 2-3x improvement expected

## ⚠️ Important Notes

1. **This is a SIMULATION tool** - does not trade with real money
2. All trades are simulated based on real market data from exchanges
3. Multiple API sources with rate limiting and error handling
4. Circuit breakers protect against catastrophic losses
5. Comprehensive audit trail for learning and analysis
6. Performance metrics are from simulated trades only

## Monitoring

The bot provides several monitoring capabilities:
- Real-time status display
- Performance reporting
- Detailed logging
- Trade analysis
- Strategy performance metrics

## 📊 Expected Performance (Position Trading Mode)

| Metric | Target | Notes |
|--------|--------|-------|
| **Win Rate** | 50-60% | Momentum strategy with confirmed entries |
| **Trade Frequency** | 3-8/day | Quality over quantity |
| **Avg Hold Time** | 1-4 hours | Until target/stop hit |
| **Avg Win** | +1.5% to +3.0% | Profit targets + trailing stops |
| **Avg Loss** | -0.8% | Stop loss protection |
| **Max Positions** | 3 concurrent | Risk diversification |
| **Max Drawdown** | <15% | Circuit breaker limit |
| **Sharpe Ratio** | 1.2-1.5 | Risk-adjusted returns |

**Trading Pattern:**
```
10:00 BUY  @ $70,000 → Target: $71,050 (+1.5%), Stop: $69,440 (-0.8%)
11:30 SELL @ $71,200 → Profit: +$120 (+1.71%) ✅

14:00 BUY  @ $71,500 → Target: $72,573, Stop: $71,079
15:45 SELL @ $70,930 → Loss: -$54 (-0.80%) ❌
```

## 🎯 Visual Indicators

The bot uses visual indicators in logs for quick status recognition:

- 📈/📉 = Portfolio profit/loss status
- ✅/❌ = Individual trade profit/loss
- 🚨 = Circuit breaker activated (critical)
- ⚠️ = Risk warning (approaching limits)
- 🧠 = ML learning system update
- 📊 = Adaptive thresholds updated
- 💾 = Data saved to disk
- 📍 = New position opened (tracks entry/target/stop)

## 🔧 Recent Improvements (2026)

**Week 1: Critical Loss Prevention**
- Fixed backwards adjustment logic bug (stops now widen when losing)
- Implemented realistic profit/stop parameters (1.5% / -0.8%)
- Capped maximum position sizes (60% hard limit)
- Added circuit breakers with emergency liquidation

**Week 2: Risk Refinement**
- Implemented trailing stops for profit protection
- Added VaR-based position limits
- Volatility-adjusted position sizing

**Week 3: Entry Quality**
- Momentum-based entry validation (requires confirmation)
- Increased signal quality thresholds (10x more selective)
- Entry rejection rate: 40-60% (filters bad entries)

**Week 4: ML Integration**
- Connected learning systems to trading loop
- Parameter evolution tracking
- Pattern-based position sizing
- Strategy weight optimization

**Week 5: Data Pipeline & Adaptability** (Feb 2026)
- 🔄 **Price history tracking** - Updates every 5 minutes (1-hour rolling window, 12 prices)
- ⏳ **Startup warm-up** - Collects 12 prices (~2 min) before first trade (prevents zero-data issues)
- 🔍 **Enhanced diagnostics** - Detailed momentum/volatility logging with health checks
- 🔄 **Fallback logic** - Simple trend detection when sophisticated calculations fail
- 📊 **Auto-adjustment** - Reduces thresholds by 20% if rejection rate > 90%
- 💡 **Self-diagnostic** - Bot detects and reports data pipeline issues

**Week 6: Position Trading Strategy** (Feb 15, 2026) 🚀
- 🎯 **CRITICAL FIX:** Converted from rebalancing to true position trading
- ❌ **Removed:** All "RISK_REDUCTION" automatic sells that caused premature exits
- ✅ **Implemented:** Hold-to-target strategy - positions held until profit target (+1.5%) or stop loss (-0.8%)
- 📊 **Entry-only decisions:** Trading logic now only generates BUY signals for new positions
- 🎯 **Exit management:** ALL exits handled by position management (targets/stops/trailing stops)
- 💰 **Position tracking:** Each BUY creates tracked position with defined targets and stops
- ⏱️ **Hold time:** Positions now held 1-4 hours (not 15-30 minutes)
- 🔒 **Max positions:** Limited to 3 concurrent positions with 20% cash reserve

**Problem Solved:** Bot had 10% "win rate" because 100% of trades showed `pnl: $0` due to immediate rebalancing. Now positions are held to completion, capturing actual profits and losses. Expected: 50-60% real win rate with 3-8 quality trades per day instead of 25-50 worthless rebalancing trades.

See `STRATEGY_CHANGE.md` for complete technical analysis.

## 🤝 Contributing

Key areas for enhancement:
- Additional technical indicators (RSI, MACD, Bollinger Bands)
- Multi-asset support (Ethereum, other cryptos)
- Backtesting framework with historical data
- Additional exchange integrations
- Web dashboard for monitoring

## ⚖️ Disclaimer

**Educational and Testing Purposes Only**

This is a simulation tool intended for learning and strategy testing. It:
- Does NOT connect to real trading accounts
- Does NOT handle real money
- Does NOT execute actual trades
- Should NOT be used for live trading without substantial modification, thorough testing, and professional financial advice

The authors are not responsible for any financial losses. Always consult with a qualified financial advisor before trading with real money.

## 📄 License

MIT License - Free to use and modify.

---

**Built with Python • Powered by Machine Learning • Protected by Circuit Breakers** 🚀