from pathlib import Path

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
MODELS_DIR = BASE_DIR / "models"
LOGS_DIR = BASE_DIR / "logs"

# Risk defaults
DAILY_MAX_LOSS_PCT = 4.5
TOTAL_MAX_LOSS_PCT = 10.0
RISK_PER_TRADE_PCT = 2.0
MIN_RR = 2.0

# MTF thresholds
MTF_PAPER_THRESHOLD = 75
# Default MTF threshold used at runtime (percentage)
MTF_THRESHOLD = 50.0
MTF_PAPER_THRESHOLD = 75
# Backwards-compatible alias
MTF_REAL_THRESHOLD = MTF_PAPER_THRESHOLD

# Backtest policy
BACKTEST_YEARS = 7

# Auto execution
# When True, the bot will execute orders by default
AUTO_EXECUTION = True

# Default instruments
INSTRUMENTS = ["EURUSD", "XAUUSD", "BTCUSD"]

# Per-instrument daily quota overrides. These are defaults and can be
# overridden by environment variables (e.g. PER_SYMBOL_DAILY_QUOTA).
PER_INSTRUMENT_DAILY_QUOTA_OVERRIDES = {
    "EURUSD": 100,
    "XAUUSD": 80,
    "BTCUSD": 80,
}

# Demo mode
DEMO_MODE = False

# Default scaling factor for SL/TP distances
SCALE_SL_TP_DEFAULT = 0.75

# Default lot sizes and per-instrument config
DEFAULT_LOT = 0.01
INSTRUMENT_CONFIG = {
    "EURUSD": {"lot": 0.01, "quota": 100},
    "XAUUSD": {"lot": 0.03, "quota": 80},
    "BTCUSD": {"lot": 0.01, "quota": 80},
}

# DataFirm parameters (weights)
DATAFIRM_MTF_WEIGHT = 0.4
DATAFIRM_BASE_WEIGHT = 0.6

# Persistence
TRADES_CSV = BASE_DIR / "data" / "paper_trades.csv"
