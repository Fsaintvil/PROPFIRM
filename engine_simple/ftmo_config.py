"""Configuration du FTMO Protector — niveaux de trailing, buffers BE, constants.

Extrait de ftmo_protector.py pour réduire la god class.
Calibration spécifique par actif (Juin 2026).
"""

# ============================================================================
# TRAILING STOP — Par actif et par régime
# ============================================================================
# Chaque actif a des caractéristiques de volatilité différentes.
# Le trailing doit s'adapter pour protéger les profits sans sortir trop tôt.
#
# XAUUSD: Or — tendances longues, trailing serré en trending
# BTCUSD: Bitcoin — volatilité extrême, trailing large pour laisser bouger
# US500.cash: S&P 500 — volatilité modérée, trailing standard
# ============================================================================

# Niveaux de trailing par régime et par actif
# Format : (profit_atr_seuil, trail_distance_mult)

TRAILING_BY_SYMBOL = {
    # ═══════════════════════════════════════════════════════════════════════
    # XAUUSD H4 — Or (Juin 2026 — SYNCHRONISÉ AVEC PROFIL)
    # Lock unifié à 1.0×ATR pour tous les régimes (profil institutionnel).
    # Trailing modérément serré : tendances H4 longues → protéger les gains.
    # ═══════════════════════════════════════════════════════════════════════
    "XAUUSD": {
        "TREND_UP": [(1.0, 0.80), (2.0, 0.50), (3.0, 0.30), (5.0, 0.15)],
        "TREND_DOWN": [(1.0, 0.80), (2.0, 0.50), (3.0, 0.30), (5.0, 0.15)],
        "RANGING": [(1.0, 0.50), (2.0, 0.35), (3.0, 0.20), (5.0, 0.10)],
        "HIGH_VOL": [(1.0, 1.00), (2.0, 0.70), (3.0, 0.50), (5.0, 0.25)],
        "LOW_VOL": [(1.0, 0.35), (2.0, 0.22), (3.0, 0.12), (5.0, 0.06)],
    },
    # ═══════════════════════════════════════════════════════════════════════
    # BTCUSD H1 — Bitcoin (Juin 2026 — AJUSTÉ)
    # Lock réduit à 1.0×ATR (était 1.5×ATR, jugé trop long par l'opérateur).
    # Trailing crypto modéré : laisse respirer mais verrouille plus tôt.
    # Avec ATR≈$493, lock à 1.0 = activation après ~$493 de mouvement.
    # ═══════════════════════════════════════════════════════════════════════
    "BTCUSD": {
        "TREND_UP": [(1.0, 0.80), (2.0, 0.50), (3.0, 0.30), (5.0, 0.15)],
        "TREND_DOWN": [(1.0, 0.80), (2.0, 0.50), (3.0, 0.30), (5.0, 0.15)],
        "RANGING": [(1.0, 0.60), (2.0, 0.40), (3.0, 0.25), (5.0, 0.12)],
        "HIGH_VOL": [(1.0, 1.00), (2.0, 0.70), (3.0, 0.50), (5.0, 0.25)],
        "LOW_VOL": [(1.0, 0.40), (2.0, 0.30), (3.0, 0.15), (5.0, 0.08)],
    },
    # ═══════════════════════════════════════════════════════════════════════
    # US500.cash H4 — S&P 500 (18 Juin 2026 — TRAILING RACCORCI)
    # Premier lock baissé 1.0→0.5 ATR : les trades US500 ont des gains max de $0.76
    # (ATR=~19pts), le trailing à 1.0×ATR ne s'activait JAMAIS (besoin de ~$1.50-2.00).
    # Avec 0.5×ATR, le trailing s'active à ~$0.75 → sécurise les petits gains.
    # ═══════════════════════════════════════════════════════════════════════
    "US500.cash": {
        "TREND_UP": [(0.5, 0.60), (1.0, 0.38), (2.0, 0.22), (3.0, 0.10)],
        "TREND_DOWN": [(0.5, 0.60), (1.0, 0.38), (2.0, 0.22), (3.0, 0.10)],
        "RANGING": [(0.5, 0.40), (1.0, 0.28), (2.0, 0.15), (3.0, 0.08)],
        "HIGH_VOL": [(1.0, 0.80), (2.0, 0.55), (3.0, 0.35), (5.0, 0.18)],
        "LOW_VOL": [(1.0, 0.30), (2.0, 0.18), (3.0, 0.10), (5.0, 0.05)],
    },
    # ═══════════════════════════════════════════════════════════════════════
    # EURUSD H1 — Euro/Dollar (Juin 2026)
    # Pas de profil dédié (EURUSD n'a pas de SymbolInstitutionalProfile).
    # Lock serré à 0.80×ATR (forex H1 rapide → verrouiller tôt).
    # ═══════════════════════════════════════════════════════════════════════
    "EURUSD": {
        "TREND_UP": [(0.80, 0.50), (2.00, 0.35), (3.00, 0.20), (5.00, 0.10)],
        "TREND_DOWN": [(0.80, 0.50), (2.00, 0.35), (3.00, 0.20), (5.00, 0.10)],
        "RANGING": [(0.80, 0.40), (2.00, 0.25), (3.00, 0.15), (5.00, 0.08)],
        "HIGH_VOL": [(0.80, 0.70), (2.00, 0.50), (3.00, 0.35), (5.00, 0.20)],
        "LOW_VOL": [(0.80, 0.30), (2.00, 0.18), (3.00, 0.12), (5.00, 0.06)],
    },
    # ═══════════════════════════════════════════════════════════════════════
    # USDJPY H1 — Dollar/Yen (23 Juin 2026 — NOUVEAU, profil forex standard)
    # ═══════════════════════════════════════════════════════════════════════
    "USDJPY": {
        "TREND_UP": [(0.80, 0.50), (2.00, 0.35), (3.00, 0.20), (5.00, 0.10)],
        "TREND_DOWN": [(0.80, 0.50), (2.00, 0.35), (3.00, 0.20), (5.00, 0.10)],
        "RANGING": [(0.80, 0.40), (2.00, 0.25), (3.00, 0.15), (5.00, 0.08)],
        "HIGH_VOL": [(0.80, 0.70), (2.00, 0.50), (3.00, 0.35), (5.00, 0.20)],
        "LOW_VOL": [(0.80, 0.30), (2.00, 0.18), (3.00, 0.12), (5.00, 0.06)],
    },
    # ═══════════════════════════════════════════════════════════════════════
    # GBPUSD H1 — Livre/Dollar (23 Juin 2026 — NOUVEAU, profil forex standard)
    # ═══════════════════════════════════════════════════════════════════════
    "GBPUSD": {
        "TREND_UP": [(0.80, 0.50), (2.00, 0.35), (3.00, 0.20), (5.00, 0.10)],
        "TREND_DOWN": [(0.80, 0.50), (2.00, 0.35), (3.00, 0.20), (5.00, 0.10)],
        "RANGING": [(0.80, 0.40), (2.00, 0.25), (3.00, 0.15), (5.00, 0.08)],
        "HIGH_VOL": [(0.80, 0.70), (2.00, 0.50), (3.00, 0.35), (5.00, 0.20)],
        "LOW_VOL": [(0.80, 0.30), (2.00, 0.18), (3.00, 0.12), (5.00, 0.06)],
    },
    # ═══════════════════════════════════════════════════════════════════════
    # AUDUSD H1 — Australien/Dollar (23 Juin 2026 — NOUVEAU, profil forex standard)
    # ═══════════════════════════════════════════════════════════════════════
    "AUDUSD": {
        "TREND_UP": [(0.80, 0.50), (2.00, 0.35), (3.00, 0.20), (5.00, 0.10)],
        "TREND_DOWN": [(0.80, 0.50), (2.00, 0.35), (3.00, 0.20), (5.00, 0.10)],
        "RANGING": [(0.80, 0.40), (2.00, 0.25), (3.00, 0.15), (5.00, 0.08)],
        "HIGH_VOL": [(0.80, 0.70), (2.00, 0.50), (3.00, 0.35), (5.00, 0.20)],
        "LOW_VOL": [(0.80, 0.30), (2.00, 0.18), (3.00, 0.12), (5.00, 0.06)],
    },
    # ═══════════════════════════════════════════════════════════════════════
    # USDCAD H1 — US/Canadian Dollar (23 Juin 2026 — NOUVEAU, profil forex standard)
    # ═══════════════════════════════════════════════════════════════════════
    "USDCAD": {
        "TREND_UP": [(0.80, 0.50), (2.00, 0.35), (3.00, 0.20), (5.00, 0.10)],
        "TREND_DOWN": [(0.80, 0.50), (2.00, 0.35), (3.00, 0.20), (5.00, 0.10)],
        "RANGING": [(0.80, 0.40), (2.00, 0.25), (3.00, 0.15), (5.00, 0.08)],
        "HIGH_VOL": [(0.80, 0.70), (2.00, 0.50), (3.00, 0.35), (5.00, 0.20)],
        "LOW_VOL": [(0.80, 0.30), (2.00, 0.18), (3.00, 0.12), (5.00, 0.06)],
    },
}

# Fallback par défaut (ancien comportement)
TRAILING_BY_REGIME = {
    "TREND_UP": [(1.00, 0.80), (2.00, 0.50), (3.00, 0.30), (5.00, 0.15)],
    "TREND_DOWN": [(1.00, 0.80), (2.00, 0.50), (3.00, 0.30), (5.00, 0.15)],
    "RANGING": [(1.00, 0.50), (2.00, 0.35), (3.00, 0.20), (5.00, 0.10)],
    "HIGH_VOL": [(1.00, 1.00), (2.00, 0.70), (3.00, 0.50), (5.00, 0.25)],
    "LOW_VOL": [(1.00, 0.40), (2.00, 0.25), (3.00, 0.15), (5.00, 0.08)],
}


def get_trailing_for_symbol(symbol: str, regime: str) -> list:
    """Retourne les niveaux de trailing pour un symbole et un régime donné."""
    sym_trailing = TRAILING_BY_SYMBOL.get(symbol)
    if sym_trailing and regime in sym_trailing:
        return sym_trailing[regime]
    return TRAILING_BY_REGIME.get(regime, TRAILING_BY_REGIME["RANGING"])


# ============================================================================
# BREAK-EVEN BUFFER — Par actif et par régime
# ============================================================================
# Buffer après partial TP pour éviter que le trade revienne en perte.
# Plus le marché est volatile, plus le buffer doit être large.

BE_BUFFER_BY_SYMBOL = {
    "XAUUSD": {
        "TREND_UP": 0.55,  # or: buffer serré en trending
        "TREND_DOWN": 0.55,
        "RANGING": 0.75,  # ranging: plus large
        "HIGH_VOL": 0.90,  # haute vol: très large
        "LOW_VOL": 0.45,  # basse vol: serré
    },
    "BTCUSD": {
        "TREND_UP": 0.70,  # crypto: buffer large
        "TREND_DOWN": 0.70,
        "RANGING": 0.85,  # ranging: très large
        "HIGH_VOL": 1.10,  # haute vol: extrêmement large
        "LOW_VOL": 0.50,  # basse vol: standard
    },
    "US500.cash": {
        "TREND_UP": 0.60,  # indice: standard
        "TREND_DOWN": 0.60,
        "RANGING": 0.80,  # ranging: large
        "HIGH_VOL": 1.00,  # haute vol: très large
        "LOW_VOL": 0.50,  # basse vol: serré
    },
    "EURUSD": {
        "TREND_UP": 0.55,  # forex: buffer standard
        "TREND_DOWN": 0.55,
        "RANGING": 0.75,  # ranging: large
        "HIGH_VOL": 0.90,  # haute vol: très large
        "LOW_VOL": 0.40,  # basse vol: serré
    },
    "USDJPY": {
        "TREND_UP": 0.55,
        "TREND_DOWN": 0.55,
        "RANGING": 0.75,
        "HIGH_VOL": 0.90,
        "LOW_VOL": 0.40,
    },
    "GBPUSD": {
        "TREND_UP": 0.55,
        "TREND_DOWN": 0.55,
        "RANGING": 0.75,
        "HIGH_VOL": 0.90,
        "LOW_VOL": 0.40,
    },
    "AUDUSD": {
        "TREND_UP": 0.55,
        "TREND_DOWN": 0.55,
        "RANGING": 0.75,
        "HIGH_VOL": 0.90,
        "LOW_VOL": 0.40,
    },
    "USDCAD": {
        "TREND_UP": 0.55,
        "TREND_DOWN": 0.55,
        "RANGING": 0.75,
        "HIGH_VOL": 0.90,
        "LOW_VOL": 0.40,
    },
}

# Fallback par défaut
BE_BUFFER_BY_REGIME = {
    "TREND_UP": 0.60,
    "TREND_DOWN": 0.60,
    "RANGING": 0.80,
    "HIGH_VOL": 1.00,
    "LOW_VOL": 0.50,
}


def get_be_buffer_for_symbol(symbol: str, regime: str) -> float:
    """Retourne le buffer BE pour un symbole et un régime donné."""
    sym_buffer = BE_BUFFER_BY_SYMBOL.get(symbol)
    if sym_buffer and regime in sym_buffer:
        return sym_buffer[regime]
    return BE_BUFFER_BY_REGIME.get(regime, 0.60)


# Durée de validité du cache ATR en secondes
ATR_CACHE_TTL = 60

# Seuils de trailing par défaut
FIRST_LOCK_ATR = (
    0.5  # premier lock du trailing (fallback si symbole non trouvé) — Juin 2026: baissé de 1.0 pour accélérer fermeture
)

# Per-symbol risk_mult cap: Juin 2026 — EURUSD ajouté
RISK_MULT_CAP = {
    "XAUUSD": 1.25,
    "BTCUSD": 1.00,
    "US500.cash": 1.15,
    "EURUSD": 2.00,
    "USDJPY": 1.50,
    "GBPUSD": 1.50,
    "AUDUSD": 1.25,
    "USDCAD": 1.25,
}

# Per-symbol max positions (total BUY+SELL) — calibré 25 Juin 2026
# Aligné sur max_positions_per_symbol=4 (pipeline conf>85%→4 max)
MAX_POS_PER_SYMBOL = {
    "XAUUSD": 4,
    "BTCUSD": 4,
    "US500.cash": 4,
    "EURUSD": 4,
    "USDJPY": 4,
    "GBPUSD": 4,
    "AUDUSD": 4,
    "USDCAD": 4,
}

# ============================================================================
# DD THRESHOLDS — Risk reduction levels
# ============================================================================
DD_REDUCE_THRESHOLD = 0.05  # 5% DD → risk × (1 - dd_peak)
DD_CRITICAL_THRESHOLD = 0.07  # 7% DD → risk × 0.20 (aggressive reduction)
DD_AUTODISABLE_THRESHOLD = 0.20  # 20% WR → auto-disable symbol

# ============================================================================
# TIME-STOP — Maximum position hold duration
# ============================================================================
MAX_POSITION_HOLD_HOURS = 12  # hours before time-stop
TIME_STOP_MIN_PROFIT_ATR = 0.5  # minimum profit in ATR to trigger time-stop

# ============================================================================
# PULLBACK FILTER — Score threshold for pullback enforcement
# ============================================================================
PULLBACK_FILTER_SCORE_THRESHOLD = 0.60  # Scénario A: abaissé 0.65→0.60 pour +20% trades

# Premier lock par symbole — Harmonisé avec TRAILING_BY_SYMBOL (19 Juin 2026)
# Les valeurs correspondent au premier threshold ATR de TRAILING_BY_SYMBOL pour TREND_UP
FIRST_LOCK_BY_SYMBOL = {
    "XAUUSD": 1.0,  # TRAILING: TREND_UP first lock = 1.0
    "BTCUSD": 1.0,  # TRAILING: TREND_UP first lock = 1.0
    "US500.cash": 0.5,  # TRAILING: TREND_UP first lock = 0.5
    "EURUSD": 1.0,  # TRAILING: TREND_UP first lock = 1.0
    "USDJPY": 0.80,
    "GBPUSD": 0.80,
    "AUDUSD": 0.80,
    "USDCAD": 0.80,
}


def get_first_lock_atr(symbol: str) -> float:
    """Retourne le premier lock ATR pour un symbole donné.

    Args:
        symbol: nom du symbole (ex: "XAUUSD")

    Returns:
        float: premier lock en multiples d'ATR (ex: 0.8 pour XAUUSD)
    """
    return FIRST_LOCK_BY_SYMBOL.get(symbol, FIRST_LOCK_ATR)
