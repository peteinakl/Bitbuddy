# BitBuddy - Adaptive Bitcoin Trading Bot

## Overview
BitBuddy is an intelligent, self-learning Bitcoin trading bot that implements multiple trading strategies while continuously adapting to market conditions. The bot uses real-time price data from multiple exchanges, employs dynamic position sizing, and features a comprehensive learning system to improve its trading decisions over time.

## Core Features

### 1. Multi-Exchange Price Aggregation
- Primary data from Binance, with fallbacks to Kraken and Bitstamp
- Weighted price averaging to ensure accuracy
- Automatic API rotation to prevent rate limiting
- Price validation and outlier detection

### 2. Dynamic Trading Strategy
The bot employs a multi-faceted approach to trading decisions:

#### Market Analysis
- Multiple timeframe analysis (short, medium, long-term)
- Momentum tracking
- Volatility measurement
- Trend strength calculation
- Market condition classification (STRONG_BULLISH to STRONG_BEARISH)

#### Position Management
- Dynamic position sizing (25% to 90% of portfolio)
- Fractional position entries and exits
- Minimum position size of $2,500 or 25% of portfolio
- Maximum position size scaled by trend strength
- Up to 3 concurrent positions

#### Risk Management
- Dynamic stop-loss levels
- Trailing stops for profit protection
- Profit target scaling
- Volatility-based position sizing
- Market condition-based risk adjustment

### 3. Self-Learning System

#### Performance Tracking
- Success rate by market condition
- Time-of-day performance
- Position size effectiveness
- Holding period analysis
- Pattern recognition

#### Strategy Adaptation
- Dynamic weight adjustment for different components:
  - Momentum
  - Trend
  - Volatility
  - Time-of-day
- Success rate-based parameter adjustment
- Learning rate: 10% per trade (alpha = 0.1)

#### Pattern Recognition
- Price movement patterns
- Volume patterns
- Volatility patterns
- Successful trade contexts
- Failed trade analysis

### 4. State Management
- Persistent storage of bot state
- Learning data preservation
- Portfolio tracking
- Performance metrics
- Trading parameters

## Code Structure

### Main Components

1. **BitcoinTradingBot Class**
   - Core trading logic and portfolio management
   - API integration and price fetching
   - Position sizing and risk management
   - Learning system implementation

2. **Price Fetching**
