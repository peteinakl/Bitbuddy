# Advanced Bitcoin Trading Bot

## Overview

This is a sophisticated automated trading bot for Bitcoin that implements multiple trading strategies with dynamic position sizing, risk management, and machine learning capabilities. The bot uses a multi-API approach for reliable price data and implements various safety mechanisms to protect against market volatility and API failures.

## Key Features

### Real-Time Market Analysis
- Multi-API price fetching with automatic rotation (Binance, Kraken, Bitstamp)
- Price validation and cross-reference checking
- Real-time market condition analysis (Strong Bullish to Strong Bearish)
- Momentum and volatility calculations
- Pattern recognition and market trend analysis

### Adaptive Trading Strategy
- Dynamic position sizing based on market conditions
- Scaled entry and exit positions
- Multiple concurrent position management
- Trailing stop-loss implementation
- Progressive profit taking based on market conditions

### Risk Management
- Dynamic stop-loss adjustments
- Position size limits based on portfolio value
- Maximum position counts
- Volatility-based position sizing
- API rate limiting and error handling

### Machine Learning Components
- Pattern recognition and storage
- Success rate tracking by market condition
- Time-of-day performance analysis
- Strategy weight adaptation
- Performance-based parameter adjustment

## Trading Strategy

### Market Analysis
The bot analyzes market conditions using multiple indicators:
1. Price momentum and direction
2. Market volatility
3. Trend strength and direction
4. Time-based patterns

### Position Management
- Maximum of 3 concurrent positions
- Position sizes range from 35% to 50% of available capital
- Scaled entry and exit based on market conditions
- Trailing stops for winning positions

### Entry Criteria
- Strong bullish or bullish market conditions
- Positive momentum indicators
- Volatility within acceptable ranges
- Historical success rate consideration

### Exit Criteria
1. Profit Targets:
   - Full exit at 2x target
   - Partial exits based on market conditions
   - Trailing stop activation at 50% of target

2. Stop Losses:
   - Dynamic stop-loss levels
   - Preventive stops in highly bearish conditions
   - Trailing stops for winning positions

## Learning Mechanism

### Data Collection
- Stores successful and failed trade patterns
- Tracks market conditions during trades
- Monitors time-of-day performance
- Records position sizing outcomes

### Parameter Adaptation
The bot continuously adjusts its parameters based on performance:
- Trading thresholds
- Position sizes
- Entry/exit timing
- Strategy weights

### Performance Metrics
- Trade success rate tracking
- Pattern success analysis
- Market condition performance
- Time-based performance

## Technical Implementation

### Data Management
- Regular state saving
- Redundant data storage
- Periodic cleanup of old data
- Performance logging and analysis

### Safety Features
- API rotation and rate limiting
- Price validation across multiple sources
- Error handling and recovery
- State persistence and recovery

### Monitoring
- Real-time status updates
- Performance reporting
- Trade logging
- Market condition monitoring

## Configuration

### Initial Settings
- Default initial capital: $10,000
- Position size range: 35-50% of capital
- Maximum positions: 3
- Minimum position size: $2,500 or 25% of portfolio

### Trading Parameters
- Scalp threshold: 0.05%
- Initial profit target: 0.2%
- Initial stop loss: -0.15%
- Trailing stop: 0.08%

## Requirements
- Python 3.7+
- Required packages:
  - requests
  - numpy
  - pandas
  - schedule
  - logging

## Usage

```python
from bitcoin_trading_bot import BitcoinTradingBot

# Initialize with custom capital (optional)
bot = BitcoinTradingBot(initial_capital=10000)

# Start trading
bot.main()
```

## Warning
This bot deals with real money and cryptocurrency trading. Use at your own risk and thoroughly test with paper trading before deploying with real capital. Cryptocurrency markets are highly volatile and can result in significant losses.