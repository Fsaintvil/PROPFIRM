"""
FTMO Backtest Script
====================
Backtesting script with FTMO-specific risk management rules:
- Daily drawdown max: 4.5%
- Global drawdown max: 10%
- Risk/Reward minimum: 1:2
- Full trade logging

This script can be run from the project root with:
    python backtesting\\ftmo_backtest.py
"""
import json
import sys
from pathlib import Path
from datetime import datetime
import pandas as pd
import numpy as np

# Ensure repo root is in path
root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

# FTMO Risk Management Constants
DAILY_DRAWDOWN_LIMIT = 0.045  # 4.5%
GLOBAL_DRAWDOWN_LIMIT = 0.10  # 10%
MIN_RISK_REWARD_RATIO = 2.0  # 1:2 minimum
INITIAL_CAPITAL = 10000.0
RISK_PER_TRADE = 0.01  # 1% risk per trade
HOLD_BARS = 5
THRESHOLD = 0.50  # Confidence threshold for entry

# File paths
DATA_FILE = Path('data') / 'features_sample.csv'
REPORT_FILE = Path('artifacts') / 'ftmo_backtest_report.json'


def check_ftmo_rules(capital, initial_capital, daily_pnl, daily_start_capital):
    """
    Check if FTMO risk management rules are violated.
    
    Args:
        capital: Current account capital
        initial_capital: Starting capital
        daily_pnl: Current day's P&L
        daily_start_capital: Capital at start of day
        
    Returns:
        tuple: (is_valid, message)
    """
    # Check global drawdown
    global_dd = (initial_capital - capital) / initial_capital
    if global_dd > GLOBAL_DRAWDOWN_LIMIT:
        return False, f"Global drawdown limit exceeded: {global_dd:.2%} > {GLOBAL_DRAWDOWN_LIMIT:.2%}"
    
    # Check daily drawdown
    daily_dd = (daily_start_capital - capital) / daily_start_capital
    if daily_dd > DAILY_DRAWDOWN_LIMIT:
        return False, f"Daily drawdown limit exceeded: {daily_dd:.2%} > {DAILY_DRAWDOWN_LIMIT:.2%}"
    
    return True, "OK"


def load_data():
    """Load and prepare data for backtesting."""
    print(f'Loading data from {DATA_FILE}...')
    
    if not DATA_FILE.exists():
        print(f"Warning: {DATA_FILE} not found. Creating synthetic data for demonstration.")
        # Create synthetic data with trending patterns
        np.random.seed(42)
        idx = pd.date_range("2025-01-01", periods=1000, freq="1h")
        df = pd.DataFrame(index=idx)
        
        # Generate price with trend and noise
        trend = np.linspace(0, 20, len(idx))
        noise = np.cumsum(np.random.randn(len(idx)) * 0.5)
        df["close"] = 100.0 + trend + noise
        
        df["high"] = df["close"] + np.abs(np.random.randn(len(idx)) * 0.3)
        df["low"] = df["close"] - np.abs(np.random.randn(len(idx)) * 0.3)
        df["volume"] = np.random.randint(100, 1000, size=len(idx))
        
        # Calculate indicators
        df["sma_1T"] = df["close"].rolling(5).mean()
        df["ema_15T"] = df["close"].ewm(span=10, adjust=False).mean()
        
        # RSI calculation
        delta = df["close"].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / (loss + 1e-9)
        df["rsi_60T"] = 100 - (100 / (1 + rs))
        
        df = df.dropna()
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(DATA_FILE)
        print(f"Synthetic data created and saved to {DATA_FILE}")
        return df
    
    df = pd.read_csv(DATA_FILE)
    if 'Unnamed: 0' in df.columns:
        df = df.set_index('Unnamed: 0')
        df.index = pd.to_datetime(df.index)
    
    return df


def load_model(skip_model=False):
    """Load trading model if available."""
    if skip_model:
        print("Skipping model loading (using simple MA/RSI strategy)")
        return None
        
    try:
        from meta_learning_system import MetaLearningTradingSystem
        
        meta = MetaLearningTradingSystem(max_models=3)
        
        # Check if model was auto-loaded
        if hasattr(meta, 'model_ensemble') and meta.model_ensemble:
            print(f'Model auto-loaded: {len(meta.model_ensemble)} ensemble member(s)')
            return meta
        
        # Try to load pre-trained model
        if hasattr(meta, 'model_ensemble') and not meta.model_ensemble:
            art = Path('artifacts') / 'auto_improve'
            candidate = None
            
            if (art / 'best_lightgbm_large.txt').exists():
                candidate = art / 'best_lightgbm_large.txt'
            elif (art / 'best_lightgbm.txt').exists():
                candidate = art / 'best_lightgbm.txt'
            
            if candidate:
                try:
                    import lightgbm as lgb
                    booster = lgb.Booster(model_file=str(candidate))
                    meta.model_ensemble = [{'model': booster, 'performance': 1.0, 'architecture': 'lightgbm_booster'}]
                    print(f'Loaded model: {candidate}')
                except Exception as e:
                    print(f'Failed to load model: {e}')
        
        return meta
    except Exception as e:
        print(f"Could not load MetaLearningTradingSystem: {e}")
        return None


def generate_signal(model, row, numeric_df, index):
    """Generate trading signal from model prediction."""
    if model is None:
        # Simple MA crossover strategy with RSI as fallback
        if 'sma_1T' in row.columns and 'ema_15T' in row.columns and 'rsi_60T' in row.columns:
            sma = float(row['sma_1T'].iloc[0])
            ema = float(row['ema_15T'].iloc[0])
            close = float(row['close'].iloc[0])
            rsi = float(row['rsi_60T'].iloc[0])
            
            # Buy signal: price above EMA, EMA > SMA (uptrend), and RSI not overbought
            if close > ema and ema > sma and 30 < rsi < 70:
                return 0.65  # Above threshold
            # Secondary signal: strong momentum with RSI
            elif close > sma and 40 < rsi < 60:
                return 0.55  # Above threshold
        return 0.40  # Below threshold
    
    # Use model prediction
    preferred = ['close', 'volume', 'sma_1T', 'ema_15T', 'rsi_60T']
    vals = []
    
    try:
        if hasattr(model, 'model_ensemble') and model.model_ensemble:
            ensemble_model = model.model_ensemble[0]['model']
            fn = ensemble_model.feature_name() or []
            n_feat = len(fn) if fn else 5
        else:
            n_feat = 5
    except Exception:
        n_feat = 5
    
    for j in range(n_feat):
        if j < len(preferred) and preferred[j] in row.columns:
            v = float(row[preferred[j]].iloc[0])
        elif j < len(row.columns):
            v = float(row.iloc[0, j])
        else:
            v = 0.0
        vals.append(v)
    
    X = pd.DataFrame([vals], columns=[f'Column_{k}' for k in range(len(vals))])
    
    try:
        if hasattr(model, 'ensemble_predict'):
            pred = float(model.ensemble_predict(X)[0])
        else:
            pred = 0.5
    except Exception:
        pred = 0.5
    
    return pred


def run_backtest(threshold=THRESHOLD, skip_model=False, verbose=False):
    """Run FTMO-compliant backtest."""
    print("\n" + "="*60)
    print("FTMO BACKTEST - Starting...")
    print("="*60)
    print(f"Initial Capital: ${INITIAL_CAPITAL:,.2f}")
    print(f"Risk per Trade: {RISK_PER_TRADE:.1%}")
    print(f"Daily Drawdown Limit: {DAILY_DRAWDOWN_LIMIT:.1%}")
    print(f"Global Drawdown Limit: {GLOBAL_DRAWDOWN_LIMIT:.1%}")
    print(f"Min Risk/Reward Ratio: 1:{MIN_RISK_REWARD_RATIO:.0f}")
    print(f"Confidence Threshold: {threshold:.2f}")
    print("="*60 + "\n")
    
    # Load data and model
    df = load_data()
    model = load_model(skip_model=skip_model)
    
    # Prepare numeric dataframe
    numeric_df = df.select_dtypes(include=[np.number]).ffill().fillna(0)
    print(f"Data loaded: {len(numeric_df)} bars")
    
    # Initialize tracking variables
    capital = INITIAL_CAPITAL
    initial_capital = INITIAL_CAPITAL
    peak_capital = INITIAL_CAPITAL
    trades = []
    daily_start_capital = INITIAL_CAPITAL
    current_date = None
    violations = []
    
    # Run backtest
    for i in range(len(numeric_df) - HOLD_BARS - 1):
        row = numeric_df.iloc[i:i+1]
        
        # Check for new trading day
        if hasattr(numeric_df.index, 'date'):
            trade_date = numeric_df.index[i].date()
            if current_date != trade_date:
                current_date = trade_date
                daily_start_capital = capital
        
        # Check FTMO rules before trading
        is_valid, msg = check_ftmo_rules(capital, initial_capital, 0, daily_start_capital)
        if not is_valid:
            violations.append({
                'index': i,
                'timestamp': str(numeric_df.index[i]) if hasattr(numeric_df.index, 'date') else i,
                'message': msg,
                'capital': capital
            })
            print(f"⚠️  FTMO Rule Violation at bar {i}: {msg}")
            break
        
        # Generate trading signal
        pred = generate_signal(model, row, numeric_df, i)
        
        if pred <= threshold:
            continue
        
        # Calculate position size based on risk
        entry_price = numeric_df['close'].iloc[i+1]
        
        # Estimate ATR for stop loss calculation
        window = 14
        if i >= window:
            vol = numeric_df['close'].pct_change().iloc[i-window+1:i+1].std()
            atr = vol * entry_price if not np.isnan(vol) else entry_price * 0.02
        else:
            atr = entry_price * 0.02
        
        # Set stop loss and take profit with minimum risk/reward ratio
        sl = entry_price - (2 * atr)
        tp = entry_price + (MIN_RISK_REWARD_RATIO * 2 * atr)
        
        # Position sizing: risk per trade
        risk_amount = capital * RISK_PER_TRADE
        risk_per_unit = entry_price - sl
        
        if risk_per_unit <= 0:
            continue
        
        units = risk_amount / risk_per_unit
        
        # Simulate trade exit after HOLD_BARS
        exit_price = numeric_df['close'].iloc[i+1+HOLD_BARS]
        
        # Apply stop loss and take profit
        if exit_price <= sl:
            exit_price = sl
        elif exit_price >= tp:
            exit_price = tp
        
        # Calculate P&L
        pnl = (exit_price - entry_price) * units
        capital += pnl
        
        # Update peak
        if capital > peak_capital:
            peak_capital = capital
        
        # Record trade
        trade = {
            'index': i,
            'timestamp': str(numeric_df.index[i]) if hasattr(numeric_df.index, 'date') else i,
            'entry_price': float(entry_price),
            'exit_price': float(exit_price),
            'sl': float(sl),
            'tp': float(tp),
            'units': float(units),
            'pnl': float(pnl),
            'capital': float(capital),
            'prediction': float(pred),
            'risk_reward': float((tp - entry_price) / (entry_price - sl))
        }
        trades.append(trade)
    
    # Calculate metrics
    final_capital = capital
    net_pnl = final_capital - initial_capital
    total_trades = len(trades)
    profitable_trades = sum(1 for t in trades if t['pnl'] > 0)
    losing_trades = sum(1 for t in trades if t['pnl'] < 0)
    
    if total_trades > 0:
        win_rate = profitable_trades / total_trades
        avg_win = np.mean([t['pnl'] for t in trades if t['pnl'] > 0]) if profitable_trades > 0 else 0
        avg_loss = np.mean([abs(t['pnl']) for t in trades if t['pnl'] < 0]) if losing_trades > 0 else 0
    else:
        win_rate = 0
        avg_win = 0
        avg_loss = 0
    
    # Calculate drawdown
    max_drawdown = (peak_capital - final_capital) / peak_capital if peak_capital > 0 else 0
    
    # Generate report
    report = {
        'backtest_info': {
            'script': 'backtesting/ftmo_backtest.py',
            'timestamp': datetime.now().isoformat(),
            'data_file': str(DATA_FILE),
            'bars_analyzed': len(numeric_df)
        },
        'ftmo_rules': {
            'daily_drawdown_limit': DAILY_DRAWDOWN_LIMIT,
            'global_drawdown_limit': GLOBAL_DRAWDOWN_LIMIT,
            'min_risk_reward_ratio': MIN_RISK_REWARD_RATIO,
            'violations': violations
        },
        'capital': {
            'initial': initial_capital,
            'final': final_capital,
            'net_pnl': net_pnl,
            'return_pct': (net_pnl / initial_capital) * 100,
            'peak_capital': peak_capital,
            'max_drawdown_pct': max_drawdown * 100
        },
        'trades': {
            'total': total_trades,
            'profitable': profitable_trades,
            'losing': losing_trades,
            'win_rate': win_rate * 100,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'profit_factor': (avg_win * profitable_trades) / (avg_loss * losing_trades) if losing_trades > 0 else 0
        },
        'sample_trades': trades[:10],
        'all_trades': trades
    }
    
    # Save report
    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_FILE, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, default=float)
    
    # Print summary
    print("\n" + "="*60)
    print("FTMO BACKTEST - Results")
    print("="*60)
    print(f"Initial Capital:     ${initial_capital:,.2f}")
    print(f"Final Capital:       ${final_capital:,.2f}")
    print(f"Net P&L:             ${net_pnl:,.2f} ({(net_pnl/initial_capital)*100:.2f}%)")
    print(f"Peak Capital:        ${peak_capital:,.2f}")
    print(f"Max Drawdown:        {max_drawdown*100:.2f}%")
    print("-"*60)
    print(f"Total Trades:        {total_trades}")
    print(f"Profitable:          {profitable_trades} ({win_rate*100:.1f}%)")
    print(f"Losing:              {losing_trades}")
    print(f"Avg Win:             ${avg_win:.2f}")
    print(f"Avg Loss:            ${avg_loss:.2f}")
    print("-"*60)
    print(f"FTMO Rules:")
    print(f"  Daily DD Limit:    {DAILY_DRAWDOWN_LIMIT*100:.1f}%")
    print(f"  Global DD Limit:   {GLOBAL_DRAWDOWN_LIMIT*100:.1f}%")
    print(f"  Violations:        {len(violations)}")
    print("="*60)
    print(f"\nReport saved to: {REPORT_FILE}")
    
    return report


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="FTMO Backtest Script")
    parser.add_argument("--threshold", type=float, default=THRESHOLD, 
                       help=f"Confidence threshold for entry (default: {THRESHOLD})")
    parser.add_argument("--no-model", action="store_true",
                       help="Skip model loading and use simple MA/RSI strategy")
    parser.add_argument("--verbose", action="store_true",
                       help="Print verbose debug information")
    
    args = parser.parse_args()
    
    try:
        report = run_backtest(
            threshold=args.threshold,
            skip_model=args.no_model,
            verbose=args.verbose
        )
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Error running backtest: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
