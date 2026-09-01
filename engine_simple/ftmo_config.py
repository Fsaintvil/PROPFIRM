"""Configuration du FTMO Protector — niveaux de trailing, buffers BE, constants.

Extrait de ftmo_protector.py pour réduire la god class.
Calibration spécifique par actif (Juin 2026).
"""

# Import défensif : config_simple peut être en cours de chargement (cycle
# d'import). Fallback = 4 (valeur production.yaml) si indisponible.
try:
    import config_simple as cfg

    _MAX_POS_PER_SYMBOL_DEFAULT = cfg.MAX_POSITIONS_PER_SYMBOL
except Exception:  # pragma: no cover — dépendance d'import
    _MAX_POS_PER_SYMBOL_DEFAULT = 4

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
    # XAUUSD H4 — Or (FIX 6 Juillet 2026 — TRAILING SERRÉ)
    # Lock 0.6-1.0×ATR selon régime (était 1.5-2.0×ATR). L'ATR H4≈$20 signifie
    # qu'un gain de +$200 à lot 0.10 = 1.0×ATR. Le trailing doit verrouiller tôt
    # pour protéger les gains sur ce symbole à forte WR (73% backtest).
    # ═══════════════════════════════════════════════════════════════════════
    "XAUUSD": {
        # 🔧 FIX 21 Juillet 2026: Premier lock AUGMENTÉ à 1.50×ATR (était 1.00×ATR)
        # Cause: Le trailing 0.50×ATR sur H4 (ATR≈$20) s'activait à +$10 —
        # les wicks H4 normaux ($10-15) stopaient les trades avant d'atteindre
        # le TP 6.0×ATR ($120). Solution: lock à 1.5×ATR = +$30, marge de $20
        # pour laisser respirer.
        "TREND_UP": [(1.50, 0.80), (3.00, 0.50), (5.00, 0.30), (7.00, 0.15)],
        "TREND_DOWN": [(1.50, 0.80), (3.00, 0.50), (5.00, 0.30), (7.00, 0.15)],
        "RANGING": [(1.20, 0.55), (2.50, 0.35), (4.00, 0.20), (6.00, 0.10)],
        "HIGH_VOL": [(1.50, 1.00), (3.00, 0.65), (5.00, 0.40), (7.00, 0.20)],
        "LOW_VOL": [(1.00, 0.50), (2.00, 0.30), (3.00, 0.18), (5.00, 0.08)],
    },
    # ═══════════════════════════════════════════════════════════════════════
    # BTCUSD H1 — Bitcoin (Juin 2026 — AJUSTÉ)
    # Lock réduit à 1.0×ATR (était 1.5×ATR, jugé trop long par l'opérateur).
    # Trailing crypto modéré : laisse respirer mais verrouille plus tôt.
    # Avec ATR≈$493, lock à 1.0 = activation après ~$493 de mouvement.
    # ═══════════════════════════════════════════════════════════════════════
    "BTCUSD": {
        # 🔧 FIX 24 Juillet 2026: ÉLARGISSEMENT BTCUSD (W/L=0.84, -$266)
        # Bitcoin H1 ATR≈$493. Premier lock 2.50×ATR=$1232 (était 2.00×ATR=$986).
        # N1 trail 1.50×ATR=$740 (était 1.00×ATR=$493). Objectif: laisser les
        # trades BTC respirer les wicks crypto ($200-400+). Avec TP=5.0×ATR=$2465,
        # les trades ont assez de place pour atteindre leur cible.
        "TREND_UP": [(2.50, 1.50), (4.00, 0.80), (6.00, 0.50), (9.00, 0.25)],
        "TREND_DOWN": [(2.50, 1.50), (4.00, 0.80), (6.00, 0.50), (9.00, 0.25)],
        "RANGING": [(2.00, 1.00), (3.50, 0.60), (5.00, 0.35), (7.00, 0.18)],
        "HIGH_VOL": [(2.50, 1.50), (4.00, 0.90), (6.00, 0.55), (9.00, 0.30)],
        "LOW_VOL": [(2.00, 1.00), (3.00, 0.50), (4.50, 0.30), (6.50, 0.15)],
    },
    # ═══════════════════════════════════════════════════════════════════════
    # SOLUSD H1 — Solana (1 Sept 2026 — TRAILING RELÂCHÉ)
    # Le fallback RANGING (lock 1.80, trail 0.80) est trop serré pour le crypto.
    # SOLUSD ATR H1 ≈ $1.5-3.0, lock 1.80×ATR = $2.7-5.4 → wicks crypto normaux.
    # Nouveau: lock 2.00×ATR, trail 1.20×ATR — laisse les trades respirer.
    # ═══════════════════════════════════════════════════════════════════════
    "SOLUSD": {
        "TREND_UP": [(2.00, 1.20), (3.50, 0.70), (5.00, 0.45), (7.00, 0.20)],
        "TREND_DOWN": [(2.00, 1.20), (3.50, 0.70), (5.00, 0.45), (7.00, 0.20)],
        "RANGING": [(1.80, 1.00), (3.00, 0.60), (4.50, 0.35), (6.00, 0.15)],
        "HIGH_VOL": [(2.00, 1.40), (3.50, 0.85), (5.00, 0.55), (7.00, 0.25)],
        "LOW_VOL": [(1.50, 0.90), (2.50, 0.55), (4.00, 0.30), (5.50, 0.12)],
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
# 🔧 FIX 21 Juillet 2026: Trailing RELÂCHÉ — premiers locks repoussés
# Cause racine: Le trailing 1.5×ATR avec SL=0.80×ATR signifiait qu'un retracement
# de 0.80×ATR (ex: $16 sur XAUUSD H4) stoppait un trade avant d'atteindre le TP.
# Les trades gagnants étaient stoppés sur du bruit H4 (wicks de $10-15).
# Solution: lock à 2.0×ATR, SL=1.00×ATR — besoin de 1.0×ATR de retracement
# pour stopper. Objectif: laisser les trades atteindre 2:1 RR avant activation.
TRAILING_BY_REGIME = {
    # 🔧 FIX 22 Juillet 2026: Premier lock 2.00→1.50×ATR pour TREND_UP/DOWN/HIGH_VOL
    # Raison: Le lock à 2.00×ATR laissait trop de profit non-verrouillé.
    # Exemple USOIL: ATR=$0.624 → 2.00×ATR=$1.248 de profit nécessaire pour activer
    # le trailing, les trades perdaient $1+ de gain avant verrouillage.
    # Nouveau: 1.50×ATR=$0.936 — active le trailing plus tôt, préserve plus de gains.
    # Note: XAUUSD/BTCUSD ont leurs propres réglages dans TRAILING_BY_SYMBOL,
    # ce changement n'affecte QUE les symboles sans config spécifique (fallback).
    # 🔧 30 Juil 2026: TRAILING SERRÉ (PROFESSIONAL SOLUTION)
    # Avec WR 35% et Partial TP à 40%, il faut verrouiller les gains PLUS tôt.
    # Premier lock 1.20×ATR au lieu de 1.50×ATR — protège +33% de gain dès le départ.
    # Niveaux plus agressifs: trail_distance réduite pour tous les paliers.
    # 🔧 30 Juil 2026 (v2): Ajout palier N1.5 à 1.80×ATR — le gap N1(1.20)→N2(2.50)
    # de 1.30×ATR laissait le même trail lâche (0.80×ATR) trop longtemps. N1.5 resserre
    # à 0.60×ATR dès 1.80×ATR de profit, protégeant 33% de gain supplémentaire.
    # 🔧 31 Juil 2026 (Quant Auditor — R2): TRAILING RELÂCHÉ — revert vers la config
    # du 21-22 Juillet validée en backtest. La config serrée du 30/07 a été calibrée
    # sur WR 35% corrompue (direction inversée dans le CSV). Preuve: 62.4% des gagnants
    # sortent à <0.5R, 95% n'atteignent jamais le TP, payout 1.41 < breakeven 1.55.
    # N1 lock 1.20→1.80×ATR, trails 0.55-0.90→0.80-1.20×ATR: laisse les gagnants respirer
    # jusqu'au partial TP (65% du TP) avant de verrouiller.
    "TREND_UP": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30), (6.00, 0.20)],
    "TREND_DOWN": [(1.80, 1.00), (2.50, 0.70), (3.50, 0.50), (5.00, 0.30), (6.00, 0.20)],
    "RANGING": [(1.80, 0.80), (2.50, 0.55), (3.50, 0.40), (5.00, 0.25), (5.50, 0.15)],
    "HIGH_VOL": [(1.80, 1.20), (2.50, 0.90), (3.50, 0.65), (5.00, 0.40), (6.00, 0.25)],
    "LOW_VOL": [(1.80, 0.70), (2.50, 0.50), (3.20, 0.35), (4.50, 0.20), (5.50, 0.12)],
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
        "TREND_UP": 0.35,  # or: buffer serré en trending
        "TREND_DOWN": 0.35,
        "RANGING": 0.50,  # ranging: modéré
        "HIGH_VOL": 0.60,  # haute vol: large
        "LOW_VOL": 0.25,  # basse vol: très serré
    },
    "BTCUSD": {
        "TREND_UP": 0.45,  # crypto: buffer modéré
        "TREND_DOWN": 0.45,
        "RANGING": 0.60,  # ranging: modéré
        "HIGH_VOL": 0.80,  # haute vol: large
        "LOW_VOL": 0.35,  # basse vol: serré
    },
    # "US500.cash": {  # DÉSACTIVÉ — PF 0.39 toxique (25 Juin 2026)
    #     "TREND_UP": 0.60,  # indice: standard
    #     "TREND_DOWN": 0.60,
    #     "RANGING": 0.80,  # ranging: large
    #     "HIGH_VOL": 1.00,  # haute vol: très large
    #     "LOW_VOL": 0.50,  # basse vol: serré
    # },
}

# Fallback par défaut
# 🔧 30 Juillet 2026: Buffers BE réduits — 0.60→0.35×ATR pour trending
# Raison: buffer 0.60×ATR laissait $0.55 de profit non sécurisé sur USOIL après
# partial TP. Avec le nouveau N1.5 à 1.80×ATR et le BE progressif, le trailing
# protège déjà le trade — le buffer BE peut être plus serré.
BE_BUFFER_BY_REGIME = {
    "TREND_UP": 0.35,
    "TREND_DOWN": 0.35,
    "RANGING": 0.50,
    "HIGH_VOL": 0.60,
    "LOW_VOL": 0.30,
}


def get_be_buffer_for_symbol(symbol: str, regime: str) -> float:
    """Retourne le buffer BE pour un symbole et un régime donné."""
    sym_buffer = BE_BUFFER_BY_SYMBOL.get(symbol)
    if sym_buffer and regime in sym_buffer:
        return sym_buffer[regime]
    return BE_BUFFER_BY_REGIME.get(regime, 0.60)


# ═══════════════════════════════════════════════════════════════════════
# NO TRAILING SYMBOLS — 27 Juillet 2026 (Solution A)
# Ces symboles n'ont PAS de trailing ni de partial TP.
# Leur stratégie est optimisée pour FTMO : threshold 4.0×ATR,
# SL 1.5×ATR, TP 6.0×ATR. Le trailing détruisait la performance
# (PF 1.04→1.39, DD 45%→5.5% sans trailing).
# ═══════════════════════════════════════════════════════════════════════
NO_TRAILING_SYMBOLS: set = {"US500.cash", "US100.cash", "JP225.cash"}


def is_trailing_disabled(symbol: str) -> bool:
    """Retourne True si le trailing et partial TP sont désactivés pour ce symbole."""
    return symbol in NO_TRAILING_SYMBOLS


# Durée de validité du cache ATR en secondes
ATR_CACHE_TTL = 60

# Seuils de trailing par défaut
FIRST_LOCK_ATR = (
    0.8  # premier lock du trailing (fallback si symbole non trouvé) — 10 Juil 2026: ↑ 0.5→0.8 pour laisser respirer
)

# Per-symbol risk_mult cap — 27 symboles (fix M12: étendu 1er Juillet 2026)
RISK_MULT_CAP = {
    "XAUUSD": 1.50,
    "BTCUSD": 1.25,
    "US30.cash": 1.30,
    "ETHUSD": 1.15,
    "US100.cash": 1.20,
    "US500.cash": 1.15,
    "XAGUSD": 1.10,
    "EURUSD": 1.15,
    "GBPUSD": 1.15,
    "USDJPY": 2.00,  # 🚀 16 Juil: ↑ 1.15→2.00 — meilleur symbole, WR 61% stable
    "USDCAD": 1.15,
    "AUDUSD": 1.15,
    "NZDUSD": 1.15,
    "USDCHF": 1.15,
    "EURJPY": 1.10,
    "GBPJPY": 1.10,
    "EURGBP": 1.10,
    "AUDJPY": 1.50,  # 🚀 16 Juil: ↑ 1.10→1.50 — débloqué, WR 55.6%
    "USOIL.cash": 2.00,  # 🚀 16 Juil: ↑ 1.10→2.00 — PF 5.30, +$33
    "UKOIL.cash": 1.10,
    "NATGAS.cash": 1.05,
    "SOLUSD": 1.10,
    "BNBUSD": 1.10,
    "JP225.cash": 1.15,
    "GER40.cash": 1.15,
    "UK100.cash": 1.15,
}

# Per-symbol max positions — 27 symboles (fix M12: valeur globale depuis YAML)
# 🐛 FIX 16 Août 2026 (Data Manager): était hardcodé à 6 alors que la config
# réelle MAX_POSITIONS_PER_SYMBOL = 4 (default.yaml 3 / production.yaml 4).
# Le portfolio_controller bloquait déjà à 4 en aval, mais le pipeline autorisait
# 6 → divergence silencieuse (signaux générés puis rejetés). Aligné sur la
# config effective pour une source unique de vérité.
MAX_POS_PER_SYMBOL = {
    sym: _MAX_POS_PER_SYMBOL_DEFAULT
    for sym in [
        "XAUUSD",
        "BTCUSD",
        "US30.cash",
        "ETHUSD",
        "US100.cash",
        "US500.cash",
        "XAGUSD",
        "EURUSD",
        "GBPUSD",
        "USDJPY",
        "USDCAD",
        "AUDUSD",
        "NZDUSD",
        "USDCHF",
        "EURJPY",
        "GBPJPY",
        "EURGBP",
        "AUDJPY",
        "USOIL.cash",
        "UKOIL.cash",
        "NATGAS.cash",
        "SOLUSD",
        "BNBUSD",
        "JP225.cash",
        "GER40.cash",
        "UK100.cash",
    ]
}

# ============================================================================
# CENTRAL BYPASS — Cap max_per_symbol par symbole
# ============================================================================
# 🔧 FIX 20 Août 2026 (Auto-Fixer): le bypass central du signal_pipeline
# (score ≥ 0.90 + raw_mom ≥ 0.85) hardcodait max_per_symbol = 4 → jusqu'à 4
# positions du MÊME signal sur le MÊME symbole. Les signaux MOM20x3 persistent
# tant qu'ils sont actifs (rejoués chaque cycle 15s) → doublons intra-symbole
# → pertes multipliées (XAUUSD BUY 02:02 + 02:07 → 2 SL = -338$ le 20/08).
# Ce dict plafonne le bypass par symbole. Défaut = 4 (comportement historique).
BYPASS_MAX_PER_SYMBOL = {
    "XAUUSD": 1,  # SL 1.5×ATR serré → 1 position max par signal (doublon = risque doublé)
}

# ============================================================================
# DD THRESHOLDS — Risk reduction levels
# ============================================================================
DD_REDUCE_THRESHOLD = 0.05  # 5% DD → risk × (1 - dd_peak)
DD_CRITICAL_THRESHOLD = 0.07  # 7% DD → risk × 0.20 (aggressive reduction)
DD_AUTODISABLE_THRESHOLD = 0.20  # 20% WR → auto-disable symbol

# ============================================================================
# PULLBACK FILTER — Score threshold for pullback enforcement
# ============================================================================
# 🔧 FIX 21 Juillet 2026: ↑ 0.50→0.55 — le seuil 0.50 était trop bas,
# laissait passer des signaux faibles sans pullback confirme.
# Le pullback filtre les entrees "etendues" loin de EMA20, ce qui reduit
# le risque d'entrer en fin de trend.
PULLBACK_FILTER_SCORE_THRESHOLD = 0.55  # ↑ 0.50→0.55 pour plus de securite

# Premier lock par symbole — uniquement 3 symboles actifs
# Les symboles inactifs utilisent FIRST_LOCK_ATR (0.5) comme fallback
FIRST_LOCK_BY_SYMBOL = {
    "XAUUSD": 1.0,  # Or: lock unifié à 1.0×ATR
    "BTCUSD": 1.0,  # Bitcoin: lock à 1.0×ATR (risk_mult réduit à 0.20)
}


def get_first_lock_atr(symbol: str) -> float:
    """Retourne le premier lock ATR pour un symbole donné.

    Args:
        symbol: nom du symbole (ex: "XAUUSD")

    Returns:
        float: premier lock en multiples d'ATR (ex: 0.8 pour XAUUSD)
    """
    return FIRST_LOCK_BY_SYMBOL.get(symbol, FIRST_LOCK_ATR)


# ============================================================================
# MAX TOTAL LOTS — Anti-runaway guard
# ============================================================================
# Limite le volume total maximum de toutes les positions ouvertes combinées.
# Protège contre les bugs qui rendent le robot aveugle à ses propres positions
# (ex: tuple bug positions_get → 91 positions au lieu de 18).
# Une fois ce seuil atteint, calculate_lot() retourne min_lot pour tout nouveau trade.
# ============================================================================
MAX_TOTAL_LOTS = 2.0  # volume total max (ex: 20 positions × 0.10 = 2.0)

# ============================================================================
# SYMBOL MAX RISK — Per-symbol risk_mult override (hard blocks)
# ============================================================================
# Utilisé par OnlineLearner (get_params + _update_params) pour plafonner
# le risk_mult par symbole. Un hard block à 0.0 désactive complètement
# le symbole pour l'OL.
# 🚫 VIDÉ 04 Aout 2026 (Robot Manager) — DÉGEL TOTAL.
# Ce mécanisme n'existait PAS au pic (commit 4011b396b, 23 Juin). Il a été
# ajouté le 10 Juillet 2026 et capait les FX à 0.5-0.6, bridant les risk_mult
# du pic (EURUSD 1.00, USDJPY 1.00, GBPUSD 0.90, USDCAD 0.85, AUDUSD 0.75).
# Vidé pour restaurer le comportement pic: l'OL utilise les risk_mult config
# + plancher 0.60. Voir _update_params (risk_mult = max(0.60, ...)).
# NOTE: les symboles NON listés ici utilisent le plancher OL 0.60.
# ============================================================================
SYMBOL_MAX_RISK = {
    # ═══════════════════════════════════════════════════════════════════════
    # DÉGEL TOTAL 04 Aout 2026 — dict VIDÉ (comportement pic restauré).
    # L'OL applique le plancher 0.60 et les risk_mult de la config YAML.
    # ═══════════════════════════════════════════════════════════════════════
}
