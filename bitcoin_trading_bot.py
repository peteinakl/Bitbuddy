import requests
import time
import json
import logging
import schedule
from datetime import datetime
import os
from typing import Dict, List, Tuple, Optional
import numpy as np
import pandas as pd
from datetime import timedelta

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('trading_bot.log'),
        logging.StreamHandler()
    ]
)

class BitcoinTradingBot:
    def __init__(self, initial_capital: float = None):
        """Initialize the trading bot with optional initial capital"""
        # Initialize price history first
        self.price_history = []
        self.max_price_history = 720  # 1 hour of 5-sec data
        
        # Enhanced API management
        self.api_rotation = {
            'CoinGecko': {
                'calls': [], 
                'errors': 0, 
                'cooldown_until': None,
                'rate_limit': 45,  # Requests per minute
                'cooldown_period': 120  # 2 minutes cooldown
            },
            'Binance': {
                'calls': [], 
                'errors': 0, 
                'cooldown_until': None,
                'rate_limit': 100,
                'cooldown_period': 60
            },
            'Kraken': {
                'calls': [], 
                'errors': 0, 
                'cooldown_until': None,
                'rate_limit': 60,
                'cooldown_period': 60
            },
            'Bitfinex': {
                'calls': [], 
                'errors': 0, 
                'cooldown_until': None,
                'rate_limit': 90,
                'cooldown_period': 60
            },
            'Bitstamp': {
                'calls': [], 
                'errors': 0, 
                'cooldown_until': None,
                'rate_limit': 80,
                'cooldown_period': 60
            }
        }
        self.current_api = 'Binance'  # Start with Binance instead of CoinGecko
        self.min_api_interval = 10  # Increased from 4.5 to 10 seconds
        
        # Enhanced logging setup
        self.setup_logging()
        
        # Initialize core attributes without setting values
        self.initial_capital = None
        self.capital = None
        self.btc_holdings = 0.0
        
        # Load portfolio first (this will set initial_capital and capital)
        self.load_existing_portfolio()
        
        # Only use the passed initial_capital if no existing portfolio was loaded
        if self.initial_capital is None:
            self.initial_capital = initial_capital or 10000.0
            self.capital = self.initial_capital
        
        # Get current portfolio value for logging
        current_price = self.fetch_bitcoin_price()
        if current_price:
            portfolio_value = self.capital + (self.btc_holdings * current_price)
            logging.info(f"Bot initialized with portfolio value: ${portfolio_value:,.2f}")
        else:
            logging.info(f"Bot initialized with portfolio value: ${self.capital:,.2f}")
        
        # Trading parameters - more aggressive
        self.position_stack = []
        self.max_positions = 3
        self.min_position_size = 0.50    # Increased from 0.40
        self.max_position_size = 0.90    # Increased from 0.80
        self.position_step = 0.15        # Larger steps
        self.scalp_threshold = 0.0015    # More sensitive
        self.profit_target = 0.005       # 0.5% target
        self.stop_loss = -0.003         # Tighter stop
        self.trailing_stop = 0.002      # Tighter trailing
        
        # Add trailing stop loss
        self.trailing_active = False
        self.trailing_price = None
        
        # Market data tracking
        self.market_data_file = 'market_data.csv'
        self.ensure_market_data_file()
        
        # Performance tracking
        self.trade_history = []
        self.recent_trades = []
        self.max_trade_history = 10
        self.last_trade_time = None
        
        # Resource management
        self.cleanup_interval = 3600  # Cleanup old data hourly
        self.last_cleanup = time.time()
        self.last_data_refresh = None
        self.data_refresh_interval = 300  # 5 minutes
        
        # Add market learning parameters
        self.market_trends = {
            'up_trends': [],    # Store successful upward trends
            'down_trends': [],  # Store successful downward trends
            'failed_trades': [] # Store failed trades for learning
        }
        
        # Dynamic threshold adjustment
        self.threshold_adjustment = {
            'profit_target': {'base': 0.0015, 'min': 0.0008, 'max': 0.0025},
            'stop_loss': {'base': -0.0010, 'min': -0.0015, 'max': -0.0005},
            'entry_threshold': {'base': 0.0008, 'min': 0.0005, 'max': 0.0012}
        }
        
        # Market momentum tracking
        self.momentum_window = 12  # Track last hour of movements (5 min intervals)
        self.price_momentum = []   # Store price momentum data
        
        # Success rate tracking
        self.trade_success_rate = {
            'total_trades': 0,
            'profitable_trades': 0,
            'success_rate': 0.0
        }
        
        # Add learning parameters
        self.learning_data = {
            'successful_patterns': [],  # Store patterns that led to profits
            'failed_patterns': [],     # Store patterns that led to losses
            'market_conditions': {},   # Track success rate in different conditions
            'time_patterns': {},       # Track success rate at different times
            'position_sizes': {},      # Track performance of different position sizes
            'holding_periods': {}      # Track performance of different holding periods
        }
        
        # Strategy adaptation parameters
        self.strategy_weights = {
            'momentum': 1.0,
            'trend': 1.0,
            'volatility': 1.0,
            'time_of_day': 1.0
        }
        
        # Load historical learning data if exists
        self.load_learning_data()
        
        # Position sizing parameters
        self.min_position_size = 0.40    # Start smaller
        self.max_position_size = 0.80    # Never go all-in
        self.position_step = 0.10        # Smaller steps
        
        # Market condition thresholds - more aggressive
        self.condition_multipliers = {
            'STRONG_BULLISH': 3.0,    # Triple size
            'BULLISH': 2.0,           # Double size
            'VOLATILE_RANGE': 1.5,    # 150% size
            'RANGING': 1.0,           # Full size
            'BEARISH': 0.7,           # 70% size
            'STRONG_BEARISH': 0.5,    # Half size
            'NEUTRAL': 1.0            # Full size
        }
        
        # Momentum thresholds
        self.momentum_thresholds = {
            'strong': 0.0008,
            'medium': 0.0005,
            'weak': 0.0003
        }
        
        # Bull market parameters
        self.bull_market_threshold = 0.02  # 2% uptrend defines bull market
        self.hold_position_size = 0.90     # Hold up to 90% in bull markets
        self.max_drawdown = 0.15           # Allow 15% drawdown in bull markets
        
        # Volatility management
        self.volatility_multiplier = 1.0
        self.max_volatility = 0.02      # 2% max volatility threshold
        self.min_trade_size = 100       # Minimum trade size in USD

    def setup_logging(self):
        """Setup enhanced logging configuration"""
        # Create logs directory if it doesn't exist
        if not os.path.exists('logs'):
            os.makedirs('logs')
        
        # Get current timestamp for log files
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        # Configure different log files for different types of data
        logging.basicConfig(level=logging.INFO)
        
        # Main trading log
        self.trading_logger = logging.getLogger('trading')
        trading_handler = logging.FileHandler(f'logs/trading_{timestamp}.log')
        trading_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        self.trading_logger.addHandler(trading_handler)
        
        # Price data log
        self.price_logger = logging.getLogger('price_data')
        price_handler = logging.FileHandler(f'logs/price_data_{timestamp}.log')
        price_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
        self.price_logger.addHandler(price_handler)
        
        # Analysis log
        self.analysis_logger = logging.getLogger('analysis')
        analysis_handler = logging.FileHandler(f'logs/analysis_{timestamp}.log')
        analysis_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
        self.analysis_logger.addHandler(analysis_handler)
        
        # Performance log
        self.performance_logger = logging.getLogger('performance')
        performance_handler = logging.FileHandler(f'logs/performance_{timestamp}.log')
        performance_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
        self.performance_logger.addHandler(performance_handler)

    def load_existing_portfolio(self):
        """Initialize portfolio maintaining current balance"""
        try:
            # Try to load existing state first
            with open('bot_state.json', 'r') as f:
                state = json.load(f)
                self.initial_capital = state['portfolio']['initial_capital']
                current_capital = state['portfolio']['current_capital']
                btc_holdings = state['portfolio']['btc_holdings']
                
                # Get current price to calculate current portfolio value
                current_price = self.fetch_bitcoin_price()
                if current_price:
                    self.btc_holdings = btc_holdings
                    self.capital = current_capital
                    
                    # Log portfolio restoration
                    portfolio_value = self.capital + (self.btc_holdings * current_price)
                    logging.info(f"Restored existing portfolio:")
                    logging.info(f"BTC Holdings: {self.btc_holdings:.8f} BTC (${self.btc_holdings * current_price:,.2f})")
                    logging.info(f"Cash Balance: ${self.capital:.2f}")
                    logging.info(f"Total Portfolio Value: ${portfolio_value:,.2f}")
                    return
                    
        except (FileNotFoundError, KeyError, json.JSONDecodeError) as e:
            logging.warning(f"Could not load existing portfolio: {str(e)}")
        
        # If no valid state found, then initialize with 10000
        self.initial_capital = 10000.0
        current_price = self.fetch_bitcoin_price()
        
        if not current_price:
            logging.error("Could not fetch price to initialize portfolio")
            return
        
        # Start with 100% BTC position
        self.btc_holdings = self.initial_capital / current_price
        self.capital = 0  # All capital in BTC
        
        # Initialize position stack with the full position
        initial_position = {
            'amount': self.btc_holdings,
            'entry_price': current_price,
            'timestamp': datetime.now(),
            'active': True,
            'type': 'INITIAL'
        }
        
        self.position_stack = [initial_position]
        
        # Initialize trade history
        self.trade_history = [{
            'action': 'INITIAL_POSITION',
            'amount': self.btc_holdings,
            'price': current_price,
            'timestamp': datetime.now().isoformat(),
            'reason': 'PORTFOLIO_INITIALIZATION',
            'active': True
        }]
        
        logging.info(f"Initialized new portfolio with 100% BTC position:")
        logging.info(f"BTC Holdings: {self.btc_holdings:.8f} BTC (${self.initial_capital:,.2f})")
        logging.info(f"Entry Price: ${current_price:,.2f}")
        logging.info(f"Cash Balance: ${self.capital:.2f}")

    def fetch_bitcoin_price(self) -> Optional[float]:
        """Fetch price using multiple APIs with price validation"""
        apis = {
            'Binance': {
                "url": "https://api.binance.com/api/v3/ticker/price",
                "params": {"symbol": "BTCUSDT"},
                "extract": lambda r: float(r["price"]),
                "weight": 1.0  # Primary source
            },
            'Kraken': {
                "url": "https://api.kraken.com/0/public/Ticker",
                "params": {"pair": "XBTUSD"},
                "extract": lambda r: float(r["result"]["XXBTZUSD"]["c"][0]),
                "weight": 0.8  # Secondary source
            },
            'Bitstamp': {
                "url": "https://www.bitstamp.net/api/v2/ticker/btcusd/",
                "params": {},
                "extract": lambda r: float(r["last"]),
                "weight": 0.8  # Secondary source
            }
        }
        
        current_time = datetime.now()
        prices = []
        weights = []
        
        # Try to get prices from all APIs
        for api_name, api in apis.items():
            api_status = self.api_rotation[api_name]
            
            # Skip if in cooldown
            if api_status['cooldown_until'] and current_time < api_status['cooldown_until']:
                continue
            
            try:
                response = requests.get(api["url"], params=api["params"], timeout=5)
                response.raise_for_status()
                price = api["extract"](response.json())
                
                # Basic sanity check for price
                if 10000 <= price <= 200000:  # Reasonable BTC price range
                    prices.append(price)
                    weights.append(api["weight"])
                    
                    # Update API tracking
                    api_status['calls'].append(current_time)
                    api_status['errors'] = 0
                    logging.debug(f"Price from {api_name}: ${price:,.2f}")
                else:
                    logging.warning(f"Suspicious price from {api_name}: ${price:,.2f}")
                    
            except Exception as e:
                logging.warning(f"Failed to fetch from {api_name}: {str(e)}")
                api_status['errors'] += 1
                
                if api_status['errors'] >= 3:
                    api_status['cooldown_until'] = current_time + timedelta(seconds=api_status['cooldown_period'])
        
        if not prices:
            logging.error("Could not fetch valid price from any API")
            return None
        
        if len(prices) == 1:
            final_price = prices[0]
        else:
            # Calculate weighted average, excluding outliers
            mean_price = sum(p * w for p, w in zip(prices, weights)) / sum(weights)
            
            # Filter out prices that deviate more than 0.5% from mean
            valid_prices = [(p, w) for p, w in zip(prices, weights) 
                           if abs(p - mean_price) / mean_price <= 0.005]
            
            if valid_prices:
                final_price = sum(p * w for p, w in valid_prices) / sum(w for _, w in valid_prices)
            else:
                final_price = mean_price
        
        # Update price history
        self.price_history.append((final_price, current_time))
        
        # Log price discrepancy if multiple sources
        if len(prices) > 1:
            max_diff = max(abs(p - final_price) / final_price for p in prices)
            if max_diff > 0.001:  # Log if difference is more than 0.1%
                logging.info(f"Price spread: {max_diff:.3%} across {len(prices)} sources")
        
        return final_price

    def rotate_api(self):
        """Rotate to the next available API"""
        apis = list(self.api_rotation.keys())
        current_index = apis.index(self.current_api)
        next_index = (current_index + 1) % len(apis)
        self.current_api = apis[next_index]
        logging.debug(f"Switched to {self.current_api} API")

    def cleanup_old_data(self):
        """Clean up old data periodically"""
        current_time = time.time()
        if current_time - self.last_cleanup >= self.cleanup_interval:
            # Trim price history
            current = datetime.now()
            self.price_history = [
                x for x in self.price_history 
                if (current - x[1]).total_seconds() <= 3600
            ]
            self.last_cleanup = current_time
            logging.debug("Performed routine data cleanup")

    def calculate_price_change(self) -> float:
        """Calculate price change percentage over lookback period"""
        if len(self.price_history) < 2:
            return 0.0
        
        current_price = self.price_history[-1][0]
        previous_price = self.price_history[-2][0]
        return (current_price - previous_price) / previous_price

    def analyze_market_condition(self) -> str:
        """Enhanced market condition analysis with shorter timeframes"""
        if len(self.price_history) < self.momentum_window:
            return "NEUTRAL"
        
        # Use multiple timeframes for better accuracy
        recent_prices = [price for price, _ in self.price_history[-self.momentum_window:]]
        very_recent = recent_prices[-5:]  # Last 5 periods
        
        # Calculate multiple EMAs
        ema_fast = np.mean(very_recent)
        ema_medium = np.mean(recent_prices[-10:])  # Last 10 periods
        ema_slow = np.mean(recent_prices)
        
        # Calculate momentum
        short_momentum = (very_recent[-1] - very_recent[0]) / very_recent[0]
        medium_momentum = (recent_prices[-1] - recent_prices[-10]) / recent_prices[-10]
        
        # More responsive conditions
        if short_momentum > self.scalp_threshold * 0.5:
            if ema_fast > ema_medium > ema_slow:
                return "STRONG_BULLISH"
            return "BULLISH"
        elif short_momentum < -self.scalp_threshold * 0.5:
            if ema_fast < ema_medium < ema_slow:
                return "STRONG_BEARISH"
            return "BEARISH"
        elif abs(short_momentum) < self.scalp_threshold * 0.2:
            return "RANGING"
        
        return "NEUTRAL"

    def execute_trade(self, action: str, amount: float, price: float):
        """Execute a trade and update portfolio"""
        if action == "BUY":
            cost = amount * price
            if cost <= self.capital:
                self.btc_holdings += amount
                self.capital -= cost
                self.position_stack.append({
                    'amount': amount,
                    'entry_price': price,
                    'timestamp': datetime.now()
                })
                logging.info(f"BUY: {amount:.8f} BTC at ${price:,.2f}")
        
        elif action == "SELL":
            if amount <= self.btc_holdings:
                revenue = amount * price
                self.btc_holdings -= amount
                self.capital += revenue
                logging.info(f"SELL: {amount:.8f} BTC at ${price:,.2f}")

    def log_market_data(self, data: Dict):
        """Log market data to CSV file"""
        df = pd.DataFrame([data])
        df.to_csv(self.market_data_file, mode='a', header=False, index=False)

    def save_trade_history(self):
        """Save trade history to JSON file"""
        with open('trade_history.json', 'w') as f:
            json.dump(self.trade_history, f, indent=4)

    def generate_report(self):
        """Generate performance report"""
        current_price = self.fetch_bitcoin_price()
        if current_price is None:
            return
        
        portfolio_value = self.capital + (self.btc_holdings * current_price)
        roi = ((portfolio_value - self.initial_capital) / self.initial_capital) * 100
        
        report = {
            "timestamp": datetime.now().isoformat(),
            "initial_capital": self.initial_capital,
            "final_portfolio_value": portfolio_value,
            "roi_percentage": roi,
            "total_trades": len(self.trade_history),
            "btc_holdings": self.btc_holdings,
            "cash_balance": self.capital
        }
        
        with open('performance_report.json', 'w') as f:
            json.dump(report, f, indent=4)

    def ensure_market_data_file(self):
        """Create market data file if it doesn't exist"""
        if not os.path.exists(self.market_data_file):
            df = pd.DataFrame(columns=['timestamp', 'price', 'price_change', 'market_condition'])
            df.to_csv(self.market_data_file, index=False)
            logging.info(f"Created new market data file: {self.market_data_file}")

    def display_status(self):
        """Display current bot status with detailed P/L information"""
        current_price = self.fetch_bitcoin_price()
        if current_price is None:
            logging.error("Could not fetch current price for status update")
            return
        
        # Calculate current portfolio value and P/L
        portfolio_value = self.capital + (self.btc_holdings * current_price)
        total_pnl = portfolio_value - self.initial_capital
        pnl_percentage = (total_pnl / self.initial_capital) * 100
        
        # Format status message
        status = f"""
{'='*50}
BITCOIN TRADING BOT - STATUS UPDATE
{'='*50}
Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

CURRENT MARKET
-------------
Bitcoin Price: ${current_price:,.2f}
24h Change: {self.calculate_price_change()*100:.2f}%
Market Condition: {self.analyze_market_condition()}

PORTFOLIO SUMMARY
----------------
Portfolio Value: ${portfolio_value:,.2f}
Total P/L: ${total_pnl:,.2f} ({pnl_percentage:+.2f}%)
Cash Balance: ${self.capital:,.2f}
BTC Holdings: {self.btc_holdings:.8f} BTC (${(self.btc_holdings * current_price):,.2f})

TRADING ACTIVITY
---------------
Active Positions: {len([p for p in self.position_stack if p.get('active', True)])}
Total Trades: {len(self.trade_history)}
"""
        
        # Add position details if any exist
        if self.position_stack:
            status += "\nACTIVE POSITIONS\n----------------\n"
            for i, pos in enumerate(self.position_stack, 1):
                entry_price = pos['entry_price']
                pos_pnl = ((current_price - entry_price) / entry_price) * 100
                status += f"Position {i}: {pos['amount']:.8f} BTC @ ${pos['entry_price']:,.2f} "
                status += f"(P/L: {pos_pnl:+.2f}%)\n"
        
        # Add recent trades if any
        if self.trade_history:
            status += "\nRECENT TRADES\n-------------\n"
            for trade in self.trade_history[-3:]:  # Show last 3 trades
                status += f"{trade['action']}: {trade['amount']:.8f} BTC @ ${trade['price']:,.2f} "
                status += f"({trade['reason']})\n"
        
        # Add VaR information
        var_95 = self.calculate_var(confidence_level=0.95)
        var_99 = self.calculate_var(confidence_level=0.99)
        
        status += f"""
RISK METRICS
-----------
95% Daily VaR: ${var_95:,.2f} ({(var_95/portfolio_value)*100:.2f}% of portfolio)
99% Daily VaR: ${var_99:,.2f} ({(var_99/portfolio_value)*100:.2f}% of portfolio)
"""
        
        status += f"\n{'='*50}\n"
        
        # Log status and save to file
        logging.info(status)
        with open('current_status.txt', 'w') as f:
            f.write(status)

    def calculate_optimal_position_size(self, current_price: float, trend_strength: float) -> float:
        """Calculate position size with aggressive trend following"""
        portfolio_value = self.capital + (self.btc_holdings * current_price)
        
        # Calculate volatility adjustment
        volatility = self.calculate_volatility()
        volatility_factor = max(0.5, min(1.5, self.max_volatility / volatility if volatility > 0 else 1.5))
        
        # Base position size with volatility adjustment
        base_size = portfolio_value * self.min_position_size * volatility_factor
        
        # More aggressive trend multiplier
        trend_multiplier = min(3.0, max(1.0, 1.0 + trend_strength * 100))
        position_size = base_size * trend_multiplier
        
        # Ensure within limits but allow larger positions in strong trends
        max_position = min(
            portfolio_value * self.max_position_size * trend_multiplier,
            portfolio_value * 0.95  # Allow up to 95% in very strong trends
        )
        min_position = max(self.min_trade_size, portfolio_value * 0.2)  # Minimum 20% position
        
        return min(max_position, max(min_position, position_size))

    def calculate_position_fraction(self, market_condition: str, momentum: Dict, current_price: float) -> float:
        """More conservative position sizing"""
        base_fraction = 0.3  # Reduced from 0.5 for more conservative entries
        
        condition_multipliers = {
            'STRONG_BULLISH': 1.2,    # Reduced from 1.5
            'BULLISH': 1.0,           # Reduced from 1.2
            'VOLATILE_RANGE': 0.5,    # Reduced from 0.8
            'RANGING': 0.3,           # Reduced from 0.6
            'BEARISH': 0.0,           # No entries in bearish conditions
            'STRONG_BEARISH': 0.0,    # No entries in strong bearish conditions
            'NEUTRAL': 0.3            # Reduced from 0.5
        }
        
        fraction = base_fraction * condition_multipliers.get(market_condition, 1.0)
        
        # Adjust for momentum strength
        if momentum['strength'] > 0.001:  # Strong momentum
            fraction *= min(2.0, 1 + momentum['strength'] * 100)
        
        # Adjust for volatility
        if momentum['volatility'] > self.scalp_threshold:
            fraction *= max(0.5, 1 - momentum['volatility'] * 10)  # Reduce position in high volatility
        
        # Adjust for recent performance
        if self.trade_success_rate['total_trades'] > 0:
            success_rate = self.trade_success_rate['success_rate']
            fraction *= min(1.5, max(0.5, success_rate * 2))  # Scale based on success
        
        # Apply strategy weights
        weighted_fraction = (
            fraction * 
            self.strategy_weights['momentum'] * (momentum['strength'] / 0.001) +
            self.strategy_weights['trend'] * (1 if market_condition in ['STRONG_BULLISH', 'BULLISH'] else 0.5) +
            self.strategy_weights['volatility'] * (1 - momentum['volatility'] * 5) +
            self.strategy_weights['time_of_day'] * self._get_time_weight()
        ) / sum(self.strategy_weights.values())
        
        return min(1.0, max(0.1, weighted_fraction))  # Keep between 10% and 100%

    def _get_time_weight(self) -> float:
        """Get weight based on time of day performance"""
        current_hour = datetime.now().hour
        if current_hour in self.learning_data['time_patterns']:
            return self.learning_data['time_patterns'][current_hour].get('success_rate', 0.5)
        return 0.5

    def execute_trade_decision(self):
        """Execute trading strategy with alternative analysis"""
        current_price = self.fetch_bitcoin_price()
        if current_price is None:
            return
        
        # Make actual trading decision
        decision = self._get_trade_decision(current_price)
        
        # Need to add position management here
        for position in self.position_stack:
            if position.get('active', True):
                action = self.manage_position(position, current_price, self.analyze_market_condition())
                if action != 'HOLD':
                    self._execute_exit_trade(position, current_price, action, 
                        (current_price - position['entry_price']) / position['entry_price'],
                        position['amount'])
        
        # Log decision analysis
        logging.info(f"""
Trade Decision:
  Action: {decision['action']}
  Amount: {decision.get('amount', 0):.8f} BTC
  Reason: {decision.get('reason', 'N/A')}
  Market Condition: {self.analyze_market_condition()}
  Current Price: ${current_price:,.2f}
""")
        
        # Execute actual trade
        if decision['action'] == 'BUY':
            self.execute_trade('BUY', decision['amount'], current_price)
            # Add to position stack
            self.position_stack.append({
                'amount': decision['amount'],
                'entry_price': current_price,
                'timestamp': datetime.now(),
                'active': True,
                'reason': decision['reason']
            })
        elif decision['action'] == 'SELL':
            self.execute_trade('SELL', decision['amount'], current_price)
        
        # Simulate alternatives for learning
        alternatives = self.simulate_alternative_strategy(current_price, decision['action'])
        self.learn_from_alternatives()

    def _get_trade_decision(self, current_price: float) -> Dict:
        """Get trading decision with enhanced trend following"""
        market_condition = self.analyze_market_condition()
        momentum = self.analyze_market_momentum()
        
        # Calculate position size
        position_size = self.calculate_optimal_position_size(current_price, momentum['strength'])
        entry_fraction = self.calculate_position_fraction(market_condition, momentum, current_price)
        
        # Entry conditions
        if len(self.position_stack) < self.max_positions:
            # Buy in strong trends or accumulate in ranging markets
            if ((market_condition in ['STRONG_BULLISH', 'BULLISH'] and momentum['direction'] == 'up') or
                (market_condition == 'RANGING' and momentum['strength'] > self.scalp_threshold * 0.5)):
                
                amount = (position_size * entry_fraction) / current_price
                if amount * current_price >= 10:  # Minimum $10 trade
                    return {
                        'action': 'BUY',
                        'amount': amount,
                        'reason': f"{market_condition}_MOMENTUM"
                    }
        
        # Exit conditions
        if self.btc_holdings > 0:
            # Sell in bearish conditions or take profits in volatility
            if ((market_condition in ['STRONG_BEARISH', 'BEARISH'] and momentum['direction'] == 'down') or
                (market_condition == 'VOLATILE_RANGE' and momentum['strength'] > self.scalp_threshold)):
                
                amount = self.btc_holdings * entry_fraction
                return {
                    'action': 'SELL',
                    'amount': amount,
                    'reason': f"{market_condition}_MOMENTUM"
                }
        
        return {
            'action': 'HOLD',
            'amount': 0,
            'reason': 'NO_SIGNAL'
        }

    def _execute_exit_trade(self, position, current_price, reason, profit_pct, exit_amount):
        """Execute partial or full position exits with proper tracking"""
        self.execute_trade("SELL", exit_amount, current_price)
        
        # Update position
        position['amount'] -= exit_amount
        if position['amount'] <= 0.00001:  # If essentially zero
            position['active'] = False
            position['exit_price'] = current_price
            position['exit_time'] = datetime.now()
            position['final_pnl'] = profit_pct
        
        trade_result = {
            'action': 'SELL',
            'reason': reason,
            'amount': exit_amount,
            'price': current_price,
            'profit_pct': profit_pct,
            'timestamp': datetime.now().isoformat(),
            'position_id': position.get('id', 'unknown')
        }
        
        self.trade_history.append(trade_result)
        self.update_trade_metrics(trade_result)

    def analyze_market_momentum(self) -> Dict:
        """Analyze market momentum and patterns"""
        if len(self.price_history) < 2:
            return {'strength': 0, 'direction': 'neutral', 'volatility': 0}
        
        recent_prices = [price for price, _ in self.price_history[-self.momentum_window:]]
        
        if not recent_prices:
            return {'strength': 0, 'direction': 'neutral', 'volatility': 0}
        
        # Calculate momentum indicators
        price_changes = np.diff(recent_prices) / recent_prices[:-1]
        momentum = sum(price_changes)
        volatility = np.std(price_changes)
        
        # Determine momentum strength and direction
        strength = abs(momentum)
        direction = 'up' if momentum > 0 else 'down' if momentum < 0 else 'neutral'
        
        momentum_data = {
            'strength': strength,
            'direction': direction,
            'volatility': volatility
        }
        
        # Log detailed momentum analysis
        self.analysis_logger.info(
            f"\nMomentum Analysis:"
            f"\n  Strength: {momentum_data['strength']:.6f}"
            f"\n  Direction: {momentum_data['direction']}"
            f"\n  Volatility: {momentum_data['volatility']:.6f}"
        )
        
        return momentum_data

    def adjust_trading_parameters(self):
        """Dynamically adjust trading parameters based on performance"""
        if self.trade_success_rate['total_trades'] < 10:
            return  # Need minimum trades for adjustment
        
        success_rate = self.trade_success_rate['success_rate']
        
        # Adjust thresholds based on success rate
        if success_rate > 0.6:  # If winning more than 60%
            # Be more aggressive
            self.profit_target = min(
                self.threshold_adjustment['profit_target']['max'],
                self.profit_target * 1.1
            )
            self.stop_loss = max(
                self.threshold_adjustment['stop_loss']['min'],
                self.stop_loss * 1.1
            )
        elif success_rate < 0.4:  # If winning less than 40%
            # Be more conservative
            self.profit_target = max(
                self.threshold_adjustment['profit_target']['min'],
                self.profit_target * 0.9
            )
            self.stop_loss = min(
                self.threshold_adjustment['stop_loss']['max'],
                self.stop_loss * 0.9
            )

    def update_trade_metrics(self, trade_result: Dict):
        """Update metrics with enhanced performance logging"""
        self.trade_success_rate['total_trades'] += 1
        
        if trade_result['profit_pct'] > 0:
            self.trade_success_rate['profitable_trades'] += 1
        
        self.trade_success_rate['success_rate'] = (
            self.trade_success_rate['profitable_trades'] / 
            self.trade_success_rate['total_trades']
        )
        
        # Log detailed performance metrics
        self.performance_logger.info(
            f"\nTrade Performance Update:"
            f"\n  Total Trades: {self.trade_success_rate['total_trades']}"
            f"\n  Profitable Trades: {self.trade_success_rate['profitable_trades']}"
            f"\n  Success Rate: {self.trade_success_rate['success_rate']:.2%}"
            f"\n  Last Trade P/L: {trade_result['profit_pct']:.2%}"
        )

    def check_rate_limit(self) -> bool:
        """Check if we're within API rate limits"""
        current_time = datetime.now()
        
        # Clean up old API calls
        self.api_calls = [
            call_time for call_time in self.api_calls
            if (current_time - call_time).total_seconds() <= 60
        ]
        
        # Check if we're within rate limit
        if len(self.api_calls) >= self.max_calls_per_minute:
            logging.warning(f"Rate limit reached: {len(self.api_calls)} calls in last minute")
            return False
        
        # Check minimum interval between calls
        if self.last_api_call and (time.time() - self.last_api_call) < self.min_api_interval:
            logging.debug("Minimum API interval not reached")
            return False
        
        return True

    def manage_position(self, position: Dict, current_price: float, market_condition: str):
        """Enhanced position management with trend following"""
        entry_price = position['entry_price']
        current_pnl = (current_price - entry_price) / entry_price
        
        # Dynamic thresholds based on market condition
        if market_condition in ['STRONG_BULLISH', 'BULLISH']:
            # Let profits run in strong trends
            adjusted_profit_target = self.profit_target * 2
            adjusted_stop_loss = self.stop_loss * 1.5  # Wider stops
        else:
            # Take profits quicker in other conditions
            adjusted_profit_target = self.profit_target
            adjusted_stop_loss = self.stop_loss
        
        # Trailing stop management
        if current_pnl > adjusted_profit_target * 0.5:
            if not self.trailing_active:
                self.trailing_active = True
                self.trailing_price = current_price
            elif current_price > self.trailing_price:
                self.trailing_price = current_price
                # Tighten stop as profits increase
                self.trailing_stop = max(0.002, self.trailing_stop * (1 - current_pnl))
            elif current_price < self.trailing_price * (1 - self.trailing_stop):
                return 'TRAILING_STOP'
        
        # Exit conditions
        if current_pnl >= adjusted_profit_target:
            return 'TAKE_PROFIT'
        elif current_pnl <= adjusted_stop_loss:
            return 'STOP_LOSS'
        
        return 'HOLD'

    def update_learning_data(self, trade_result: Dict):
        """Update learning data after each trade"""
        # Extract pattern from recent price history
        recent_prices = self.price_history[-self.momentum_window:]
        pattern = self.extract_pattern(recent_prices)
        
        # Get current market context
        market_context = {
            'market_condition': self.analyze_market_condition(),
            'momentum': self.analyze_market_momentum(),
            'time_of_day': datetime.now().hour,
            'position_size': trade_result['amount'],
            'holding_period': (datetime.fromisoformat(trade_result['timestamp']) - 
                             datetime.fromisoformat(trade_result.get('entry_timestamp', trade_result['timestamp']))).seconds / 3600
        }
        
        # Store trade result with context
        if trade_result['profit_pct'] > 0:
            self.learning_data['successful_patterns'].append({
                'pattern': pattern,
                'context': market_context,
                'profit': trade_result['profit_pct']
            })
            
            # Update success rates
            self._update_success_rate('market_conditions', market_context['market_condition'], True)
            self._update_success_rate('time_patterns', market_context['time_of_day'], True)
            
        else:
            self.learning_data['failed_patterns'].append({
                'pattern': pattern,
                'context': market_context,
                'loss': trade_result['profit_pct']
            })
            
            # Update failure rates
            self._update_success_rate('market_conditions', market_context['market_condition'], False)
            self._update_success_rate('time_patterns', market_context['time_of_day'], False)
        
        # Adjust strategy weights based on performance
        self.adjust_strategy_weights()
        
        # Save learning data
        self.save_learning_data()

    def extract_pattern(self, price_data: List[Tuple[float, datetime]]) -> Dict:
        """Extract tradeable patterns from price data"""
        prices = [price for price, _ in price_data]
        
        return {
            'price_changes': np.diff(prices) / prices[:-1],
            'volatility': np.std(prices),
            'trend': (prices[-1] - prices[0]) / prices[0],
            'pattern_length': len(prices)
        }

    def adjust_strategy_weights(self):
        """Adjust strategy weights based on performance"""
        # Calculate success rates for different components
        momentum_success = self._calculate_component_success('momentum')
        trend_success = self._calculate_component_success('trend')
        volatility_success = self._calculate_component_success('volatility')
        time_success = self._calculate_component_success('time_of_day')
        
        # Update weights using exponential moving average
        alpha = 0.1  # Learning rate
        self.strategy_weights['momentum'] = (1 - alpha) * self.strategy_weights['momentum'] + alpha * momentum_success
        self.strategy_weights['trend'] = (1 - alpha) * self.strategy_weights['trend'] + alpha * trend_success
        self.strategy_weights['volatility'] = (1 - alpha) * self.strategy_weights['volatility'] + alpha * volatility_success
        self.strategy_weights['time_of_day'] = (1 - alpha) * self.strategy_weights['time_of_day'] + alpha * time_success

    def save_learning_data(self):
        """Save learning data to file"""
        with open('learning_data.json', 'w') as f:
            json.dump(self.learning_data, f, indent=4)

    def load_learning_data(self):
        """Load learning data from file"""
        try:
            with open('learning_data.json', 'r') as f:
                self.learning_data = json.load(f)
        except FileNotFoundError:
            logging.info("No existing learning data found")

    def _calculate_component_success(self, component: str) -> float:
        """Calculate success rate for a specific strategy component"""
        successful_trades = self.learning_data['successful_patterns']
        failed_trades = self.learning_data['failed_patterns']
        
        if not successful_trades and not failed_trades:
            return 0.5  # Default weight if no trade history
        
        # Calculate success metrics based on component
        if component == 'momentum':
            # Success rate when momentum direction matched trade direction
            success_count = sum(1 for trade in successful_trades 
                              if trade['context']['momentum']['direction'] == 
                              ('up' if trade['profit'] > 0 else 'down'))
            total_trades = len(successful_trades) + len(failed_trades)
            return success_count / total_trades if total_trades > 0 else 0.5
        
        elif component == 'trend':
            # Success rate for trades aligned with market condition
            success_count = sum(1 for trade in successful_trades 
                              if trade['context']['market_condition'] in 
                              ['STRONG_BULLISH', 'BULLISH'])
            total_trades = len(successful_trades) + len(failed_trades)
            return success_count / total_trades if total_trades > 0 else 0.5
        
        elif component == 'volatility':
            # Success rate in different volatility conditions
            avg_success_volatility = np.mean([trade['context']['momentum']['volatility'] 
                                            for trade in successful_trades]) if successful_trades else 0
            avg_failed_volatility = np.mean([trade['context']['momentum']['volatility'] 
                                           for trade in failed_trades]) if failed_trades else 0
            return 1 - (avg_failed_volatility / (avg_success_volatility + avg_failed_volatility)) if (avg_success_volatility + avg_failed_volatility) > 0 else 0.5
        
        elif component == 'time_of_day':
            # Success rate for current hour
            current_hour = datetime.now().hour
            hour_trades = [trade for trade in successful_trades + failed_trades 
                          if trade['context']['time_of_day'] == current_hour]
            if not hour_trades:
                return 0.5
            success_count = sum(1 for trade in hour_trades 
                              if trade in successful_trades)
            return success_count / len(hour_trades)
        
        return 0.5  # Default fallback

    def save_bot_state(self):
        """Save complete bot state with all performance data"""
        try:
            state = {
                'timestamp': datetime.now().isoformat(),
                'portfolio': {
                    'initial_capital': self.initial_capital,
                    'current_capital': self.capital,
                    'btc_holdings': self.btc_holdings,
                    'position_stack': [
                        {**pos, 'timestamp': pos['timestamp'].isoformat()} 
                        for pos in self.position_stack
                    ]
                },
                'performance': {
                    'trade_history': self.trade_history,
                    'trade_success_rate': self.trade_success_rate,
                    'market_trends': self.market_trends,
                    'learning_data': self.learning_data
                },
                'trading_parameters': {
                    'profit_target': float(self.profit_target),
                    'stop_loss': float(self.stop_loss),
                    'scalp_threshold': float(self.scalp_threshold),
                    'trailing_stop': float(self.trailing_stop),
                    'strategy_weights': {k: float(v) for k, v in self.strategy_weights.items()}
                }
            }
            
            # Save to both JSON files for redundancy
            with open('bot_state.json', 'w') as f:
                json.dump(state, f, indent=4)
            with open('learning_data.json', 'w') as f:
                json.dump(state['performance']['learning_data'], f, indent=4)
            
            logging.info("Bot state and learning data saved successfully")
            
        except Exception as e:
            logging.error(f"Error saving bot state: {str(e)}")

    def load_bot_state(self):
        """Load complete bot state with all performance data"""
        try:
            # Try loading from bot_state.json first
            try:
                with open('bot_state.json', 'r') as f:
                    state = json.load(f)
                    
                # Convert timestamps back to datetime
                for position in state['portfolio']['position_stack']:
                    position['timestamp'] = datetime.fromisoformat(position['timestamp'])
                    
                # Restore portfolio state
                self.initial_capital = state['portfolio']['initial_capital']
                self.capital = state['portfolio']['current_capital']
                self.btc_holdings = state['portfolio']['btc_holdings']
                self.position_stack = state['portfolio']['position_stack']
                
                # Restore performance metrics
                self.trade_history = state['performance']['trade_history']
                self.trade_success_rate = state['performance']['trade_success_rate']
                self.market_trends = state['performance']['market_trends']
                self.learning_data = state['performance']['learning_data']
                
                # Restore trading parameters
                self.profit_target = float(state['trading_parameters']['profit_target'])
                self.stop_loss = float(state['trading_parameters']['stop_loss'])
                self.scalp_threshold = float(state['trading_parameters']['scalp_threshold'])
                self.trailing_stop = float(state['trading_parameters']['trailing_stop'])
                self.strategy_weights = {k: float(v) for k, v in state['trading_parameters']['strategy_weights'].items()}
                
                logging.info("Bot state loaded successfully")
                return
                
            except FileNotFoundError:
                # Try loading just learning data as fallback
                with open('learning_data.json', 'r') as f:
                    self.learning_data = json.load(f)
                    logging.info("Loaded learning data from backup file")
                    return
                    
        except Exception as e:
            logging.error(f"Error loading bot state: {str(e)}")
        
        # If all loading attempts fail, initialize fresh
        logging.info("No existing state found, initializing fresh data")
        self._initialize_fresh_state()

    def _initialize_fresh_state(self):
        """Initialize fresh learning data with baseline values"""
        self.learning_data = {
            'successful_patterns': [],
            'failed_patterns': [],
            'market_conditions': {
                condition: {'total_trades': 0, 'successful_trades': 0, 'success_rate': 0.5}
                for condition in ['STRONG_BULLISH', 'BULLISH', 'NEUTRAL', 'BEARISH', 
                                'STRONG_BEARISH', 'VOLATILE_RANGE', 'RANGING']
            },
            'time_patterns': {
                str(hour): {'total_trades': 0, 'successful_trades': 0, 'success_rate': 0.5}
                for hour in range(24)
            }
        }

    def calculate_volatility(self) -> float:
        """Calculate price volatility over recent history"""
        if len(self.price_history) < 2:
            return 0.0
        
        # Get recent prices
        recent_prices = [price for price, _ in self.price_history[-self.momentum_window:]]
        
        if len(recent_prices) < 2:
            return 0.0
        
        # Calculate returns
        returns = np.diff(recent_prices) / recent_prices[:-1]
        
        # Calculate volatility (standard deviation of returns)
        volatility = np.std(returns)
        
        # Log volatility calculation
        self.analysis_logger.debug(
            f"Volatility calculation:"
            f"\n  Window size: {len(recent_prices)}"
            f"\n  Volatility: {volatility:.6f}"
        )
        
        return volatility

    def _update_success_rate(self, category: str, key: str, success: bool):
        """Update success rates for different trading categories"""
        if category not in self.learning_data:
            self.learning_data[category] = {}
        
        if key not in self.learning_data[category]:
            self.learning_data[category][key] = {
                'total_trades': 0,
                'successful_trades': 0,
                'success_rate': 0.0,
                'last_updated': datetime.now().isoformat()
            }
        
        stats = self.learning_data[category][key]
        stats['total_trades'] += 1
        if success:
            stats['successful_trades'] += 1
        
        stats['success_rate'] = stats['successful_trades'] / stats['total_trades']
        stats['last_updated'] = datetime.now().isoformat()
        
        # Log the update
        logging.debug(
            f"Updated {category} success rate for {key}:"
            f"\n  Total Trades: {stats['total_trades']}"
            f"\n  Success Rate: {stats['success_rate']:.2%}"
        )

    def calculate_var(self, confidence_level: float = 0.95, time_horizon: int = 1) -> float:
        """
        Calculate Value at Risk using historical method
        confidence_level: typically 0.95 or 0.99
        time_horizon: number of days to calculate VaR for
        """
        if len(self.price_history) < 100:  # Need sufficient historical data
            return 0.0
        
        # Calculate daily returns
        prices = [price for price, _ in self.price_history]
        returns = np.diff(prices) / prices[:-1]
        
        # Sort returns from worst to best
        sorted_returns = np.sort(returns)
        
        # Find the return at the confidence level
        index = int((1 - confidence_level) * len(sorted_returns))
        var_return = sorted_returns[index]
        
        # Calculate current portfolio value
        current_price = self.fetch_bitcoin_price()
        if not current_price:
            return 0.0
        
        portfolio_value = self.capital + (self.btc_holdings * current_price)
        
        # Calculate VaR in dollar terms
        var_dollar = portfolio_value * abs(var_return) * np.sqrt(time_horizon)
        
        logging.info(f"""
Value at Risk Analysis:
  Confidence Level: {confidence_level*100}%
  Time Horizon: {time_horizon} day(s)
  Portfolio Value: ${portfolio_value:,.2f}
  VaR: ${var_dollar:,.2f}
  VaR %: {(var_dollar/portfolio_value)*100:.2f}%
""")
        
        return var_dollar

    def is_bull_market(self) -> bool:
        """Detect bull market conditions"""
        if len(self.price_history) < 100:  # Need sufficient history
            return False
        
        prices = [price for price, _ in self.price_history[-100:]]
        trend = (prices[-1] - prices[0]) / prices[0]
        
        # Check multiple timeframes
        short_trend = (prices[-1] - prices[-20]) / prices[-20]  # 20-period
        medium_trend = (prices[-1] - prices[-50]) / prices[-50]  # 50-period
        
        # Bull market conditions:
        # 1. Overall uptrend > threshold
        # 2. Short and medium trends positive
        # 3. Short trend > medium trend (acceleration)
        return (trend > self.bull_market_threshold and 
                short_trend > 0 and medium_trend > 0 and 
                short_trend > medium_trend)

    def simulate_alternative_strategy(self, current_price: float, actual_decision: str) -> Dict:
        """Simulate alternative trading decisions to learn from missed opportunities"""
        alternatives = {
            'HOLD': {'action': 'HOLD', 'profit': 0, 'reason': 'baseline'},
            'BUY': {'action': 'BUY', 'profit': 0, 'reason': 'alternative'},
            'SELL': {'action': 'SELL', 'profit': 0, 'reason': 'alternative'}
        }
        
        # Get next 10 price points for simulation
        future_prices = [price for price, _ in self.price_history[-10:]]
        if len(future_prices) < 10:
            return {}
        
        # Simulate each alternative
        for action in alternatives.keys():
            if action == actual_decision:
                continue
            
            sim_profit = self._simulate_trade(action, current_price, future_prices)
            alternatives[action]['profit'] = sim_profit
            
            # Log if we missed a better opportunity
            if sim_profit > 0 and sim_profit > self.profit_target:
                logging.info(f"""
Missed Opportunity Analysis:
  Actual Decision: {actual_decision}
  Better Alternative: {action}
  Potential Profit: {sim_profit:.2%}
  Market Condition: {self.analyze_market_condition()}
  Volatility: {self.calculate_volatility():.4f}
""")
                
                # Store learning data
                self.learning_data['missed_opportunities'].append({
                    'timestamp': datetime.now().isoformat(),
                    'actual_decision': actual_decision,
                    'better_alternative': action,
                    'potential_profit': sim_profit,
                    'market_context': {
                        'condition': self.analyze_market_condition(),
                        'volatility': self.calculate_volatility(),
                        'price': current_price
                    }
                })
        
        return alternatives

    def _simulate_trade(self, action: str, entry_price: float, future_prices: List[float]) -> float:
        """Simulate a trade with future price data"""
        if action == 'HOLD':
            return 0
        
        # Simulate BUY
        if action == 'BUY':
            # Find best exit in future prices
            max_price = max(future_prices)
            return (max_price - entry_price) / entry_price
        
        # Simulate SELL
        if action == 'SELL':
            # Find lowest price (best avoided loss)
            min_price = min(future_prices)
            return (entry_price - min_price) / entry_price
        
        return 0

    def learn_from_alternatives(self):
        """Analyze missed opportunities to adjust strategy"""
        if not self.learning_data.get('missed_opportunities'):
            return
        
        # Analyze recent missed opportunities
        recent_misses = [
            miss for miss in self.learning_data['missed_opportunities']
            if (datetime.now() - datetime.fromisoformat(miss['timestamp'])).days < 7
        ]
        
        if not recent_misses:
            return
        
        # Calculate success rates for different conditions
        condition_stats = {}
        for miss in recent_misses:
            condition = miss['market_context']['condition']
            if condition not in condition_stats:
                condition_stats[condition] = {
                    'count': 0,
                    'avg_profit': 0,
                    'better_actions': {'BUY': 0, 'SELL': 0, 'HOLD': 0}
                }
            
            stats = condition_stats[condition]
            stats['count'] += 1
            stats['avg_profit'] = (stats['avg_profit'] * (stats['count'] - 1) + 
                                 miss['potential_profit']) / stats['count']
            stats['better_actions'][miss['better_alternative']] += 1
        
        # Adjust strategy based on findings
        for condition, stats in condition_stats.items():
            if stats['count'] >= 5:  # Need minimum samples
                # If we consistently miss opportunities in certain conditions
                if stats['avg_profit'] > self.profit_target:
                    # Adjust condition multipliers
                    if 'BUY' in stats['better_actions'] and stats['better_actions']['BUY'] > stats['count'] * 0.6:
                        self.condition_multipliers[condition] *= 1.1  # More aggressive
                    elif 'SELL' in stats['better_actions'] and stats['better_actions']['SELL'] > stats['count'] * 0.6:
                        self.condition_multipliers[condition] *= 0.9  # More conservative
        
        logging.info(f"""
Strategy Adjustment Based on Missed Opportunities:
  Total Missed Opportunities: {len(recent_misses)}
  Condition Stats: {json.dumps(condition_stats, indent=2)}
  Updated Multipliers: {json.dumps(self.condition_multipliers, indent=2)}
""")

def main():
    """Main bot loop with enhanced monitoring"""
    bot = BitcoinTradingBot()
    
    logging.info("\n" + "="*50)
    logging.info("Trading Bot Started")
    logging.info(f"Portfolio Value: ${bot.capital + (bot.btc_holdings * bot.fetch_bitcoin_price()):,.2f}")
    logging.info("="*50 + "\n")
    
    # More frequent state saving
    schedule.every(1).minutes.do(bot.display_status)
    schedule.every(2).minutes.do(bot.execute_trade_decision)
    schedule.every(3).minutes.do(bot.save_bot_state)  # Save state every 3 minutes
    schedule.every(1).hours.do(bot.cleanup_old_data)
    
    # Initial actions
    bot.display_status()  # Show initial status
    bot.execute_trade_decision()  # Check for initial trades
    bot.save_bot_state()  # Save initial state
    
    try:
        while True:
            try:
                schedule.run_pending()
                time.sleep(1)
            except Exception as e:
                logging.error(f"Error in main loop: {str(e)}")
                time.sleep(30)
    except KeyboardInterrupt:
        logging.info("\nBot shutdown initiated by user")
        bot.save_bot_state()  # Save state before shutdown
        bot.display_status()
        bot.generate_report()
    finally:
        bot.save_trade_history()
        logging.info("Bot shutdown complete")

if __name__ == "__main__":
    main() 