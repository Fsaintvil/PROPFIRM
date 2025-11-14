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
    # === PERFORMANCE / QUALITY THRESHOLDS ===
    WINRATE_MIN_SYMBOL = float(os.getenv("WINRATE_MIN_SYMBOL", "0.45"))
    EXPECTANCY_MIN_SYMBOL = float(os.getenv("EXPECTANCY_MIN_SYMBOL", "0.00"))
    DAILY_LOSS_LIMIT_PCT = float(os.getenv("DAILY_LOSS_LIMIT_PCT", "0.03"))  # 3% défaut

    # Lots par défaut par symbole (centralisation)
    # Prioritaires si présents (AI_VOLUME reste utilisé comme fallback)
    PER_SYMBOL_DEFAULT_LOTS = {
        "BTCUSD": 0.01,
        "ETHUSD": 0.01,
        "XAUUSD": 0.01,
        "USDCAD": 0.03,
        "AUDNZD": 0.03,
        "EURJPY": 0.05,
        "GBPCHF": 0.03,
        "NZDJPY": 0.03,
        "EURUSD": 0.03,
        "EURAUD": 0.03,
        "US500.cash": 0.01,
        "JP225.cash": 0.01,
    }

    ENABLE_PROFIT_LOCK = os.getenv("ENABLE_PROFIT_LOCK", "1") in {"1", "true", "True"}
    PROFIT_LOCK_MIN_R = float(os.getenv("PROFIT_LOCK_MIN_R", "1.2"))  # activer après 1.2R
    PROFIT_LOCK_SECURE_R = float(os.getenv("PROFIT_LOCK_SECURE_R", "0.6"))  # sécuriser 0.6R
    # Après 1.8R profit, on resserre le SL à 1.0R
    PROFIT_LOCK_TRAIL_R = float(os.getenv("PROFIT_LOCK_TRAIL_R", "1.8"))

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

    # === FONCTIONNALITÉS OPTIONNELLES ===
    # Activer la convergence MTF (15m) en live (1=on, 0=off)
    USE_MTF_CONVERGENCE = os.getenv('USE_MTF', '1').strip() not in ('0', 'false', 'False')
    # Activer l’usage des fondamentaux (1=on, 0=off)
    USE_FUNDAMENTAL_CONFLUENCE = (
        os.getenv('USE_FUNDAMENTAL', '1').strip() not in ('0', 'false', 'False')
    )
    # Boost max accordé par la confluence fondamentale (0..0.2 conseillé)
    FUNDAMENTAL_BOOST_MAX = float(os.getenv('FUNDAMENTAL_BOOST_MAX', '0.07'))
    # Activer l’extension technique MTF (EMA/BB/ATR/MACD_hist)
    USE_EXTENDED_MTF_TECH = (
        os.getenv('USE_EXT_MTF_TECH', '1').strip() not in ('0', 'false', 'False')
    )

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

    # === NOUVEAU: Seuils minimaux par symbole (politique prudente) ===
    @classmethod
    def per_symbol_min_confidence(cls, symbol: str, base: float | None = None) -> float:
        """Retourne un plancher de confiance par symbole.

        Logique:
        - Crypto (BTC, ETH): ≥ max(base, 0.80)
        - Métaux précieux (XAU): ≥ max(base, 0.78)
        - Indices *.cash: ≥ max(base, 0.80)
        - Forex autres: ≥ max(base, 0.72)
        """
        if base is None:
            base = cls.DEFAULT_CONFIDENCE_THRESHOLD
        try:
            s = symbol.upper()
            if 'BTC' in s or 'ETH' in s:
                return max(base, 0.80)
            if 'XAU' in s:
                return max(base, 0.78)
            if s.endswith('.CASH'):
                return max(base, 0.80)
            # Forex / général
            return max(base, 0.72)
        except Exception:
            return max(base, 0.72)
