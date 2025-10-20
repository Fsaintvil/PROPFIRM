"""
Configuration centralisée pour éliminer les valeurs hardcodées.
Tous les paramètres du système de trading sont définis ici.
"""

import os
from typing import Dict, Any


class TradingConfig:
    """Configuration centralisée du système de trading"""

    # === TIMEOUTS ET INTERVALLES ===
    TRADING_INTERVAL_SECONDS = int(os.getenv("TRADING_INTERVAL", "600"))
    CONNECTION_TIMEOUT_SECONDS = int(os.getenv("CONNECTION_TIMEOUT", "30"))
    RETRY_DELAY_SECONDS = float(os.getenv("RETRY_DELAY", "1.0"))
    ERROR_SLEEP_SECONDS = int(os.getenv("ERROR_SLEEP", "300"))
    MIN_SLEEP_SECONDS = int(os.getenv("MIN_SLEEP", "60"))

    # === SEUILS DE DÉCISION ===
    DEFAULT_CONFIDENCE_THRESHOLD = float(
        os.getenv("CONFIDENCE_THRESHOLD", "0.50")
    )
    MIN_CONFIDENCE_THRESHOLD = float(os.getenv("MIN_CONFIDENCE", "0.60"))
    MAX_CONFIDENCE_THRESHOLD = float(os.getenv("MAX_CONFIDENCE", "0.85"))

    # === GESTION DES DONNÉES ===
    MAX_HISTORY_TRADES = int(os.getenv("MAX_HISTORY_TRADES", "1000"))
    MAX_MARKET_DATA_BARS = int(os.getenv("MAX_MARKET_DATA_BARS", "300"))
    CLEANUP_CYCLE_INTERVAL = int(os.getenv("CLEANUP_CYCLE", "20"))
    LOG_SUMMARY_INTERVAL = int(os.getenv("LOG_SUMMARY_INTERVAL", "5"))

    # === PARAMÈTRES DE RISQUE ===
    BASE_RISK_PCT = float(os.getenv("BASE_RISK_PCT", "0.02"))
    MAX_RISK_PCT = float(os.getenv("MAX_RISK_PCT", "0.05"))
    ATR_FALLBACK_PCT = float(os.getenv("ATR_FALLBACK_PCT", "0.001"))
    ATR_MIN_MULTIPLIER = float(os.getenv("ATR_MIN_MULT", "0.0005"))
    ATR_MAX_MULTIPLIER = float(os.getenv("ATR_MAX_MULT", "0.005"))

    # === VOLATILITÉ ET FILTRES ===
    VOLATILITY_THRESHOLD_IMMEDIATE = float(
        os.getenv("VOL_THRESH_IMM", "0.008")
    )
    VOLATILITY_THRESHOLD_NORMAL = float(os.getenv("VOL_THRESH_NORM", "0.012"))
    UNCERTAINTY_MAX = float(os.getenv("UNCERTAINTY_MAX", "0.4"))
    PATTERN_STRENGTH_MIN = float(os.getenv("PATTERN_STRENGTH_MIN", "0.5"))

    # === SYSTÈME IA ===
    ENSEMBLE_MAX_MODELS = int(os.getenv("ENSEMBLE_MAX", "3"))
    PERFORMANCE_WINDOW = int(os.getenv("PERF_WINDOW", "100"))
    LEARNING_RATE = float(os.getenv("LEARNING_RATE", "0.01"))
    MEMORY_WINDOW = int(os.getenv("MEMORY_WINDOW", "500"))
    RECALIBRATION_FREQUENCY = int(os.getenv("RECALIB_FREQ", "50"))

    # === RETRY ET ROBUSTESSE ===
    MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
    BACKOFF_MULTIPLIER = float(os.getenv("BACKOFF_MULT", "2.0"))
    CONNECTION_CHECK_INTERVAL = int(os.getenv("CONN_CHECK_INTERVAL", "10"))

    # === PARAMÈTRES DE POSITION ===
    BASE_POSITION_SIZE = float(os.getenv('BASE_POSITION_SIZE', '0.1'))
    MAX_POSITION_SIZE = float(os.getenv('MAX_POSITION_SIZE', '10.0'))
    MIN_POSITION_SIZE = float(os.getenv('MIN_POSITION_SIZE', '0.01'))
    MAX_DRAWDOWN_THRESHOLD = float(os.getenv('MAX_DRAWDOWN_THRESHOLD', '0.05'))
    DRAWDOWN_REDUCTION_FACTOR = float(
        os.getenv('DRAWDOWN_REDUCTION_FACTOR', '0.5')
    )
    POSITION_SIZE_FACTOR = float(os.getenv('POSITION_SIZE_FACTOR', '0.00001'))

    @classmethod
    def get_adaptive_threshold_range(cls) -> tuple:
        """Retourne la plage de seuils adaptatifs"""
        return (cls.MIN_CONFIDENCE_THRESHOLD, cls.MAX_CONFIDENCE_THRESHOLD)

    @classmethod
    def get_volatility_threshold(cls, urgency: str) -> float:
        """Retourne le seuil de volatilité selon l'urgence"""
        if urgency == 'immediate':
            return cls.VOLATILITY_THRESHOLD_IMMEDIATE
        return cls.VOLATILITY_THRESHOLD_NORMAL

    @classmethod
    def get_all_config(cls) -> Dict[str, Any]:
        """Retourne toute la configuration comme dictionnaire"""
        return {
            attr: getattr(cls, attr)
            for attr in dir(cls)
            if not attr.startswith('_') and not callable(getattr(cls, attr))
        }

    @classmethod
    def validate_config(cls) -> bool:
        """Valide la cohérence de la configuration"""
        errors = []

        if cls.MIN_CONFIDENCE_THRESHOLD >= cls.MAX_CONFIDENCE_THRESHOLD:
            errors.append("MIN_CONFIDENCE >= MAX_CONFIDENCE")

        if cls.DEFAULT_CONFIDENCE_THRESHOLD < cls.MIN_CONFIDENCE_THRESHOLD:
            errors.append("DEFAULT_CONFIDENCE < MIN_CONFIDENCE")

        if cls.DEFAULT_CONFIDENCE_THRESHOLD > cls.MAX_CONFIDENCE_THRESHOLD:
            errors.append("DEFAULT_CONFIDENCE > MAX_CONFIDENCE")

        if cls.BASE_RISK_PCT > cls.MAX_RISK_PCT:
            errors.append("BASE_RISK > MAX_RISK")

        if cls.ATR_MIN_MULTIPLIER >= cls.ATR_MAX_MULTIPLIER:
            errors.append("ATR_MIN >= ATR_MAX")

        if errors:
            print(f"❌ Erreurs de configuration: {', '.join(errors)}")
            return False

        print("✅ Configuration valide")
        return True
