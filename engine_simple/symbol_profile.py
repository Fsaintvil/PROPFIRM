"""Profil institutionnel des 5 symboles — connaissance approfondie pour le trading

Chaque actif a sa personnalité unique : sessions actives, volatilité typique,
comportement technique, spreads, corrélations. Ce module centralise cette
connaissance pour optimiser les décisions de trading par symbole.

Usage:
    from engine_simple.symbol_profile import get_profile
    profile = get_profile("EURUSD")
    profile.best_sessions   # ["london", "london_ny_overlap"]
    profile.avg_atr_pips    # 85
    profile.sl_atr_recommended  # 1.5 (ranging), 2.0 (trending)
"""
from dataclasses import dataclass, field

# ── Profils institutionnels des 5 actifs ──
# Juin 2026: ETHUSD ajouté (remplace US500.cash retiré pour PF=1.00)

@dataclass
class SymbolInstitutionalProfile:
    """Profil de connaissance institutionnelle pour une paire de devises."""
    symbol: str
    nickname: str
    description: str

    # Volatilité
    avg_atr_pips: float                  # ATR quotidien moyen en pips
    atr_percentile_high: float           # 80e percentile ATR
    atr_percentile_low: float            # 20e percentile ATR
    typical_spread_pts: float            # Spread typique en points
    spread_warning_pts: float            # Seuil spread anormal

    # Sessions optimales (UTC)
    best_sessions: list[str]             # Sessions recommandées
    avoid_sessions: list[str]            # Sessions à éviter
    peak_hours_utc: list[tuple[int, int]]  # Plages horaires les plus actives

    # Comportement technique
    adx_trend_threshold: float           # ADX seuil pour régime TREND (vs 20 par défaut)
    rsi_overbought: float                # Seuil surachat (70 par défaut)
    rsi_oversold: float                  # Seuil survente (30 par défaut)
    bb_std_dev: float                    # Bollinger deviation (2.0 par défaut)
    respects_levels: bool                # True = soutien/résistance techniques fiables
    trend_persistence: str               # "high", "medium", "low"

    # SL/TP recommandés (multiples ATR)
    sl_atr_ranging: float
    sl_atr_trending: float
    tp_atr_ranging: float
    tp_atr_trending: float

    # Trailing (multiples ATR) — override du trailing global par régime
    trailing_profile: dict[str, list[tuple[float, float]]]  # régime → [(profit_lock, trail_distance)]

    # Facteurs de risque
    news_sensitivity: str                # "high", "medium", "low"
    intervention_risk: bool              # SNB, BoJ, etc.
    gap_risk: bool                       # Saut de prix fréquent au weekend

    # Poids dans l'ensemble
    base_weight: float = 1.0             # Pondération par défaut du signal
    spread_cost_factor: float = 1.0      # Facteur de coût de spread (1.0 = normal)

    # Saisonnalité interne
    monthly_strength: dict[int, float] = field(default_factory=dict)  # mois → force relative

    def __post_init__(self):
        self.name = self.symbol


# ── Base de connaissance institutionnelle ──

PROFILES: dict[str, SymbolInstitutionalProfile] = {
    "USDCAD": SymbolInstitutionalProfile(
        symbol="USDCAD",
        nickname="Loonie",
        description="USD/CAD — pétro-devise, tendances propres, bon marché technique",

        avg_atr_pips=70.0,
        atr_percentile_high=95.0,
        atr_percentile_low=45.0,
        typical_spread_pts=12.0,
        spread_warning_pts=30.0,

        best_sessions=["london_ny_overlap", "ny_morning"],
        avoid_sessions=["asia", "asia_london_overlap"],
        peak_hours_utc=[(13, 17), (14, 16)],

        adx_trend_threshold=22.0,          # Legerement plus haut que 20 par défaut
        rsi_overbought=72.0,
        rsi_oversold=28.0,
        bb_std_dev=2.0,
        respects_levels=True,
        trend_persistence="high",

        sl_atr_ranging=1.5,
        sl_atr_trending=2.0,
        tp_atr_ranging=3.5,
        tp_atr_trending=5.0,

        trailing_profile={
            "RANGING":     [(1.0, 0.50), (2.0, 0.35), (3.0, 0.20), (5.0, 0.10)],
            "TREND_UP":    [(1.0, 0.70), (2.0, 0.45), (3.0, 0.25), (5.0, 0.12)],
            "TREND_DOWN":  [(1.0, 0.70), (2.0, 0.45), (3.0, 0.25), (5.0, 0.12)],
            "HIGH_VOL":    [(1.0, 0.90), (2.0, 0.60), (3.0, 0.40), (5.0, 0.20)],
            "LOW_VOL":     [(1.0, 0.35), (2.0, 0.22), (3.0, 0.12), (5.0, 0.06)],
        },

        news_sensitivity="medium",
        intervention_risk=False,
        gap_risk=False,

        base_weight=1.0,
        spread_cost_factor=0.9,

        monthly_strength={1: 1.1, 6: 0.9, 12: 1.05},
    ),

    "GBPUSD": SymbolInstitutionalProfile(
        symbol="GBPUSD",
        nickname="Cable",
        description="GBP/USD — plus volatil, tendances fortes, news-sensitif",

        avg_atr_pips=100.0,
        atr_percentile_high=135.0,
        atr_percentile_low=65.0,
        typical_spread_pts=18.0,
        spread_warning_pts=40.0,

        best_sessions=["london_open", "london", "london_ny_overlap"],
        avoid_sessions=["asia", "asia_london_overlap"],
        peak_hours_utc=[(8, 10), (13, 17)],

        adx_trend_threshold=22.0,
        rsi_overbought=75.0,               # Cable peut aller plus loin
        rsi_oversold=25.0,
        bb_std_dev=2.2,                     # Bandes plus larges
        respects_levels=True,
        trend_persistence="high",

        sl_atr_ranging=2.0,                 # SL plus large (volatil)
        sl_atr_trending=2.5,
        tp_atr_ranging=4.5,
        tp_atr_trending=6.0,                # TP plus large (gros moves)

        trailing_profile={
            "RANGING":     [(1.0, 0.35), (2.0, 0.25), (3.0, 0.15), (5.0, 0.08)],
            "TREND_UP":    [(1.0, 0.55), (2.0, 0.35), (3.0, 0.20), (5.0, 0.10)],
            "TREND_DOWN":  [(1.0, 0.55), (2.0, 0.35), (3.0, 0.20), (5.0, 0.10)],
            "HIGH_VOL":    [(1.0, 0.80), (2.0, 0.55), (3.0, 0.35), (5.0, 0.18)],
            "LOW_VOL":     [(1.0, 0.25), (2.0, 0.15), (3.0, 0.10), (5.0, 0.05)],
        },

        news_sensitivity="high",
        intervention_risk=False,
        gap_risk=True,

        base_weight=1.05,                   # Ponderation legerement superieure
        spread_cost_factor=1.2,             # Spread plus cher

        monthly_strength={3: 0.95, 6: 0.9, 9: 1.1, 12: 1.15},
    ),

    "USDCHF": SymbolInstitutionalProfile(
        symbol="USDCHF",
        nickname="Swissie",
        description="USD/CHF — valeur refuge, range-bound, SNB intervention",

        avg_atr_pips=55.0,
        atr_percentile_high=75.0,
        atr_percentile_low=35.0,
        typical_spread_pts=15.0,
        spread_warning_pts=35.0,

        best_sessions=["london", "london_ny_overlap", "european_morning"],
        avoid_sessions=["asia", "asia_london_overlap"],
        peak_hours_utc=[(8, 12), (13, 16)],

        adx_trend_threshold=18.0,           # Plus sensible (souvent range)
        rsi_overbought=68.0,                # RSI extremes plus etroits
        rsi_oversold=32.0,
        bb_std_dev=1.8,                     # Bandes plus serrees
        respects_levels=True,
        trend_persistence="medium",

        sl_atr_ranging=1.5,
        sl_atr_trending=2.0,
        tp_atr_ranging=3.0,
        tp_atr_trending=4.0,                # TP plus modeste (range-bound)

        trailing_profile={
            "RANGING":     [(1.0, 0.40), (2.0, 0.28), (3.0, 0.15), (5.0, 0.08)],
            "TREND_UP":    [(1.0, 0.60), (2.0, 0.38), (3.0, 0.22), (5.0, 0.10)],
            "TREND_DOWN":  [(1.0, 0.60), (2.0, 0.38), (3.0, 0.22), (5.0, 0.10)],
            "HIGH_VOL":    [(1.0, 0.80), (2.0, 0.55), (3.0, 0.35), (5.0, 0.18)],
            "LOW_VOL":     [(1.0, 0.30), (2.0, 0.18), (3.0, 0.10), (5.0, 0.05)],
        },

        news_sensitivity="medium",
        intervention_risk=True,             # SNB intervention possible
        gap_risk=True,                      # SNB flash crash 2015

        base_weight=0.85,                   # Poids reduit (moins previsible)
        spread_cost_factor=1.0,

        monthly_strength={6: 0.85, 7: 0.9, 12: 1.1},
    ),

    "EURUSD": SymbolInstitutionalProfile(
        symbol="EURUSD",
        nickname="Fiber",
        description="EUR/USD — paire la plus liquide, technique, respecte les niveaux",

        avg_atr_pips=85.0,
        atr_percentile_high=115.0,
        atr_percentile_low=55.0,
        typical_spread_pts=8.0,
        spread_warning_pts=25.0,

        best_sessions=["london_open", "london", "london_ny_overlap"],
        avoid_sessions=["asia", "asia_london_overlap"],
        peak_hours_utc=[(8, 10), (13, 17), (14, 16)],

        adx_trend_threshold=18.0,           # Plus sensible (bonne pour SMC)
        rsi_overbought=70.0,
        rsi_oversold=30.0,
        bb_std_dev=2.0,
        respects_levels=True,               # La plus technique des 4
        trend_persistence="medium",

        sl_atr_ranging=1.5,
        sl_atr_trending=2.0,
        tp_atr_ranging=4.0,
        tp_atr_trending=5.0,

        trailing_profile={
            "RANGING":     [(1.0, 0.50), (2.0, 0.35), (3.0, 0.20), (5.0, 0.10)],
            "TREND_UP":    [(1.0, 0.75), (2.0, 0.50), (3.0, 0.28), (5.0, 0.14)],
            "TREND_DOWN":  [(1.0, 0.75), (2.0, 0.50), (3.0, 0.28), (5.0, 0.14)],
            "HIGH_VOL":    [(1.0, 1.00), (2.0, 0.70), (3.0, 0.50), (5.0, 0.25)],
            "LOW_VOL":     [(1.0, 0.40), (2.0, 0.25), (3.0, 0.15), (5.0, 0.07)],
        },

        news_sensitivity="medium",
        intervention_risk=False,
        gap_risk=False,

        base_weight=1.10,                   # Plus de poids car technique
        spread_cost_factor=0.7,             # Spread le moins cher

        monthly_strength={1: 1.15, 3: 0.9, 7: 0.85, 9: 1.1, 12: 1.2},
    ),

    "BTCUSD": SymbolInstitutionalProfile(
        symbol="BTCUSD",
        nickname="Bitcoin",
        description="BTC/USD — crypto #1, volatilité extrême, 24/7, flash crashes",

        avg_atr_pips=800.0,                     # ATR H1 ~600-1000pts (volatilité extrême)
        atr_percentile_high=1200.0,
        atr_percentile_low=400.0,
        typical_spread_pts=80.0,                # spread crypto large
        spread_warning_pts=200.0,

        best_sessions=["all"],                   # crypto 24/7
        avoid_sessions=[],
        peak_hours_utc=[(0, 23)],

        adx_trend_threshold=20.0,                # ADX moins fiable crypto
        rsi_overbought=75.0,
        rsi_oversold=25.0,
        bb_std_dev=2.5,                          # Bandes très larges (extrême volatilité)
        respects_levels=False,                   # crypto peu technique
        trend_persistence="medium",

        sl_atr_ranging=2.5,
        sl_atr_trending=3.0,
        tp_atr_ranging=5.0,
        tp_atr_trending=7.0,

        trailing_profile={
            "RANGING":     [(1.0, 0.60), (2.0, 0.40), (3.0, 0.25), (5.0, 0.12)],
            "TREND_UP":    [(1.0, 0.80), (2.0, 0.50), (3.0, 0.30), (5.0, 0.15)],
            "TREND_DOWN":  [(1.0, 0.80), (2.0, 0.50), (3.0, 0.30), (5.0, 0.15)],
            "HIGH_VOL":    [(1.0, 1.00), (2.0, 0.70), (3.0, 0.50), (5.0, 0.25)],
            "LOW_VOL":     [(1.0, 0.40), (2.0, 0.30), (3.0, 0.15), (5.0, 0.08)],
        },

        news_sensitivity="medium",
        intervention_risk=False,
        gap_risk=True,                            # crypto gaps fréquents

        base_weight=1.0,
        spread_cost_factor=2.0,                   # spread cher

        monthly_strength={1: 1.1, 3: 0.9, 6: 0.85, 11: 1.15, 12: 1.2},
    ),

    "ETHUSD": SymbolInstitutionalProfile(
        symbol="ETHUSD",
        nickname="Ether",
        description="ETH/USD — crypto #2, volatile, corrélé BTCUSD (0.89), 24/7",

        avg_atr_pips=50.0,                     # ATR H4 ~40-60pts
        atr_percentile_high=80.0,
        atr_percentile_low=25.0,
        typical_spread_pts=80.0,               # spread crypto large
        spread_warning_pts=150.0,

        best_sessions=["all"],                  # crypto 24/7
        avoid_sessions=[],
        peak_hours_utc=[(0, 23)],

        adx_trend_threshold=20.0,               # ADX moins fiable crypto
        rsi_overbought=72.0,
        rsi_oversold=28.0,
        bb_std_dev=2.5,                         # Bandes larges (volatilité)
        respects_levels=False,                  # crypto peu technique
        trend_persistence="medium",

        sl_atr_ranging=2.0,
        sl_atr_trending=2.5,
        tp_atr_ranging=4.0,
        tp_atr_trending=6.0,

        trailing_profile={
            "RANGING":     [(1.0, 0.60), (2.0, 0.40), (3.0, 0.25), (5.0, 0.12)],
            "TREND_UP":    [(1.0, 0.80), (2.0, 0.50), (3.0, 0.30), (5.0, 0.15)],
            "TREND_DOWN":  [(1.0, 0.80), (2.0, 0.50), (3.0, 0.30), (5.0, 0.15)],
            "HIGH_VOL":    [(1.0, 1.00), (2.0, 0.70), (3.0, 0.50), (5.0, 0.25)],
            "LOW_VOL":     [(1.0, 0.40), (2.0, 0.30), (3.0, 0.15), (5.0, 0.08)],
        },

        news_sensitivity="medium",
        intervention_risk=False,
        gap_risk=True,                           # crypto gaps fréquents

        base_weight=0.90,                        # poids réduit (corrélé BTC)
        spread_cost_factor=2.0,                  # spread cher

        monthly_strength={1: 1.1, 3: 0.9, 6: 0.85, 11: 1.15, 12: 1.2},
    ),
}


# ── Groupes de corrélation statique ──

CORRELATION_GROUPS: dict[str, list[str]] = {
    "USD_LONG": ["USDCAD", "USDCHF"],       # USD est la devise de base
    "USD_SHORT": ["EURUSD", "GBPUSD"],      # USD est la devise de cotation
}

# Matrice de corrélation institutionnelle (estimation)
# ETHUSD + BTCUSD ajoutés Juin 2026 — corrélation 0.89
CORRELATION_MATRIX: dict[str, dict[str, float]] = {
    "USDCAD": {"USDCAD": 1.0, "GBPUSD": -0.55, "USDCHF": 0.55, "EURUSD": -0.50},
    "GBPUSD": {"USDCAD": -0.55, "GBPUSD": 1.0, "USDCHF": -0.50, "EURUSD": 0.70},
    "USDCHF": {"USDCAD": 0.55, "GBPUSD": -0.50, "USDCHF": 1.0, "EURUSD": -0.60},
    "EURUSD": {"USDCAD": -0.50, "GBPUSD": 0.70, "USDCHF": -0.60, "EURUSD": 1.0},
    "BTCUSD": {"BTCUSD": 1.0, "ETHUSD": 0.89, "XAUUSD": 0.15},
    "ETHUSD": {"ETHUSD": 1.0, "BTCUSD": 0.89, "XAUUSD": -0.10},
}

# Groupes pour la gestion de positions (max 1 trade par direction dans un groupe)
POSITION_GROUPS: list[list[str]] = [
    ["USDCAD", "USDCHF"],       # Groupe USD-long: pas de longs simultanés
    ["EURUSD", "GBPUSD"],       # Groupe USD-short: pas de shorts simultanés
    ["BTCUSD", "ETHUSD"],       # Groupe crypto: corrélation 0.89 → pas de trades same-direction
]


def get_profile(symbol: str) -> SymbolInstitutionalProfile | None:
    """Retourne le profil institutionnel d'un symbole."""
    return PROFILES.get(symbol)


def get_correlation(sym_a: str, sym_b: str) -> float:
    """Retourne la corrélation institutionnelle estimée entre 2 symboles."""
    return CORRELATION_MATRIX.get(sym_a, {}).get(sym_b, 0.0)


def get_same_group(symbol: str) -> list[str]:
    """Retourne les symboles dans le même groupe de corrélation."""
    for group in POSITION_GROUPS:
        if symbol in group:
            return [s for s in group if s != symbol]
    return []


def get_opposite_group(symbol: str) -> list[str]:
    """Retourne les symboles du groupe opposé (corrélation négative)."""
    for i, group in enumerate(POSITION_GROUPS):
        if symbol in group:
            other_idx = 1 - i
            return list(POSITION_GROUPS[other_idx])
    return []


def is_session_optimal(symbol: str, session_name: str) -> bool:
    """Vérifie si une session est optimale pour ce symbole."""
    profile = get_profile(symbol)
    if not profile:
        return True
    return session_name not in profile.avoid_sessions


def get_symbol_weight(symbol: str, regime: str = "RANGING") -> float:
    """Calcule le poids de trading pour un symbole basé sur son profil."""
    profile = get_profile(symbol)
    if not profile:
        return 1.0
    weight = profile.base_weight
    if regime == "RANGING" and not profile.respects_levels:
        weight *= 0.7
    if regime in ("TREND_UP", "TREND_DOWN") and profile.trend_persistence == "high":
        weight *= 1.15
    return weight


def get_atr_scaling(symbol: str, current_atr_pips: float) -> float:
    """Retourne un facteur d'échelle basé sur l'ATR actuelle vs la moyenne."""
    profile = get_profile(symbol)
    if not profile or profile.avg_atr_pips <= 0:
        return 1.0
    ratio = current_atr_pips / profile.avg_atr_pips
    if ratio > 1.5:
        return 0.7                        # ATR eleve → risqué
    elif ratio < 0.5:
        return 0.85                       # ATR bas → range, moins de moves
    return 1.0


def validate_trade_with_profile(symbol: str, session_name: str,
                                regime: str, direction: str,
                                current_atr_pips: float) -> tuple[bool, str]:
    """Valide une trade contre le profil institutionnel du symbole.

    Retourne (autorise, raison).
    """
    profile = get_profile(symbol)
    if not profile:
        return True, ""

    # Session check
    if session_name in profile.avoid_sessions:
        return False, f"Session {session_name} defavorable pour {symbol}"

    # ATR extreme check
    if current_atr_pips > profile.atr_percentile_high:
        return False, f"ATR {current_atr_pips:.0f} > seuil haut {profile.atr_percentile_high:.0f} pour {symbol}"

    # Direction check avec le profil
    if not profile.respects_levels and regime == "RANGING":
        # Symboles qui ne respectent pas les niveaux en range → filtrer
        if direction in ("BUY", "SELL"):
            pass  # juste reduire risque, pas bloquer

    return True, ""
