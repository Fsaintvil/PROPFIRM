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
    # XAUUSD H4 — Or (FIX 6 Juillet 2026 — TRAILING SERRÉ)
    # Lock 0.6-1.0×ATR selon régime (était 1.5-2.0×ATR). L'ATR H4≈$20 signifie
    # qu'un gain de +$200 à lot 0.10 = 1.0×ATR. Le trailing doit verrouiller tôt
    # pour protéger les gains sur ce symbole à forte WR (73% backtest).
    # ═══════════════════════════════════════════════════════════════════════
    "XAUUSD": {
        # 🔧 FIX 10 Juillet 2026: Premier lock AUGMENTÉ à 1.00×ATR (était 0.60×ATR)
        # Cause: WR live 30-52% bien en-dessous du backtest 60-70%. Le trailing 0.60×ATR
        # sur H4 (ATR≈$20) s'activait à +$12 — trop serré pour les wicks H4.
        # Les trades étaient stopés sur du bruit avant d'atteindre le TP.
        # Solution: lock à 1.0×ATR = +$20 sur lot 0.01, laisse respirer.
        "TREND_UP": [(1.00, 0.50), (2.00, 0.35), (4.00, 0.20), (6.00, 0.10)],
        "TREND_DOWN": [(1.00, 0.50), (2.00, 0.35), (4.00, 0.20), (6.00, 0.10)],
        "RANGING": [(0.80, 0.40), (1.50, 0.25), (3.00, 0.15), (5.00, 0.08)],
        "HIGH_VOL": [(1.00, 0.60), (2.50, 0.40), (4.00, 0.25), (6.00, 0.12)],
        "LOW_VOL": [(0.60, 0.30), (1.20, 0.20), (2.50, 0.12), (4.00, 0.06)],
    },
    # ═══════════════════════════════════════════════════════════════════════
    # BTCUSD H1 — Bitcoin (Juin 2026 — AJUSTÉ)
    # Lock réduit à 1.0×ATR (était 1.5×ATR, jugé trop long par l'opérateur).
    # Trailing crypto modéré : laisse respirer mais verrouille plus tôt.
    # Avec ATR≈$493, lock à 1.0 = activation après ~$493 de mouvement.
    # ═══════════════════════════════════════════════════════════════════════
    "BTCUSD": {
        # 🔧 FIX 10 Juillet 2026: Premier lock RAPPROCHÉ à 1.50×ATR (était 2.00)
        # Cause: Bitcoin WR live 48%, perf -$168. Le 2.0×ATR était trop large pour H1,
        # les trades gagnants repartaient en perte avant verrouillage.
        # Avec ATR≈$493, 1.50×ATR = verrouillage à +$740 au lieu de +$986.
        "TREND_UP": [(1.50, 1.00), (3.00, 0.65), (5.00, 0.35), (8.00, 0.18)],
        "TREND_DOWN": [(1.50, 1.00), (3.00, 0.65), (5.00, 0.35), (8.00, 0.18)],
        "RANGING": [(1.20, 0.80), (2.50, 0.50), (4.00, 0.30), (6.00, 0.15)],
        "HIGH_VOL": [(1.50, 1.20), (3.00, 0.80), (5.00, 0.50), (8.00, 0.25)],
        "LOW_VOL": [(1.00, 0.60), (2.00, 0.40), (4.00, 0.22), (6.00, 0.12)],
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
    # 🔧 OPTIMIZER 2 Juillet 2026: First lock repoussé de 1.5→2.0×ATR (TREND)
    # Cause racine: Quant Auditor a montré RR réalisé=0.85 vs cible 2.0.
    # Le trailing activé à 1.5×ATR avec SL à 1.0×ATR du peak signifiait
    # qu'un retracement de seulement 0.5×ATR stoppait le trade avant d'atteindre
    # le TP 5.0×ATR. Solution: lock à 2.0×ATR, SL=1.0×ATR → besoin de 1.0×ATR
    # de retracement pour stopper. Objectif: laisser les trades respirer jusqu'à
    # au moins 2:1 RR avant d'activer le trailing.
    # 🔧 FIX 10 Juillet 2026: Trailing resserré sur tous les régimes
    # Cause: WR live 30-52%, pertes sur tous les symboles. Le trailing était trop
    # large (2.0×ATR first lock) — les trades gagnants repartaient en perte.
    # Solution: first lock à 1.5×ATR en trending, 1.2×ATR en ranging.
    "TREND_UP": [(1.50, 0.80), (3.00, 0.50), (5.00, 0.30), (7.00, 0.15)],
    "TREND_DOWN": [(1.50, 0.80), (3.00, 0.50), (5.00, 0.30), (7.00, 0.15)],
    "RANGING": [(1.20, 0.40), (2.50, 0.28), (4.00, 0.18), (6.00, 0.10)],
    "HIGH_VOL": [(1.50, 1.00), (3.00, 0.70), (5.00, 0.45), (7.00, 0.25)],
    "LOW_VOL": [(1.00, 0.50), (2.00, 0.32), (3.50, 0.18), (5.00, 0.10)],
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
    # "US500.cash": {  # DÉSACTIVÉ — PF 0.39 toxique (25 Juin 2026)
    #     "TREND_UP": 0.60,  # indice: standard
    #     "TREND_DOWN": 0.60,
    #     "RANGING": 0.80,  # ranging: large
    #     "HIGH_VOL": 1.00,  # haute vol: très large
    #     "LOW_VOL": 0.50,  # basse vol: serré
    # },
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
MAX_POS_PER_SYMBOL = {
    sym: 6
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
# le symbole pour l'OL. Mis à jour 10 Juillet 2026.
# NOTE: les symboles NON listés ici utilisent le plancher 0.60.
# ============================================================================
SYMBOL_MAX_RISK = {
    # ═══════════════════════════════════════════════════════════════════════
    # 🔥 TIER 1 — PERFORMANCE PROUVÉE (PF > 1.2) — risque normal
    # ═══════════════════════════════════════════════════════════════════════
    # USDJPY: 62.5% WR live, PF=1.81, +$13 — seul Forex rentable en réel
    # USOIL.cash: 50% WR, PF=∞, +$22 — surveillance active
    #
    # ═══════════════════════════════════════════════════════════════════════
    # 🟡 TIER 2 — BORDERLINE — risque réduit
    # ═══════════════════════════════════════════════════════════════════════
    # BTCUSD: 48.6% WR, PF=0.81, -$163 — 111 trades, structurellement perdant → cap 50%
    # US500.cash: 46.5% WR, PF=0.53, -$8 — quasi-breakeven → cap 10%
    # US30.cash: 45.5% WR, PF=0.42, -$15 — échantillon limité → cap 30%
    # SOLUSD: pas de données → lot min 0.01
    #
    # ═══════════════════════════════════════════════════════════════════════
    # 🔴 TIER 3 — HARD BLOCK (0.0) — pertes structurelles
    # ═══════════════════════════════════════════════════════════════════════
    # XAGUSD: 33.3% WR, PF=0.40, -$1,265
    # ETHUSD: 29.4% WR, PF=0.29, -$139
    # AUDUSD: 26.1% WR, PF=0.25, -$50
    # US100.cash: 36.4% WR, PF=0.40, -$28
    # GBPUSD: 0% WR, -$26 sur 5 trades
    # EURJPY: 0% WR, -$11 sur 4 trades
    # GBPJPY: 25% WR, PF=0.56, -$15
    # JP225.cash: 53.3% WR, PF=0.53, -$5
    # BNBUSD: pas de données
    # 🔴 13 Juillet 2026 — Tous les Forex hard-blockés (backtest prouve aucun edge après coûts)
    # EURUSD: backtest -$164K, live 0% WR (2 trades)
    # NZDUSD: backtest -$240K, live 57% WR (7 trades)
    # EURGBP: backtest -$141K, live 17% WR (6 trades)
    # AUDJPY: backtest -$167K, live 0% WR (2 trades)
    # USDCAD: backtest -$214K, live 67% WR (3 trades)
    # ============================================================================
    "XAUUSD": 0.0,  # 🔴 HARD BLOCK 16 Juil 2026 — 89% des pertes challenge, WR 0% sur 9 trades live
    "XAGUSD": 0.0,  # 🔴 Hard block — backtest +$37K mais WR live 33%, -$1,265
    "ETHUSD": 0.0,  # 🔴 Hard block — WR live 29.4%
    "AUDUSD": 0.0,  # 🔴 Hard block — Forex sans edge
    "US100.cash": 0.0,  # 🔴 Hard block — allow_shorts=false en YAML
    "GBPUSD": 0.0,  # 🔴 Hard block — Forex sans edge
    "EURJPY": 0.0,  # 🔴 Hard block — Forex sans edge
    "GBPJPY": 0.0,  # 🔴 Hard block — Forex sans edge
    "JP225.cash": 0.0,  # 🔴 Hard block — backtest -$22K après coûts
    "USDCHF": 0.0,  # 🔴 Re-bloqué 14 Juil — WR 16.7%, PF 0.012 (décision CIO + risk-compliance)
    "BNBUSD": 0.0,  # 🔴 Hard block — pas de données
    "EURUSD": 0.30,  # 🔓 Débloqué 16 Juil 2026 — BUY-only, WR 44.8% live (+$28.72), cap 30%
    "NZDUSD": 0.0,  # 🔴 Hard block 13 Juil — backtest -$240K après coûts
    "EURGBP": 1.0,  # 🔓 Débloqué 14 Juil 2026 — live WR 82.8% (29 trades)
    "AUDJPY": 1.0,  # 🔓 Débloqué 16 Juil 2026 — live WR 55.6%, PF 4.40 (+$11.34)
    "USDCAD": 0.0,  # 🔴 Hard block 13 Juil — backtest -$214K après coûts
    # Caps réduits — survivants backtest
    "BTCUSD": 0.50,  # 🟡 Cap 50% — backtest +$232K après coûts (biaisé bull run)
    "US500.cash": 0.10,  # 🟡 Cap 10% — backtest +$11K après coûts
    "US30.cash": 0.30,  # 🟡 Cap 30% — backtest +$71K après coûts
    # USDJPY, USOIL.cash, SOLUSD — PAS hard-blockés (surveillance active)
}
