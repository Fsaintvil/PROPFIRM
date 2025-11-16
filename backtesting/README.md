# Backtesting Module

This directory contains backtesting scripts for the PROPFIRM trading system.

## Scripts

### ftmo_backtest.py

FTMO-compliant backtesting script with strict risk management rules.

**FTMO Rules Enforced:**
- Daily drawdown limit: 4.5%
- Global drawdown limit: 10%
- Minimum Risk/Reward ratio: 1:2
- Full trade logging and compliance tracking

**Usage:**

From the project root directory:

```bash
# On Windows:
python backtesting\ftmo_backtest.py

# On Linux/Mac:
python backtesting/ftmo_backtest.py
```

**Output:**
- Console: Summary of backtest results
- File: `artifacts/ftmo_backtest_report.json` - Detailed JSON report

**Features:**
- Automatic data loading or synthetic data generation
- ML model integration (if available)
- Fallback to simple MA crossover strategy
- FTMO rule violation detection
- Position sizing based on risk percentage
- Stop loss and take profit management
- Comprehensive trade logging

**Report Contents:**
- Backtest metadata
- FTMO rule compliance status
- Capital and P&L metrics
- Trade statistics (win rate, profit factor, etc.)
- Individual trade details
- Violation logs (if any)
