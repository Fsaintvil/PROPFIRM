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
    # XAUUSD H4 — Or (Juin 2026)
    # Trailing plus serré en trending (tendances H4 longues → protéger tôt)
    #   Lock à 0.8×ATR (vs 1.0) : tendances longues → verrouiller tôt
    #   Trailing n1 0.60 (vs 0.70) : plus serré
    #   En ranging: large pour éviter faux signaux
    # ═══════════════════════════════════════════════════════════════════════
    "XAUUSD": {
        "TREND_UP":    [(0.80, 0.60), (2.00, 0.40), (3.00, 0.25), (5.00, 0.10)],
        "TREND_DOWN":  [(0.80, 0.60), (2.00, 0.40), (3.00, 0.25), (5.00, 0.10)],
        "RANGING":     [(0.80, 0.50), (2.00, 0.35), (3.00, 0.20), (5.00, 0.10)],
        "HIGH_VOL":    [(0.80, 0.80), (2.00, 0.55), (3.00, 0.40), (5.00, 0.20)],
        "LOW_VOL":     [(0.80, 0.35), (2.00, 0.22), (3.00, 0.15), (5.00, 0.08)],
    },
    # ═══════════════════════════════════════════════════════════════════════
    # BTCUSD H1 — Bitcoin (Juin 2026)
    # Trailing LARGE pour laisser bouger (crypto = volatilité extrême)
    #   Lock à 1.5×ATR (vs 1.0) : laisser le trade respirer
    #   Trailing n1 1.00 (vs 0.90) : très large
    #   Flash crashes nécessitent un trailing lâche
    # ═══════════════════════════════════════════════════════════════════════
    "BTCUSD": {
        "TREND_UP":    [(1.50, 1.00), (3.00, 0.70), (4.00, 0.50), (6.00, 0.30)],
        "TREND_DOWN":  [(1.50, 1.00), (3.00, 0.70), (4.00, 0.50), (6.00, 0.30)],
        "RANGING":     [(1.50, 0.75), (3.00, 0.55), (4.00, 0.40), (6.00, 0.20)],
        "HIGH_VOL":    [(1.50, 1.20), (3.00, 0.90), (4.00, 0.65), (6.00, 0.35)],
        "LOW_VOL":     [(1.50, 0.60), (3.00, 0.40), (4.00, 0.25), (6.00, 0.12)],
    },
    # ═══════════════════════════════════════════════════════════════════════
    # US500.cash H4 — S&P 500 (Juin 2026 — MIGRÉ DE H1)
    # Trailing standard, lock plus tôt (H4 → protéger tôt)
    #   Lock à 0.8×ATR (vs 1.0) : tendances H4 → verrouiller tôt
    #   Trailing plus serré qu'en H1 (H4 moins de bruit)
    # ═══════════════════════════════════════════════════════════════════════
    "US500.cash": {
        "TREND_UP":    [(0.80, 0.50), (2.00, 0.30), (3.00, 0.20), (5.00, 0.10)],
        "TREND_DOWN":  [(0.80, 0.50), (2.00, 0.30), (3.00, 0.20), (5.00, 0.10)],
        "RANGING":     [(0.80, 0.40), (2.00, 0.25), (3.00, 0.15), (5.00, 0.08)],
        "HIGH_VOL":    [(0.80, 0.70), (2.00, 0.50), (3.00, 0.35), (5.00, 0.20)],
        "LOW_VOL":     [(0.80, 0.30), (2.00, 0.18), (3.00, 0.12), (5.00, 0.06)],
    },
}

# Fallback par défaut (ancien comportement)
TRAILING_BY_REGIME = {
    "TREND_UP":    [(1.00, 0.80), (2.00, 0.50), (3.00, 0.30), (5.00, 0.15)],
    "TREND_DOWN":  [(1.00, 0.80), (2.00, 0.50), (3.00, 0.30), (5.00, 0.15)],
    "RANGING":     [(1.00, 0.50), (2.00, 0.35), (3.00, 0.20), (5.00, 0.10)],
    "HIGH_VOL":    [(1.00, 1.00), (2.00, 0.70), (3.00, 0.50), (5.00, 0.25)],
    "LOW_VOL":     [(1.00, 0.40), (2.00, 0.25), (3.00, 0.15), (5.00, 0.08)],
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
        "TREND_UP": 0.55,   # or: buffer serré en trending
        "TREND_DOWN": 0.55,
        "RANGING": 0.75,    # ranging: plus large
        "HIGH_VOL": 0.90,   # haute vol: très large
        "LOW_VOL": 0.45,    # basse vol: serré
    },
    "BTCUSD": {
        "TREND_UP": 0.70,   # crypto: buffer large
        "TREND_DOWN": 0.70,
        "RANGING": 0.85,    # ranging: très large
        "HIGH_VOL": 1.10,   # haute vol: extrêmement large
        "LOW_VOL": 0.50,    # basse vol: standard
    },
    "US500.cash": {
        "TREND_UP": 0.60,   # indice: standard
        "TREND_DOWN": 0.60,
        "RANGING": 0.80,    # ranging: large
        "HIGH_VOL": 1.00,   # haute vol: très large
        "LOW_VOL": 0.50,    # basse vol: serré
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
FIRST_LOCK_ATR = 1.0  # premier lock du trailing (fallback si symbole non trouvé)

# Premier lock par symbole — Juin 2026 Calibration
# XAUUSD H4: 0.8×ATR (tendances H4 longues → protéger tôt)
# BTCUSD H1: 1.5×ATR (crypto volatile → laisser respirer)
# US500.cash H4: 0.8×ATR (H4 → lock tôt)
FIRST_LOCK_BY_SYMBOL = {
    "XAUUSD": 0.8,
    "BTCUSD": 1.5,
    "US500.cash": 0.8,
}


def get_first_lock_atr(symbol: str) -> float:
    """Retourne le premier lock ATR pour un symbole donné.
    
    Args:
        symbol: nom du symbole (ex: "XAUUSD")
    
    Returns:
        float: premier lock en multiples d'ATR (ex: 0.8 pour XAUUSD)
    """
    return FIRST_LOCK_BY_SYMBOL.get(symbol, FIRST_LOCK_ATR)
