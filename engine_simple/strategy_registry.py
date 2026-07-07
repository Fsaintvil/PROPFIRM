"""Strategy Registry — mapping des stratégies par symbole.

Chaque symbole peut avoir une stratégie différente.
La stratégie par défaut est MOM20x3 pour tous les symboles.

Extension:
    Pour ajouter une nouvelle stratégie:
    1. Créer le fichier engine_simple/strategy_<nom>.py
    2. Ajouter la classe dans STRATEGY_CLASSES
    3. Ajouter le module dans STRATEGY_MODULES
    4. Définir le mapping dans SYMBOL_STRATEGY_MAP

Utilisation:
    >>> from engine_simple.strategy_registry import get_strategy_for
    >>> get_strategy_for("XAUUSD")
    'TrendFollow'
    >>> get_strategy_for("EURUSD")
    'MOM20x3'
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# SYMBOL_STRATEGY_MAP — assigne une stratégie à chaque symbole
# ═══════════════════════════════════════════════════════════════════════════════
# Format: "SYMBOLE": "NomDeLaStrategie"
# La valeur par défaut pour tout symbole non listé est "MOM20x3".
#
# Pour activer une stratégie différente pour un symbole, changez la valeur ici.
# Exemple: "XAUUSD": "TrendFollow"  → utilise strategy_trend_follow.py
# ═══════════════════════════════════════════════════════════════════════════════

SYMBOL_STRATEGY_MAP: dict[str, str] = {
    # ── PHASE 3: 7 symboles actifs ─────────────────────────────────────────
    "USDJPY": "MOM20x3",
    "XAUUSD": "TrendFollow",  # ✅ Activé 8 Juillet 2026 après validation backtest
    "JP225.cash": "MOM20x3",
    "EURUSD": "MOM20x3",
    "GBPJPY": "MOM20x3",
    "AUDJPY": "MOM20x3",
    "EURGBP": "MOM20x3",
    # ── Symboles inactifs (conservés pour référence) ───────────────────────
    "BTCUSD": "MOM20x3",
    "ETHUSD": "MOM20x3",
    "XAGUSD": "MOM20x3",
    "AUDUSD": "MOM20x3",
    "USDCAD": "MOM20x3",
    "NZDUSD": "MOM20x3",
    "USDCHF": "MOM20x3",
    "GBPUSD": "MOM20x3",
    "EURJPY": "MOM20x3",
    "US500.cash": "MOM20x3",
    "US100.cash": "MOM20x3",
    "US30.cash": "MOM20x3",
    "GER40.cash": "MOM20x3",
    "UK100.cash": "MOM20x3",
    "SOLUSD": "MOM20x3",
    "LNKUSD": "MOM20x3",
    "BNBUSD": "MOM20x3",
    "USOIL.cash": "MOM20x3",
    "UKOIL.cash": "MOM20x3",
    "NATGAS.cash": "MOM20x3",
}

# ═══════════════════════════════════════════════════════════════════════════════
# STRATEGY_MODULES — mapping nom de stratégie → module Python
# ═══════════════════════════════════════════════════════════════════════════════
# Utilisé pour l'import dynamique dans signal_pipeline.py
# Les stratégies built-in (MOM20x3, MR) sont importées directement.

STRATEGY_MODULES: dict[str, str] = {
    "MOM20x3": "engine_simple.strategy",
    "TrendFollow": "engine_simple.strategy_trend_follow",
    "MeanReversion": "",  # Gérée séparément dans signal_pipeline._generate_mr_signal()
}

# ═══════════════════════════════════════════════════════════════════════════════
# STRATEGY_CLASSES — mapping nom de stratégie → nom de la classe
# ═══════════════════════════════════════════════════════════════════════════════

STRATEGY_CLASSES: dict[str, str] = {
    "MOM20x3": "MOM20x3",
    "TrendFollow": "TrendFollow",
}

# ═══════════════════════════════════════════════════════════════════════════════
# API publique
# ═══════════════════════════════════════════════════════════════════════════════


def get_strategy_for(symbol: str) -> str:
    """Retourne le nom de la stratégie pour un symbole donné.

    Args:
        symbol: Nom du symbole (ex: "XAUUSD")

    Returns:
        Nom de la stratégie (ex: "MOM20x3", "TrendFollow")

    Note:
        Tout symbole non listé dans SYMBOL_STRATEGY_MAP retourne "MOM20x3".
    """
    return SYMBOL_STRATEGY_MAP.get(symbol, "MOM20x3")


def get_strategy_module(strategy_name: str) -> str | None:
    """Retourne le module Python pour une stratégie donnée.

    Args:
        strategy_name: Nom de la stratégie (ex: "TrendFollow")

    Returns:
        Chemin du module (ex: "engine_simple.strategy_trend_follow")
        None si la stratégie n'a pas de module dédié (ex: MeanReversion)
    """
    return STRATEGY_MODULES.get(strategy_name)


def get_strategy_class_name(strategy_name: str) -> str:
    """Retourne le nom de la classe pour une stratégie donnée.

    Args:
        strategy_name: Nom de la stratégie

    Returns:
        Nom de la classe (ex: "TrendFollow")
        "MOM20x3" par défaut si la stratégie n'est pas trouvée
    """
    return STRATEGY_CLASSES.get(strategy_name, "MOM20x3")


def set_strategy_for(symbol: str, strategy_name: str) -> None:
    """Modifie la stratégie pour un symbole (à chaud).

    Args:
        symbol: Nom du symbole
        strategy_name: Nom de la stratégie

    Example:
        >>> set_strategy_for("XAUUSD", "TrendFollow")
        >>> get_strategy_for("XAUUSD")
        'TrendFollow'
    """
    old = SYMBOL_STRATEGY_MAP.get(symbol, "MOM20x3")
    SYMBOL_STRATEGY_MAP[symbol] = strategy_name
    logger.info(f"[STRATEGY REGISTRY] {symbol}: {old} → {strategy_name}")


def get_all_strategies() -> dict[str, str]:
    """Retourne le mapping complet symbole → stratégie."""
    return dict(SYMBOL_STRATEGY_MAP)


def validate_registry() -> list[str]:
    """Valide que tous les symboles actifs ont une stratégie valide.

    Returns:
        Liste des erreurs (vide si tout est OK)
    """
    errors = []
    valid_strategies = set(STRATEGY_CLASSES.keys())
    for symbol, strategy in SYMBOL_STRATEGY_MAP.items():
        if strategy not in valid_strategies:
            errors.append(f"{symbol}: stratégie '{strategy}' inconnue")
    return errors
