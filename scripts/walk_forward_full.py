"""
Walk-Forward Validation Complet — 21 symboles actifs, 5 folds temporels.
Valide l'edge MOM20x3 hors échantillon pour chaque symbole.

Usage:
    python scripts/walk_forward_full.py
    python scripts/walk_forward_full.py --folds 5 --timeframe H1
"""

import os
import sys
import json
from collections import defaultdict
from pathlib import Path
from math import sqrt
from time import time as now

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine_simple.indicators import atr, adx

# ============================================================================
# CONFIGURATION
# ============================================================================
INITIAL_BALANCE = 200_000.0
RISK_PER_TRADE = 0.003
MIN_BARS = 200
TIMEOUT_BARS = 120

# 21 symboles actifs — configs calibrées depuis backtest + live
SYMBOL_CONFIG = {
    "BTCUSD": {
        "sl_atr_trending": 3.0, "tp_atr_trending": 8.0,
        "sl_atr_ranging": 2.5, "tp_atr_ranging": 5.0,
        "threshold_trending": 2.0, "threshold_ranging": 1.5,
        "adx_slope_threshold": -6.0, "adx_slope_threshold_strong": -10.0,
        "pullback_band_trending": 0.8, "pullback_band_ranging": 0.5,
        "max_lot": 2.0, "risk_mult": 1.0, "first_lock_atr": 1.5,
        "trailing": {
            "TREND_UP": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30)],
            "TREND_DOWN": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30)],
            "RANGING": [(1.80, 0.80), (2.50, 0.55), (3.50, 0.40), (5.00, 0.25)],
            "HIGH_VOL": [(1.80, 1.20), (2.50, 0.90), (3.50, 0.65), (5.00, 0.40)],
            "LOW_VOL": [(1.80, 0.70), (2.50, 0.50), (3.50, 0.35), (5.00, 0.20)],
        },
        "be_buffer": 0.70,
    },
    "SOLUSD": {
        "sl_atr_trending": 2.5, "tp_atr_trending": 6.0,
        "sl_atr_ranging": 2.0, "tp_atr_ranging": 4.0,
        "threshold_trending": 2.0, "threshold_ranging": 1.5,
        "adx_slope_threshold": -6.0, "adx_slope_threshold_strong": -10.0,
        "pullback_band_trending": 0.8, "pullback_band_ranging": 0.5,
        "max_lot": 2.0, "risk_mult": 1.0, "first_lock_atr": 1.2,
        "trailing": {
            "TREND_UP": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30)],
            "TREND_DOWN": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30)],
            "RANGING": [(1.80, 0.80), (2.50, 0.55), (3.50, 0.40), (5.00, 0.25)],
            "HIGH_VOL": [(1.80, 1.20), (2.50, 0.90), (3.50, 0.65), (5.00, 0.40)],
            "LOW_VOL": [(1.80, 0.70), (2.50, 0.50), (3.50, 0.35), (5.00, 0.20)],
        },
        "be_buffer": 0.65,
    },
    "XAUUSD": {
        "sl_atr_trending": 2.0, "tp_atr_trending": 6.0,
        "sl_atr_ranging": 1.5, "tp_atr_ranging": 4.0,
        "threshold_trending": 2.5, "threshold_ranging": 2.0,
        "adx_slope_threshold": -18.0, "adx_slope_threshold_strong": -24.0,
        "pullback_band_trending": 0.8, "pullback_band_ranging": 0.5,
        "max_lot": 2.0, "risk_mult": 1.0, "first_lock_atr": 0.8,
        "trailing": {
            "TREND_UP": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30)],
            "TREND_DOWN": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30)],
            "RANGING": [(1.80, 0.80), (2.50, 0.55), (3.50, 0.40), (5.00, 0.25)],
            "HIGH_VOL": [(1.80, 1.20), (2.50, 0.90), (3.50, 0.65), (5.00, 0.40)],
            "LOW_VOL": [(1.80, 0.70), (2.50, 0.50), (3.50, 0.35), (5.00, 0.20)],
        },
        "be_buffer": 0.55,
    },
    "EURUSD": {
        "sl_atr_trending": 3.0, "tp_atr_trending": 7.5,
        "sl_atr_ranging": 2.25, "tp_atr_ranging": 6.0,
        "threshold_trending": 2.5, "threshold_ranging": 2.0,
        "adx_slope_threshold": -14.0, "adx_slope_threshold_strong": -18.0,
        "pullback_band_trending": 0.5, "pullback_band_ranging": 0.3,
        "max_lot": 1.0, "risk_mult": 1.0, "first_lock_atr": 0.8,
        "trailing": {
            "TREND_UP": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30)],
            "TREND_DOWN": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30)],
            "RANGING": [(1.80, 0.80), (2.50, 0.55), (3.50, 0.40), (5.00, 0.25)],
            "HIGH_VOL": [(1.80, 1.20), (2.50, 0.90), (3.50, 0.65), (5.00, 0.40)],
            "LOW_VOL": [(1.80, 0.70), (2.50, 0.50), (3.50, 0.35), (5.00, 0.20)],
        },
        "be_buffer": 0.60,
    },
    "GBPUSD": {
        "sl_atr_trending": 3.0, "tp_atr_trending": 7.5,
        "sl_atr_ranging": 2.25, "tp_atr_ranging": 6.0,
        "threshold_trending": 2.5, "threshold_ranging": 2.0,
        "adx_slope_threshold": -14.0, "adx_slope_threshold_strong": -18.0,
        "pullback_band_trending": 0.5, "pullback_band_ranging": 0.3,
        "max_lot": 1.0, "risk_mult": 1.0, "first_lock_atr": 0.8,
        "trailing": {
            "TREND_UP": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30)],
            "TREND_DOWN": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30)],
            "RANGING": [(1.80, 0.80), (2.50, 0.55), (3.50, 0.40), (5.00, 0.25)],
            "HIGH_VOL": [(1.80, 1.20), (2.50, 0.90), (3.50, 0.65), (5.00, 0.40)],
            "LOW_VOL": [(1.80, 0.70), (2.50, 0.50), (3.50, 0.35), (5.00, 0.20)],
        },
        "be_buffer": 0.60,
    },
    "USDJPY": {
        "sl_atr_trending": 3.0, "tp_atr_trending": 7.5,
        "sl_atr_ranging": 2.25, "tp_atr_ranging": 6.0,
        "threshold_trending": 2.5, "threshold_ranging": 2.0,
        "adx_slope_threshold": -14.0, "adx_slope_threshold_strong": -18.0,
        "pullback_band_trending": 0.5, "pullback_band_ranging": 0.3,
        "max_lot": 1.0, "risk_mult": 1.0, "first_lock_atr": 0.8,
        "trailing": {
            "TREND_UP": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30)],
            "TREND_DOWN": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30)],
            "RANGING": [(1.80, 0.80), (2.50, 0.55), (3.50, 0.40), (5.00, 0.25)],
            "HIGH_VOL": [(1.80, 1.20), (2.50, 0.90), (3.50, 0.65), (5.00, 0.40)],
            "LOW_VOL": [(1.80, 0.70), (2.50, 0.50), (3.50, 0.35), (5.00, 0.20)],
        },
        "be_buffer": 0.60,
    },
    "USDCAD": {
        "sl_atr_trending": 3.0, "tp_atr_trending": 7.5,
        "sl_atr_ranging": 2.25, "tp_atr_ranging": 6.0,
        "threshold_trending": 2.5, "threshold_ranging": 2.0,
        "adx_slope_threshold": -14.0, "adx_slope_threshold_strong": -18.0,
        "pullback_band_trending": 0.5, "pullback_band_ranging": 0.3,
        "max_lot": 1.0, "risk_mult": 1.0, "first_lock_atr": 0.8,
        "trailing": {
            "TREND_UP": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30)],
            "TREND_DOWN": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30)],
            "RANGING": [(1.80, 0.80), (2.50, 0.55), (3.50, 0.40), (5.00, 0.25)],
            "HIGH_VOL": [(1.80, 1.20), (2.50, 0.90), (3.50, 0.65), (5.00, 0.40)],
            "LOW_VOL": [(1.80, 0.70), (2.50, 0.50), (3.50, 0.35), (5.00, 0.20)],
        },
        "be_buffer": 0.60,
    },
    "US100.cash": {
        "sl_atr_trending": 2.0, "tp_atr_trending": 5.0,
        "sl_atr_ranging": 1.5, "tp_atr_ranging": 4.0,
        "threshold_trending": 2.5, "threshold_ranging": 2.0,
        "adx_slope_threshold": -14.0, "adx_slope_threshold_strong": -18.0,
        "pullback_band_trending": 0.5, "pullback_band_ranging": 0.3,
        "max_lot": 2.0, "risk_mult": 1.0, "first_lock_atr": 0.8,
        "trailing": {
            "TREND_UP": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30)],
            "TREND_DOWN": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30)],
            "RANGING": [(1.80, 0.80), (2.50, 0.55), (3.50, 0.40), (5.00, 0.25)],
            "HIGH_VOL": [(1.80, 1.20), (2.50, 0.90), (3.50, 0.65), (5.00, 0.40)],
            "LOW_VOL": [(1.80, 0.70), (2.50, 0.50), (3.50, 0.35), (5.00, 0.20)],
        },
        "be_buffer": 0.60,
    },
    "US30.cash": {
        "sl_atr_trending": 2.0, "tp_atr_trending": 5.0,
        "sl_atr_ranging": 1.5, "tp_atr_ranging": 4.0,
        "threshold_trending": 2.5, "threshold_ranging": 2.0,
        "adx_slope_threshold": -14.0, "adx_slope_threshold_strong": -18.0,
        "pullback_band_trending": 0.5, "pullback_band_ranging": 0.3,
        "max_lot": 2.0, "risk_mult": 1.0, "first_lock_atr": 0.8,
        "trailing": {
            "TREND_UP": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30)],
            "TREND_DOWN": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30)],
            "RANGING": [(1.80, 0.80), (2.50, 0.55), (3.50, 0.40), (5.00, 0.25)],
            "HIGH_VOL": [(1.80, 1.20), (2.50, 0.90), (3.50, 0.65), (5.00, 0.40)],
            "LOW_VOL": [(1.80, 0.70), (2.50, 0.50), (3.50, 0.35), (5.00, 0.20)],
        },
        "be_buffer": 0.60,
    },
    "JP225.cash": {
        "sl_atr_trending": 2.0, "tp_atr_trending": 5.0,
        "sl_atr_ranging": 1.5, "tp_atr_ranging": 4.0,
        "threshold_trending": 2.5, "threshold_ranging": 2.0,
        "adx_slope_threshold": -14.0, "adx_slope_threshold_strong": -18.0,
        "pullback_band_trending": 0.5, "pullback_band_ranging": 0.3,
        "max_lot": 2.0, "risk_mult": 1.0, "first_lock_atr": 0.8,
        "trailing": {
            "TREND_UP": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30)],
            "TREND_DOWN": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30)],
            "RANGING": [(1.80, 0.80), (2.50, 0.55), (3.50, 0.40), (5.00, 0.25)],
            "HIGH_VOL": [(1.80, 1.20), (2.50, 0.90), (3.50, 0.65), (5.00, 0.40)],
            "LOW_VOL": [(1.80, 0.70), (2.50, 0.50), (3.50, 0.35), (5.00, 0.20)],
        },
        "be_buffer": 0.60,
    },
    "EURGBP": {
        "sl_atr_trending": 3.0, "tp_atr_trending": 7.5,
        "sl_atr_ranging": 2.25, "tp_atr_ranging": 6.0,
        "threshold_trending": 2.5, "threshold_ranging": 2.0,
        "adx_slope_threshold": -14.0, "adx_slope_threshold_strong": -18.0,
        "pullback_band_trending": 0.5, "pullback_band_ranging": 0.3,
        "max_lot": 0.06, "risk_mult": 1.0, "first_lock_atr": 0.8,
        "trailing": {
            "TREND_UP": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30)],
            "TREND_DOWN": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30)],
            "RANGING": [(1.80, 0.80), (2.50, 0.55), (3.50, 0.40), (5.00, 0.25)],
            "HIGH_VOL": [(1.80, 1.20), (2.50, 0.90), (3.50, 0.65), (5.00, 0.40)],
            "LOW_VOL": [(1.80, 0.70), (2.50, 0.50), (3.50, 0.35), (5.00, 0.20)],
        },
        "be_buffer": 0.60,
    },
    "EURJPY": {
        "sl_atr_trending": 3.0, "tp_atr_trending": 7.5,
        "sl_atr_ranging": 2.25, "tp_atr_ranging": 6.0,
        "threshold_trending": 2.5, "threshold_ranging": 2.0,
        "adx_slope_threshold": -14.0, "adx_slope_threshold_strong": -18.0,
        "pullback_band_trending": 0.5, "pullback_band_ranging": 0.3,
        "max_lot": 0.01, "risk_mult": 1.0, "first_lock_atr": 0.8,
        "trailing": {
            "TREND_UP": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30)],
            "TREND_DOWN": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30)],
            "RANGING": [(1.80, 0.80), (2.50, 0.55), (3.50, 0.40), (5.00, 0.25)],
            "HIGH_VOL": [(1.80, 1.20), (2.50, 0.90), (3.50, 0.65), (5.00, 0.40)],
            "LOW_VOL": [(1.80, 0.70), (2.50, 0.50), (3.50, 0.35), (5.00, 0.20)],
        },
        "be_buffer": 0.60,
    },
    "GBPJPY": {
        "sl_atr_trending": 3.0, "tp_atr_trending": 7.5,
        "sl_atr_ranging": 2.25, "tp_atr_ranging": 6.0,
        "threshold_trending": 2.5, "threshold_ranging": 2.0,
        "adx_slope_threshold": -14.0, "adx_slope_threshold_strong": -18.0,
        "pullback_band_trending": 0.5, "pullback_band_ranging": 0.3,
        "max_lot": 0.06, "risk_mult": 1.0, "first_lock_atr": 0.8,
        "trailing": {
            "TREND_UP": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30)],
            "TREND_DOWN": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30)],
            "RANGING": [(1.80, 0.80), (2.50, 0.55), (3.50, 0.40), (5.00, 0.25)],
            "HIGH_VOL": [(1.80, 1.20), (2.50, 0.90), (3.50, 0.65), (5.00, 0.40)],
            "LOW_VOL": [(1.80, 0.70), (2.50, 0.50), (3.50, 0.35), (5.00, 0.20)],
        },
        "be_buffer": 0.60,
    },
    "AUDJPY": {
        "sl_atr_trending": 3.0, "tp_atr_trending": 7.5,
        "sl_atr_ranging": 2.25, "tp_atr_ranging": 6.0,
        "threshold_trending": 2.5, "threshold_ranging": 2.0,
        "adx_slope_threshold": -14.0, "adx_slope_threshold_strong": -18.0,
        "pullback_band_trending": 0.5, "pullback_band_ranging": 0.3,
        "max_lot": 0.03, "risk_mult": 1.0, "first_lock_atr": 0.8,
        "trailing": {
            "TREND_UP": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30)],
            "TREND_DOWN": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30)],
            "RANGING": [(1.80, 0.80), (2.50, 0.55), (3.50, 0.40), (5.00, 0.25)],
            "HIGH_VOL": [(1.80, 1.20), (2.50, 0.90), (3.50, 0.65), (5.00, 0.40)],
            "LOW_VOL": [(1.80, 0.70), (2.50, 0.50), (3.50, 0.35), (5.00, 0.20)],
        },
        "be_buffer": 0.60,
    },
    "BNBUSD": {
        "sl_atr_trending": 2.5, "tp_atr_trending": 6.0,
        "sl_atr_ranging": 2.0, "tp_atr_ranging": 4.0,
        "threshold_trending": 2.0, "threshold_ranging": 1.5,
        "adx_slope_threshold": -6.0, "adx_slope_threshold_strong": -10.0,
        "pullback_band_trending": 0.8, "pullback_band_ranging": 0.5,
        "max_lot": 0.01, "risk_mult": 1.0, "first_lock_atr": 1.2,
        "trailing": {
            "TREND_UP": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30)],
            "TREND_DOWN": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30)],
            "RANGING": [(1.80, 0.80), (2.50, 0.55), (3.50, 0.40), (5.00, 0.25)],
            "HIGH_VOL": [(1.80, 1.20), (2.50, 0.90), (3.50, 0.65), (5.00, 0.40)],
            "LOW_VOL": [(1.80, 0.70), (2.50, 0.50), (3.50, 0.35), (5.00, 0.20)],
        },
        "be_buffer": 0.65,
    },
    "US500.cash": {
        "sl_atr_trending": 2.0, "tp_atr_trending": 5.0,
        "sl_atr_ranging": 1.5, "tp_atr_ranging": 4.0,
        "threshold_trending": 2.5, "threshold_ranging": 2.0,
        "adx_slope_threshold": -14.0, "adx_slope_threshold_strong": -18.0,
        "pullback_band_trending": 0.5, "pullback_band_ranging": 0.3,
        "max_lot": 0.01, "risk_mult": 1.0, "first_lock_atr": 0.8,
        "trailing": {
            "TREND_UP": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30)],
            "TREND_DOWN": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30)],
            "RANGING": [(1.80, 0.80), (2.50, 0.55), (3.50, 0.40), (5.00, 0.25)],
            "HIGH_VOL": [(1.80, 1.20), (2.50, 0.90), (3.50, 0.65), (5.00, 0.40)],
            "LOW_VOL": [(1.80, 0.70), (2.50, 0.50), (3.50, 0.35), (5.00, 0.20)],
        },
        "be_buffer": 0.60,
    },
    "GER40.cash": {
        "sl_atr_trending": 2.0, "tp_atr_trending": 5.0,
        "sl_atr_ranging": 1.5, "tp_atr_ranging": 4.0,
        "threshold_trending": 2.5, "threshold_ranging": 2.0,
        "adx_slope_threshold": -14.0, "adx_slope_threshold_strong": -18.0,
        "pullback_band_trending": 0.5, "pullback_band_ranging": 0.3,
        "max_lot": 0.01, "risk_mult": 1.0, "first_lock_atr": 0.8,
        "trailing": {
            "TREND_UP": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30)],
            "TREND_DOWN": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30)],
            "RANGING": [(1.80, 0.80), (2.50, 0.55), (3.50, 0.40), (5.00, 0.25)],
            "HIGH_VOL": [(1.80, 1.20), (2.50, 0.90), (3.50, 0.65), (5.00, 0.40)],
            "LOW_VOL": [(1.80, 0.70), (2.50, 0.50), (3.50, 0.35), (5.00, 0.20)],
        },
        "be_buffer": 0.60,
    },
    "UK100.cash": {
        "sl_atr_trending": 2.0, "tp_atr_trending": 5.0,
        "sl_atr_ranging": 1.5, "tp_atr_ranging": 4.0,
        "threshold_trending": 2.5, "threshold_ranging": 2.0,
        "adx_slope_threshold": -14.0, "adx_slope_threshold_strong": -18.0,
        "pullback_band_trending": 0.5, "pullback_band_ranging": 0.3,
        "max_lot": 0.01, "risk_mult": 1.0, "first_lock_atr": 0.8,
        "trailing": {
            "TREND_UP": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30)],
            "TREND_DOWN": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30)],
            "RANGING": [(1.80, 0.80), (2.50, 0.55), (3.50, 0.40), (5.00, 0.25)],
            "HIGH_VOL": [(1.80, 1.20), (2.50, 0.90), (3.50, 0.65), (5.00, 0.40)],
            "LOW_VOL": [(1.80, 0.70), (2.50, 0.50), (3.50, 0.35), (5.00, 0.20)],
        },
        "be_buffer": 0.60,
    },
    "USOIL.cash": {
        "sl_atr_trending": 2.5, "tp_atr_trending": 6.0,
        "sl_atr_ranging": 2.0, "tp_atr_ranging": 4.0,
        "threshold_trending": 2.5, "threshold_ranging": 2.0,
        "adx_slope_threshold": -14.0, "adx_slope_threshold_strong": -18.0,
        "pullback_band_trending": 0.5, "pullback_band_ranging": 0.3,
        "max_lot": 0.06, "risk_mult": 1.0, "first_lock_atr": 0.8,
        "trailing": {
            "TREND_UP": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30)],
            "TREND_DOWN": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30)],
            "RANGING": [(1.80, 0.80), (2.50, 0.55), (3.50, 0.40), (5.00, 0.25)],
            "HIGH_VOL": [(1.80, 1.20), (2.50, 0.90), (3.50, 0.65), (5.00, 0.40)],
            "LOW_VOL": [(1.80, 0.70), (2.50, 0.50), (3.50, 0.35), (5.00, 0.20)],
        },
        "be_buffer": 0.60,
    },
    "UKOIL.cash": {
        "sl_atr_trending": 2.5, "tp_atr_trending": 6.0,
        "sl_atr_ranging": 2.0, "tp_atr_ranging": 4.0,
        "threshold_trending": 2.5, "threshold_ranging": 2.0,
        "adx_slope_threshold": -14.0, "adx_slope_threshold_strong": -18.0,
        "pullback_band_trending": 0.5, "pullback_band_ranging": 0.3,
        "max_lot": 0.01, "risk_mult": 1.0, "first_lock_atr": 0.8,
        "trailing": {
            "TREND_UP": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30)],
            "TREND_DOWN": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30)],
            "RANGING": [(1.80, 0.80), (2.50, 0.55), (3.50, 0.40), (5.00, 0.25)],
            "HIGH_VOL": [(1.80, 1.20), (2.50, 0.90), (3.50, 0.65), (5.00, 0.40)],
            "LOW_VOL": [(1.80, 0.70), (2.50, 0.50), (3.50, 0.35), (5.00, 0.20)],
        },
        "be_buffer": 0.60,
    },
    "NATGAS.cash": {
        "sl_atr_trending": 2.5, "tp_atr_trending": 6.0,
        "sl_atr_ranging": 2.0, "tp_atr_ranging": 4.0,
        "threshold_trending": 2.5, "threshold_ranging": 2.0,
        "adx_slope_threshold": -14.0, "adx_slope_threshold_strong": -18.0,
        "pullback_band_trending": 0.5, "pullback_band_ranging": 0.3,
        "max_lot": 0.01, "risk_mult": 1.0, "first_lock_atr": 0.8,
        "trailing": {
            "TREND_UP": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30)],
            "TREND_DOWN": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30)],
            "RANGING": [(1.80, 0.80), (2.50, 0.55), (3.50, 0.40), (5.00, 0.25)],
            "HIGH_VOL": [(1.80, 1.20), (2.50, 0.90), (3.50, 0.65), (5.00, 0.40)],
            "LOW_VOL": [(1.80, 0.70), (2.50, 0.50), (3.50, 0.35), (5.00, 0.20)],
        },
        "be_buffer": 0.60,
    },
}

SYMBOLS = list(SYMBOL_CONFIG.keys())

# ============================================================================
# PIP INFO
# ============================================================================
def get_pip_info(symbol):
    if symbol in ("XAUUSD", "XAGUSD"):
        return 0.01, 1.0
    elif symbol in ("US500.cash", "JP225.cash", "US30.cash", "US100.cash", "GER40.cash", "UK100.cash"):
        return 0.01, 1.0
    elif symbol in ("USOIL.cash", "UKOIL.cash", "NATGAS.cash", "BTCUSD", "ETHUSD", "SOLUSD", "BNBUSD"):
        return 0.01, 1.0
    return 0.0001, 10.0


# ============================================================================
# SIMTRADE CLASS
# ============================================================================
class SimTrade:
    __slots__ = (
        "symbol", "timeframe", "action", "entry", "sl", "tp", "atr_val", "regime",
        "open_bar", "open_time", "direction", "closed", "result", "profit_usd",
        "profit_pct", "peak_price", "trailing_sl", "partial_closed", "bars_held",
        "close_time", "close_price", "lot", "config", "_pip_size", "_pip_value",
    )

    def __init__(self, symbol, timeframe, action, entry, sl, tp, atr_val, regime, bar_idx, bar_time, balance, config):
        self.symbol = symbol
        self.timeframe = timeframe
        self.action = action
        self.entry = entry
        self.sl = sl
        self.tp = tp
        self.atr_val = atr_val
        self.regime = regime
        self.open_bar = bar_idx
        self.open_time = bar_time
        self.direction = 0 if action == "BUY" else 1
        self.closed = False
        self.result = None
        self.profit_usd = 0.0
        self.profit_pct = 0.0
        self.peak_price = entry
        self.trailing_sl = sl
        self.partial_closed = False
        self.bars_held = 0
        self.close_time = None
        self.close_price = entry
        self.lot = 0.01
        self.config = config
        self._pip_size, self._pip_value = get_pip_info(symbol)
        self._calc_lot(entry, sl, balance)

    def _calc_lot(self, entry, sl, balance):
        price_dist = abs(entry - sl)
        if price_dist > 0:
            risk_usd = balance * RISK_PER_TRADE * self.config.get("risk_mult", 1.0)
            risk_in_pips = price_dist / self._pip_size
            if risk_in_pips > 0:
                self.lot = risk_usd / (risk_in_pips * self._pip_value)
                self.lot = min(self.lot, self.config.get("max_lot", 1.0))
                self.lot = round(self.lot, 2)
                if self.lot < 0.01:
                    self.lot = 0.01

    def update(self, high, low, close, bar_idx):
        if self.closed:
            return
        self.bars_held += 1
        if self.action == "BUY":
            self.peak_price = max(self.peak_price, high)
        else:
            self.peak_price = min(self.peak_price, low)
        self._check_trailing(close, bar_idx)
        self._check_partial_tp(close)
        self._check_exit(high, low, close, bar_idx)

    def _check_trailing(self, close, bar_idx):
        if self.partial_closed:
            return
        profit_price = self.peak_price - self.entry if self.action == "BUY" else self.entry - self.peak_price
        profit_atr = profit_price / self.atr_val if self.atr_val > 0 else 0
        regime_trailing = self.config.get("trailing", {}).get(self.regime, [])
        for lock_atr, trail_atr in reversed(regime_trailing):
            if profit_atr >= lock_atr:
                new_sl = (self.peak_price - trail_atr * self.atr_val if self.action == "BUY"
                          else self.peak_price + trail_atr * self.atr_val)
                if self.action == "BUY" and new_sl > self.trailing_sl:
                    self.trailing_sl = new_sl
                elif self.action == "SELL" and new_sl < self.trailing_sl:
                    self.trailing_sl = new_sl
                break

    def _check_partial_tp(self, close):
        if self.partial_closed:
            return
        if self.tp == self.entry:
            return
        if self.action == "BUY":
            progress = (close - self.entry) / (self.tp - self.entry) if self.tp > self.entry else 0
        else:
            progress = (self.entry - close) / (self.entry - self.tp) if self.entry > self.tp else 0
        if progress >= 0.65:
            self.partial_closed = True
            be_buffer = self.config.get("be_buffer", 0.60)
            if self.action == "BUY":
                self.trailing_sl = max(self.trailing_sl, self.entry + be_buffer * self.atr_val)
            else:
                self.trailing_sl = min(self.trailing_sl, self.entry - be_buffer * self.atr_val)

    def _check_exit(self, high, low, close, bar_idx):
        if self.action == "BUY":
            if low <= self.trailing_sl:
                self._close(self.trailing_sl, "TRAILING")
            elif high >= self.tp:
                self._close(self.tp, "TP")
            elif self.bars_held >= TIMEOUT_BARS:
                self._close(close, "TIMEOUT")
        else:
            if high >= self.trailing_sl:
                self._close(self.trailing_sl, "TRAILING")
            elif low <= self.tp:
                self._close(self.tp, "TP")
            elif self.bars_held >= TIMEOUT_BARS:
                self._close(close, "TIMEOUT")

    def _close(self, price, result):
        self.closed = True
        self.result = result
        self.close_price = price
        if self.action == "BUY":
            self.profit_usd = (price - self.entry) * self.lot * self._pip_value
        else:
            self.profit_usd = (self.entry - price) * self.lot * self._pip_value
        self.profit_pct = self.profit_usd / INITIAL_BALANCE * 100


# ============================================================================
# INDICATORS
# ============================================================================
def compute_indicators(df, period=20):
    """Calcule ATR, ADX, MOM20, regime — version vectorisée."""
    c = df["close"].values.astype(float)
    h = df["high"].values.astype(float)
    l = df["low"].values.astype(float)
    n = len(c)

    # ATR vectorisé
    tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
    atr_raw = np.full(n, np.nan)
    atr_raw[1:] = tr
    # EMA ATR
    atr_vals = np.full(n, np.nan)
    if n > period:
        atr_vals[period] = np.mean(tr[:period])
        alpha = 2.0 / (period + 1)
        for i in range(period + 1, n):
            atr_vals[i] = alpha * tr[i-1] + (1 - alpha) * atr_vals[i-1]

    # MOM20
    mom_vals = np.full(n, np.nan)
    mom_vals[period:] = c[period:] - c[:n-period]

    # ADX vectorisé (approximation rapide via DI)
    adx_vals = np.full(n, np.nan)
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    for i in range(1, n):
        up = h[i] - h[i-1]
        down = l[i-1] - l[i]
        plus_dm[i] = up if (up > down and up > 0) else 0
        minus_dm[i] = down if (down > up and down > 0) else 0

    # Smoothed TR, +DM, -DM
    smooth_tr = np.full(n, np.nan)
    smooth_plus = np.full(n, np.nan)
    smooth_minus = np.full(n, np.nan)
    if n > 14:
        smooth_tr[14] = np.sum(tr[:14])
        smooth_plus[14] = np.sum(plus_dm[1:15])
        smooth_minus[14] = np.sum(minus_dm[1:15])
        for i in range(15, n):
            smooth_tr[i] = smooth_tr[i-1] - smooth_tr[i-1]/14 + tr[i-1]
            smooth_plus[i] = smooth_plus[i-1] - smooth_plus[i-1]/14 + plus_dm[i-1]
            smooth_minus[i] = smooth_minus[i-1] - smooth_minus[i-1]/14 + minus_dm[i-1]

        plus_di = np.where(smooth_tr > 0, 100 * smooth_plus / smooth_tr, 0)
        minus_di = np.where(smooth_tr > 0, 100 * smooth_minus / smooth_tr, 0)
        dx = np.where((plus_di + minus_di) > 0, 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di), 0)
        # Smoothed DX = ADX
        adx_vals[28] = np.mean(dx[14:29]) if n > 28 else 0
        for i in range(29, n):
            adx_vals[i] = (adx_vals[i-1] * 13 + dx[i]) / 14

    # Regime
    regime_vals = ["RANGING"] * n
    for i in range(28, n):
        if np.isnan(adx_vals[i]):
            regime_vals[i] = "RANGING"
        elif adx_vals[i] > 22:
            regime_vals[i] = "TREND_UP" if c[i] > c[i-1] else "TREND_DOWN"
        elif adx_vals[i] < 18:
            regime_vals[i] = "RANGING"
        else:
            regime_vals[i] = "TRANSITION"

    return atr_vals, adx_vals, mom_vals, regime_vals


# ============================================================================
# BACKTEST ENGINE
# ============================================================================
def backtest_symbol_tf(symbol, tf, df, config):
    """Backtest MOM20x3 sur un symbole/timeframe."""
    atr_vals, adx_vals, mom_vals, regime_vals = compute_indicators(df)
    trades = []
    balance = INITIAL_BALANCE
    position = None
    cooldown = 0
    consecutive_losses = 0
    n = len(df)
    c = df["close"].values
    h = df["high"].values
    l = df["low"].values

    for i in range(60, n):
        if cooldown > 0:
            cooldown -= 1
        if position is not None:
            position.update(h[i], l[i], c[i], i)
            if position.closed:
                balance += position.profit_usd
                trades.append(position)
                if position.profit_usd < 0:
                    consecutive_losses += 1
                    if consecutive_losses >= 5:
                        cooldown = 30
                else:
                    consecutive_losses = 0
                position = None
            continue

        if np.isnan(mom_vals[i]) or np.isnan(adx_vals[i]) or np.isnan(atr_vals[i]):
            continue
        if atr_vals[i] <= 0:
            continue

        regime = regime_vals[i]
        threshold = (config["threshold_trending"] if regime in ("TREND_UP", "TREND_DOWN")
                     else config["threshold_ranging"])
        mom = mom_vals[i]
        adx_val = adx_vals[i]

        if abs(mom) < threshold * atr_vals[i]:
            continue

        if adx_val < 20 and regime == "RANGING":
            continue

        action = "BUY" if mom > 0 else "SELL"
        entry = c[i]
        if action == "BUY":
            sl = entry - config["sl_atr_trending"] * atr_vals[i] if regime in ("TREND_UP", "TREND_DOWN") else entry - config["sl_atr_ranging"] * atr_vals[i]
            tp = entry + config["tp_atr_trending"] * atr_vals[i] if regime in ("TREND_UP", "TREND_DOWN") else entry + config["tp_atr_ranging"] * atr_vals[i]
        else:
            sl = entry + config["sl_atr_trending"] * atr_vals[i] if regime in ("TREND_UP", "TREND_DOWN") else entry + config["sl_atr_ranging"] * atr_vals[i]
            tp = entry - config["tp_atr_trending"] * atr_vals[i] if regime in ("TREND_UP", "TREND_DOWN") else entry - config["tp_atr_ranging"] * atr_vals[i]

        position = SimTrade(symbol, tf, action, entry, sl, tp, atr_vals[i], regime, i, i, balance, config)
        cooldown = 5

    return trades


# ============================================================================
# METRICS
# ============================================================================
def compute_metrics(trades):
    if not trades:
        return {"n": 0, "win_rate": 0, "profit_factor": 0, "total_pnl": 0,
                "max_drawdown_pct": 0, "p_value": 1.0, "significant": False}
    wins = [t for t in trades if t.profit_usd > 0]
    losses = [t for t in trades if t.profit_usd <= 0]
    n = len(trades)
    wr = len(wins) / n * 100
    gross_profit = sum(t.profit_usd for t in wins) if wins else 0
    gross_loss = abs(sum(t.profit_usd for t in losses)) if losses else 0
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    total_pnl = sum(t.profit_usd for t in trades)
    # Max drawdown
    equity = INITIAL_BALANCE
    peak = equity
    max_dd = 0
    for t in trades:
        equity += t.profit_usd
        peak = max(peak, equity)
        dd = (peak - equity) / peak * 100
        max_dd = max(max_dd, dd)
    # P-value (binomial test)
    p_value = 1.0
    if n >= 10:
        from scipy.stats import binomtest
        k = len(wins)
        p_value = binomtest(k, n, 0.5, alternative="greater").pvalue
    significant = p_value < 0.05 and n >= 20
    return {
        "n": n, "win_rate": round(wr, 1), "profit_factor": round(pf, 2),
        "total_pnl": round(total_pnl, 2), "max_drawdown_pct": round(max_dd, 1),
        "p_value": round(p_value, 4), "significant": significant,
    }


# ============================================================================
# WALK-FORWARD
# ============================================================================
def create_folds(n_bars, n_folds=5, test_ratio=0.15):
    test_size = int(n_bars * test_ratio)
    fold_size = n_bars // n_folds
    folds = []
    for i in range(n_folds):
        test_start = i * fold_size
        test_end = test_start + test_size
        train_start = test_end
        train_end = min(train_start + fold_size * (n_folds - 1), n_bars)
        if train_end - train_start < MIN_BARS:
            continue
        folds.append({
            "fold": i + 1, "train_start": train_start, "train_end": train_end,
            "test_start": test_start, "test_end": test_end,
        })
    return folds


def run_walk_forward():
    data_dir = Path("data/historical")
    out_dir = Path("runtime")
    out_dir.mkdir(exist_ok=True)

    print("=" * 110)
    print("  WALK-FORWARD VALIDATION — 21 SYMBOLES ACTIFS, 5 FOLDS")
    print("  MOM20x3 + Trailing ATR + Partial TP — Validation hors échantillon")
    print("=" * 110)

    start_all = now()
    all_results = {}
    total_folds = 0

    for sym in SYMBOLS:
        if sym not in SYMBOL_CONFIG:
            continue
        fpath = data_dir / f"{sym}_H1.parquet"
        if not fpath.exists():
            print(f"  {sym}: SKIP (no data)")
            continue

        df = pd.read_parquet(fpath)
        if len(df) < MIN_BARS + 100:
            print(f"  {sym}: SKIP ({len(df)} bars < {MIN_BARS + 100})")
            continue

        config = SYMBOL_CONFIG[sym]
        n_bars = len(df)
        folds = create_folds(n_bars, n_folds=5, test_ratio=0.15)
        if not folds:
            print(f"  {sym}: SKIP (not enough folds)")
            continue

        print(f"\n  === {sym} ({n_bars} bars, {len(folds)} folds) ===")
        all_results[sym] = {"folds": []}

        for fold in folds:
            t0 = now()
            train_df = df.iloc[fold["train_start"]:fold["train_end"]].reset_index(drop=True)
            is_trades = backtest_symbol_tf(sym, "H1", train_df, config)
            is_metrics = compute_metrics(is_trades)

            test_df = df.iloc[fold["test_start"]:fold["test_end"]].reset_index(drop=True)
            oos_trades = backtest_symbol_tf(sym, "H1", test_df, config)
            oos_metrics = compute_metrics(oos_trades)

            elapsed = now() - t0
            wr_drop = is_metrics["win_rate"] - oos_metrics["win_rate"]

            fold_result = {
                "fold": fold["fold"],
                "is_wr": is_metrics["win_rate"],
                "is_pf": is_metrics["profit_factor"],
                "is_n": is_metrics["n"],
                "oos_wr": oos_metrics["win_rate"],
                "oos_pf": oos_metrics["profit_factor"],
                "oos_n": oos_metrics["n"],
                "oos_pnl": oos_metrics["total_pnl"],
                "oos_dd": oos_metrics["max_drawdown_pct"],
                "oos_p_value": oos_metrics["p_value"],
                "oos_significant": oos_metrics["significant"],
                "wr_drop": round(wr_drop, 1),
            }
            all_results[sym]["folds"].append(fold_result)
            total_folds += 1

            status = "PASS" if oos_metrics["win_rate"] >= 50 and wr_drop < 20 else "WARN"
            print(
                f"    Fold {fold['fold']}: IS_WR={is_metrics['win_rate']:5.1f}% OOS_WR={oos_metrics['win_rate']:5.1f}% "
                f"Drop={wr_drop:+5.1f}% PF={oos_metrics['profit_factor']:5.2f} N={oos_metrics['n']:3d} "
                f"DD={oos_metrics['max_drawdown_pct']:5.1f}% p={oos_metrics['p_value']:.4f} [{status}]"
            )

    # ── RÉSUMÉ ──
    print(f"\n{'=' * 110}")
    print(f"  RÉSUMÉ WALK-FORWARD — {total_folds} FOLDS")
    print(f"{'=' * 110}")

    summary = {"pass": 0, "marginal": 0, "fail": 0}
    verdicts = {}

    print(f"\n  {'Symbole':<16} {'IS WR':>7} {'OOS WR':>7} {'Drop':>7} {'OOS PF':>7} {'OOS DD':>7} {'p-value':>8} {'Sig':>5} {'Verdict':>12}")
    print(f"  {'-' * 90}")

    for sym, data in sorted(all_results.items()):
        if not data["folds"]:
            continue
        avg_is_wr = np.mean([f["is_wr"] for f in data["folds"]])
        avg_oos_wr = np.mean([f["oos_wr"] for f in data["folds"]])
        avg_drop = avg_is_wr - avg_oos_wr
        avg_oos_pf = np.mean([f["oos_pf"] for f in data["folds"]])
        avg_oos_dd = np.mean([f["oos_dd"] for f in data["folds"]])
        avg_p_value = np.mean([f["oos_p_value"] for f in data["folds"]])
        any_sig = any(f["oos_significant"] for f in data["folds"])

        if avg_oos_wr >= 55 and avg_drop < 15 and avg_oos_pf >= 1.2:
            verdict = "PASS"
            summary["pass"] += 1
        elif avg_oos_wr >= 50 and avg_drop < 20 and avg_oos_pf >= 1.0:
            verdict = "MARGINAL"
            summary["marginal"] += 1
        else:
            verdict = "FAIL"
            summary["fail"] += 1
        verdicts[sym] = verdict

        print(
            f"  {sym:<16} {avg_is_wr:>6.1f}% {avg_oos_wr:>6.1f}% {avg_drop:>+6.1f}% "
            f"{avg_oos_pf:>6.2f} {avg_oos_dd:>6.1f}% {avg_p_value:>8.4f} "
            f"{'  Y' if any_sig else '  N'} {verdict:>12}"
        )

    print(f"\n  {'-' * 90}")
    print(f"  PASS: {summary['pass']} | MARGINAL: {summary['marginal']} | FAIL: {summary['fail']}")
    print(f"  Total folds: {total_folds} | Time: {now() - start_all:.0f}s")

    # ── VERDICT FINAL ──
    total = len([k for k, v in all_results.items() if v["folds"]])
    print(f"\n{'=' * 110}")
    print(f"  VERDICT FINAL")
    print(f"{'=' * 110}")
    if summary["pass"] > total * 0.5:
        print(f"  WALK-FORWARD PASS — {summary['pass']}/{total} symboles robustes")
        print(f"  -> Le MOM20x3 a un edge statistique reel sur la majorite des symboles")
    elif summary["fail"] > total * 0.5:
        print(f"  WALK-FORWARD FAIL — {summary['fail']}/{total} symboles overfités")
        print(f"  -> Il faut ajuster les seuils ou desactiver les symboles faibles")
    else:
        print(f"  WALK-FORWARD MARGINAL — résultats mitigés")
        print(f"  -> Envisager de desactiver les symboles FAIL")

    print(f"\n  Symboles recommandés pour challenge: {', '.join(s for s, v in verdicts.items() if v == 'PASS')}")
    print(f"  Symboles à surveiller: {', '.join(s for s, v in verdicts.items() if v == 'MARGINAL')}")
    print(f"  Symboles à desactiver: {', '.join(s for s, v in verdicts.items() if v == 'FAIL')}")
    print(f"{'=' * 110}")

    # ── SAVE ──
    export = {
        "timestamp": str(pd.Timestamp.now()),
        "summary": summary,
        "total_folds": total_folds,
        "elapsed_s": round(now() - start_all, 1),
        "verdicts": verdicts,
        "results": {},
    }
    for sym, data in all_results.items():
        if data["folds"]:
            export["results"][sym] = {
                "avg_is_wr": round(np.mean([f["is_wr"] for f in data["folds"]]), 1),
                "avg_oos_wr": round(np.mean([f["oos_wr"] for f in data["folds"]]), 1),
                "avg_oos_pf": round(np.mean([f["oos_pf"] for f in data["folds"]]), 2),
                "avg_oos_dd": round(np.mean([f["oos_dd"] for f in data["folds"]]), 1),
                "avg_p_value": round(np.mean([f["oos_p_value"] for f in data["folds"]]), 4),
                "folds": data["folds"],
            }
    out_path = out_dir / "walk_forward_full.json"
    with open(out_path, "w") as f:
        json.dump(export, f, indent=2, default=str)
    print(f"\n  Saved: {out_path}")

    return summary


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--timeframe", default="H1")
    args = parser.parse_args()
    run_walk_forward()
