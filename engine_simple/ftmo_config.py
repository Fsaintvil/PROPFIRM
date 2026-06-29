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
    # US500.cash — DÉSACTIVÉ 25 Juin 2026 (PF 0.39 toxique)
    # ═══════════════════════════════════════════════════════════════════════
    # ═══════════════════════════════════════════════════════════════════════
    # Symboles REACTIVÉS 29 Juin 2026 — High Confidence Only (≥90%)
    # EURUSD, GBPUSD, USDJPY, USDCAD, AUDUSD, NZDUSD, USDCHF
    # → utilisent TRAILING_BY_REGIME comme fallback (standard trailing)
    # ═══════════════════════════════════════════════════════════════════════
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

# Per-symbol risk_mult cap — uniquement 3 symboles actifs
RISK_MULT_CAP = {
    "XAUUSD": 1.25,
    "BTCUSD": 1.00,
    "US30.cash": 1.00,  # risk_mult YAML=0.50, cap=1.0 = pas de plafond
}

# Per-symbol max positions — uniquement 3 symboles actifs
# Les symboles inactifs utilisent le fallback dans ftmo_protector.py
MAX_POS_PER_SYMBOL = {
    "XAUUSD": 4,
    "BTCUSD": 4,
    "US30.cash": 4,  # limité par max_positions_per_symbol=4
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
PULLBACK_FILTER_SCORE_THRESHOLD = 0.50  # ↓ 0.60→0.50 pour + de trades (plus de signaux sans pullback)

# Premier lock par symbole — uniquement 3 symboles actifs
# Les symboles inactifs utilisent FIRST_LOCK_ATR (0.5) comme fallback
FIRST_LOCK_BY_SYMBOL = {
    "XAUUSD": 1.0,  # TRAILING: TREND_UP first lock = 1.0
    "BTCUSD": 1.0,  # TRAILING: TREND_UP first lock = 1.0
    "US30.cash": 0.5,  # TRAILING: TREND_UP first lock = 0.5 (harmonisé US500.cash)
}


def get_first_lock_atr(symbol: str) -> float:
    """Retourne le premier lock ATR pour un symbole donné.

    Args:
        symbol: nom du symbole (ex: "XAUUSD")

    Returns:
        float: premier lock en multiples d'ATR (ex: 0.8 pour XAUUSD)
    """
    return FIRST_LOCK_BY_SYMBOL.get(symbol, FIRST_LOCK_ATR)
