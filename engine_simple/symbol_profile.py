"""Profil institutionnel des 4 symboles actifs — connaissance approfondie pour le trading

Chaque actif a sa personnalité unique : sessions actives, volatilité typique,
comportement technique, spreads, corrélations. Ce module centralise cette
connaissance pour optimiser les décisions de trading par symbole.

Symboles actifs (Juin 2026): XAUUSD H4, BTCUSD H1, EURUSD H1
Note: ETHUSD désactivé 19 Juin (WR 27.6%, PF 0.50); US500.cash réactivé 23 Juin H4

Usage:
    from engine_simple.symbol_profile import get_profile
    profile = get_profile("XAUUSD")
    profile.best_sessions   # ["london_ny_overlap", "ny_morning"]
    profile.avg_atr_pips    # 95
    profile.pip_factor      # 1.0 (gold = unités prix)
"""

from dataclasses import dataclass, field

# ── Profils institutionnels des 4 actifs ──
# Juin 2026: XAUUSD H4, BTCUSD H1, ETHUSD H4, US500.cash H4


@dataclass
class SymbolInstitutionalProfile:
    """Profil de connaissance institutionnelle pour une paire de devises."""

    symbol: str
    nickname: str
    description: str

    # Volatilité
    avg_atr_pips: float  # ATR quotidien moyen en pips
    atr_percentile_high: float  # 80e percentile ATR
    atr_percentile_low: float  # 20e percentile ATR
    typical_spread_pts: float  # Spread typique en points
    spread_warning_pts: float  # Seuil spread anormal

    # Sessions optimales (UTC)
    best_sessions: list[str]  # Sessions recommandées
    avoid_sessions: list[str]  # Sessions à éviter
    peak_hours_utc: list[tuple[int, int]]  # Plages horaires les plus actives

    # Comportement technique
    adx_trend_threshold: float  # ADX seuil pour régime TREND (vs 20 par défaut)
    rsi_overbought: float  # Seuil surachat (70 par défaut)
    rsi_oversold: float  # Seuil survente (30 par défaut)
    bb_std_dev: float  # Bollinger deviation (2.0 par défaut)
    respects_levels: bool  # True = soutien/résistance techniques fiables
    trend_persistence: str  # "high", "medium", "low"

    # SL/TP recommandés (multiples ATR)
    sl_atr_ranging: float
    sl_atr_trending: float
    tp_atr_ranging: float
    tp_atr_trending: float

    # Trailing (multiples ATR) — override du trailing global par régime
    # Juin 2026: centralisé dans ftmo_config.py TRAILING_BY_SYMBOL.
    # Passer trailing_profile={} dans le constructeur pour utiliser la config globale.
    trailing_profile: dict[str, list[tuple[float, float]]]

    # Facteurs de risque
    news_sensitivity: str  # "high", "medium", "low"
    intervention_risk: bool  # SNB, BoJ, etc.
    gap_risk: bool  # Saut de prix fréquent au weekend

    # Poids dans l'ensemble
    base_weight: float = 1.0  # Pondération par défaut du signal
    spread_cost_factor: float = 1.0  # Facteur de coût de spread (1.0 = normal)

    # Saisonnalité interne
    monthly_strength: dict[int, float] = field(default_factory=dict)  # mois → force relative

    # Conversion ATR: facteur pour convertir ATR (unités prix) → unités profil
    # Forex 5-digit: 10000 (0.0070 → 70 pips). Crypto/Gold: adapté à la décimale.
    pip_factor: float = 10000.0

    def __post_init__(self):
        self.name = self.symbol


# ── Base de connaissance institutionnelle ──

PROFILES: dict[str, SymbolInstitutionalProfile] = {
    "EURUSD": SymbolInstitutionalProfile(
        symbol="EURUSD",
        nickname="Euro",
        description="EUR/USD — paire forex majeure, liquidité maximale, H1",
        avg_atr_pips=25.0,
        atr_percentile_high=40.0,
        atr_percentile_low=12.0,
        typical_spread_pts=8.0,
        spread_warning_pts=25.0,
        pip_factor=10000.0,
        best_sessions=["london", "london_ny_overlap"],
        avoid_sessions=["asian"],
        peak_hours_utc=[(8, 12), (13, 17)],
        adx_trend_threshold=22.0,
        rsi_overbought=70.0,
        rsi_oversold=30.0,
        bb_std_dev=2.0,
        respects_levels=True,
        trend_persistence="medium",
        sl_atr_ranging=1.5,
        sl_atr_trending=2.0,
        tp_atr_ranging=3.0,
        tp_atr_trending=5.0,
        trailing_profile={},
        news_sensitivity="high",
        intervention_risk=True,
        gap_risk=False,
        base_weight=1.0,
        spread_cost_factor=1.0,
        monthly_strength={},
    ),
    "BTCUSD": SymbolInstitutionalProfile(
        symbol="BTCUSD",
        nickname="Bitcoin",
        description="BTC/USD — crypto #1, volatilité extrême, 24/7, flash crashes",
        avg_atr_pips=800.0,  # ATR H1 ~600-1000pts (volatilité extrême)
        atr_percentile_high=1200.0,
        atr_percentile_low=400.0,
        typical_spread_pts=80.0,  # spread crypto large
        spread_warning_pts=200.0,
        pip_factor=1.0,  # Crypto: ATR déjà en unités prix ($376)
        best_sessions=["all"],  # crypto 24/7
        avoid_sessions=[],
        peak_hours_utc=[(0, 23)],
        adx_trend_threshold=20.0,  # ADX moins fiable crypto
        rsi_overbought=75.0,
        rsi_oversold=25.0,
        bb_std_dev=2.5,  # Bandes très larges (extrême volatilité)
        respects_levels=False,  # crypto peu technique
        trend_persistence="medium",
        sl_atr_ranging=2.5,
        sl_atr_trending=3.0,
        tp_atr_ranging=5.0,
        tp_atr_trending=7.0,
        trailing_profile={},  # centralisé dans ftmo_config.py
        news_sensitivity="medium",
        intervention_risk=False,
        gap_risk=True,  # crypto gaps fréquents
        base_weight=1.0,
        spread_cost_factor=2.0,  # spread cher
        monthly_strength={1: 1.1, 3: 0.9, 6: 0.85, 11: 1.15, 12: 1.2},
    ),
    "XAUUSD": SymbolInstitutionalProfile(
        symbol="XAUUSD",
        nickname="Gold",
        description="XAU/USD — Or,避险资产, tendances longues, session London+NY overlap",
        avg_atr_pips=95.0,  # ATR H4 ~80-110pts (Or)
        atr_percentile_high=140.0,
        atr_percentile_low=55.0,
        typical_spread_pts=30.0,  # spread or modéré
        spread_warning_pts=80.0,
        pip_factor=1.0,  # Gold: ATR déjà en unités prix ($90)
        best_sessions=["london_ny_overlap", "ny_morning", "london"],
        avoid_sessions=["asia", "asia_london_overlap"],
        peak_hours_utc=[(13, 17), (14, 16)],  # London+NY overlap = pic volatilité or
        adx_trend_threshold=22.0,  # H4 tendances pures
        rsi_overbought=72.0,
        rsi_oversold=28.0,
        bb_std_dev=2.2,
        respects_levels=True,  # Or respecte S/R techniques
        trend_persistence="high",  # tendances or = longues (semaines)
        sl_atr_ranging=1.5,
        sl_atr_trending=1.8,  # SL serré H4 (validé backtest)
        tp_atr_ranging=3.5,
        tp_atr_trending=5.0,
        trailing_profile={},  # centralisé dans ftmo_config.py
        news_sensitivity="high",  # NFP, CPI, Fed → impact fort
        intervention_risk=True,  # Banques centrales achètent/vendent l'or
        gap_risk=False,  # Or = peu de gaps H4
        base_weight=1.10,  # poids élevé (backtest H4 PF=1.16)
        spread_cost_factor=1.5,  # spread modéré
        monthly_strength={1: 1.15, 3: 0.9, 8: 0.85, 9: 1.1, 12: 1.1},
    ),
    "US500.cash": SymbolInstitutionalProfile(
        symbol="US500.cash",
        nickname="S&P500",
        description="S&P 500 Index — indice US, volatilité modérée, sessions US",
        avg_atr_pips=55.0,  # ATR H4 ~45-65pts
        atr_percentile_high=85.0,
        atr_percentile_low=30.0,
        typical_spread_pts=20.0,
        spread_warning_pts=50.0,
        pip_factor=1.0,  # Index: ATR en unités prix
        best_sessions=["ny_morning", "london_ny_overlap", "us_afternoon"],
        avoid_sessions=["asia", "asia_london_overlap"],
        peak_hours_utc=[(14, 17), (15, 16)],  # US market hours
        adx_trend_threshold=20.0,  # indices = trends modérées
        rsi_overbought=72.0,
        rsi_oversold=28.0,
        bb_std_dev=2.0,
        respects_levels=True,  # S&P respecte S/R
        trend_persistence="medium",
        sl_atr_ranging=1.2,
        sl_atr_trending=1.5,  # SL serré (backtest H4 validé)
        tp_atr_ranging=3.0,
        tp_atr_trending=4.0,
        trailing_profile={},  # centralisé dans ftmo_config.py
        news_sensitivity="high",  # NFP, CPI, Fed → impact fort
        intervention_risk=False,  # Fed = indirect
        gap_risk=True,  # gap le lundi fréquent
        base_weight=1.0,
        spread_cost_factor=1.2,
        monthly_strength={1: 1.1, 3: 0.9, 6: 0.85, 9: 1.1, 12: 1.15},
    ),
    "USDJPY": SymbolInstitutionalProfile(
        symbol="USDJPY",
        nickname="Dollar-Yen",
        description="USD/JPY — paire forex majeure, H1",
        avg_atr_pips=30.0,
        atr_percentile_high=50.0,
        atr_percentile_low=15.0,
        typical_spread_pts=7.0,
        spread_warning_pts=20.0,
        pip_factor=100.0,  # JPY pair: 1 pip = 0.01 yen → ATR_raw 0.3 × 100 = 30 pips
        best_sessions=["london", "london_ny_overlap"],
        avoid_sessions=["asian"],
        peak_hours_utc=[(8, 12), (13, 17)],
        adx_trend_threshold=22.0,
        rsi_overbought=70.0,
        rsi_oversold=30.0,
        bb_std_dev=2.0,
        respects_levels=True,
        trend_persistence="medium",
        sl_atr_ranging=1.5,
        sl_atr_trending=2.0,
        tp_atr_ranging=3.0,
        tp_atr_trending=5.0,
        trailing_profile={},
        news_sensitivity="high",
        intervention_risk=True,
        gap_risk=False,
        base_weight=1.0,
        spread_cost_factor=1.0,
        monthly_strength={},
    ),
    "GBPUSD": SymbolInstitutionalProfile(
        symbol="GBPUSD",
        nickname="Sterling",
        description="GBP/USD — paire forex majeure, H1",
        avg_atr_pips=35.0,
        atr_percentile_high=55.0,
        atr_percentile_low=18.0,
        typical_spread_pts=10.0,
        spread_warning_pts=30.0,
        pip_factor=10000.0,
        best_sessions=["london", "london_ny_overlap"],
        avoid_sessions=["asian"],
        peak_hours_utc=[(8, 12), (13, 17)],
        adx_trend_threshold=22.0,
        rsi_overbought=70.0,
        rsi_oversold=30.0,
        bb_std_dev=2.0,
        respects_levels=True,
        trend_persistence="medium",
        sl_atr_ranging=1.5,
        sl_atr_trending=2.0,
        tp_atr_ranging=3.0,
        tp_atr_trending=5.0,
        trailing_profile={},
        news_sensitivity="high",
        intervention_risk=True,
        gap_risk=False,
        base_weight=1.0,
        spread_cost_factor=1.0,
        monthly_strength={},
    ),
    "AUDUSD": SymbolInstitutionalProfile(
        symbol="AUDUSD",
        nickname="Aussie",
        description="AUD/USD — paire forex mineure, H1",
        avg_atr_pips=28.0,
        atr_percentile_high=45.0,
        atr_percentile_low=14.0,
        typical_spread_pts=9.0,
        spread_warning_pts=25.0,
        pip_factor=10000.0,
        best_sessions=["asian", "london_ny_overlap"],
        avoid_sessions=[],
        peak_hours_utc=[(1, 5), (13, 17)],
        adx_trend_threshold=22.0,
        rsi_overbought=70.0,
        rsi_oversold=30.0,
        bb_std_dev=2.0,
        respects_levels=True,
        trend_persistence="medium",
        sl_atr_ranging=1.5,
        sl_atr_trending=2.0,
        tp_atr_ranging=3.0,
        tp_atr_trending=5.0,
        trailing_profile={},
        news_sensitivity="medium",
        intervention_risk=False,
        gap_risk=False,
        base_weight=0.8,
        spread_cost_factor=1.0,
        monthly_strength={},
    ),
    "USDCAD": SymbolInstitutionalProfile(
        symbol="USDCAD",
        nickname="Loonie",
        description="USD/CAD — paire forex mineure, H1",
        avg_atr_pips=25.0,
        atr_percentile_high=40.0,
        atr_percentile_low=12.0,
        typical_spread_pts=9.0,
        spread_warning_pts=25.0,
        pip_factor=10000.0,
        best_sessions=["london_ny_overlap", "ny"],
        avoid_sessions=["asian"],
        peak_hours_utc=[(13, 17), (14, 16)],
        adx_trend_threshold=22.0,
        rsi_overbought=70.0,
        rsi_oversold=30.0,
        bb_std_dev=2.0,
        respects_levels=True,
        trend_persistence="medium",
        sl_atr_ranging=1.5,
        sl_atr_trending=2.0,
        tp_atr_ranging=3.0,
        tp_atr_trending=5.0,
        trailing_profile={},
        news_sensitivity="medium",
        intervention_risk=True,
        gap_risk=False,
        base_weight=0.8,
        spread_cost_factor=1.0,
        monthly_strength={},
    ),
}


# ── Groupes de corrélation statique (Juin 2026 — 3 symboles actifs) ──

CORRELATION_GROUPS: dict[str, list[str]] = {
    "CRYPTO": ["BTCUSD"],
    "RISK_ON": ["US500.cash"],
    "SAFE_HAVEN": ["XAUUSD"],
    "FOREX_MAJOR": ["EURUSD", "GBPUSD", "USDJPY", "USDCAD"],
    "FOREX_COMMODITY": ["AUDUSD", "USDCAD"],
}

# Matrice de corrélation institutionnelle (estimation)
# Juin 2026: 3 symboles actifs (ETHUSD retiré)
CORRELATION_MATRIX: dict[str, dict[str, float]] = {
    "BTCUSD": {
        "BTCUSD": 1.0,
        "XAUUSD": 0.15,
        "US500.cash": 0.45,
        "EURUSD": 0.10,
        "GBPUSD": 0.15,
        "USDJPY": -0.30,
        "AUDUSD": 0.40,
        "USDCAD": 0.35,
    },
    "XAUUSD": {
        "XAUUSD": 1.0,
        "BTCUSD": 0.15,
        "US500.cash": -0.25,
        "EURUSD": -0.10,
        "GBPUSD": 0.05,
        "USDJPY": 0.20,
        "AUDUSD": -0.15,
        "USDCAD": -0.10,
    },
    "US500.cash": {
        "US500.cash": 1.0,
        "BTCUSD": 0.45,
        "XAUUSD": -0.25,
        "EURUSD": 0.35,
        "GBPUSD": 0.40,
        "USDJPY": -0.20,
        "AUDUSD": 0.30,
        "USDCAD": 0.25,
    },
    "EURUSD": {"EURUSD": 1.0, "GBPUSD": 0.70, "USDJPY": 0.30, "AUDUSD": 0.55, "USDCAD": 0.50},
    "GBPUSD": {"GBPUSD": 1.0, "EURUSD": 0.70, "USDJPY": 0.25, "AUDUSD": 0.50, "USDCAD": 0.45},
    "USDJPY": {"USDJPY": 1.0, "EURUSD": 0.30, "GBPUSD": 0.25, "AUDUSD": 0.15, "USDCAD": 0.20},
    "AUDUSD": {"AUDUSD": 1.0, "EURUSD": 0.55, "GBPUSD": 0.50, "USDJPY": 0.15, "USDCAD": 0.70},
    "USDCAD": {"USDCAD": 1.0, "EURUSD": 0.50, "GBPUSD": 0.45, "USDJPY": 0.20, "AUDUSD": 0.70},
}

# Groupes pour la gestion de positions (max 1 trade par direction dans un groupe)
POSITION_GROUPS: list[list[str]] = [
    # Groupes de corrélation SUPPRIMÉS — mode agressif (Phase 0, Juin 2026)
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
    """Retourne les symboles du groupe opposé (corrélation négative).

    Si le symbole n'est dans aucun groupe ou n'a pas de groupe opposé, retourne [].
    """
    for i, group in enumerate(POSITION_GROUPS):
        if symbol in group:
            other_idx = 1 - i
            if other_idx < len(POSITION_GROUPS):
                return list(POSITION_GROUPS[other_idx])
            return []
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


def get_atr_scaling(symbol: str, current_atr_raw: float) -> float:
    """Retourne un facteur d'échelle basé sur l'ATR actuelle vs la moyenne.

    Args:
        symbol: Le symbole (ex: "BTCUSD")
        current_atr_raw: ATR en unités de prix (pas en pips)
    """
    profile = get_profile(symbol)
    if not profile or profile.avg_atr_pips <= 0:
        return 1.0
    # Convertir l'ATR brut en unités du profil via pip_factor du symbole
    pip_factor = getattr(profile, "pip_factor", 10000.0)
    current_atr_converted = current_atr_raw * pip_factor
    ratio = current_atr_converted / profile.avg_atr_pips
    if ratio >= 1.5:
        return 0.7  # ATR eleve → risqué
    elif ratio < 0.5:
        return 0.85  # ATR bas → range, moins de moves
    return 1.0


def validate_trade_with_profile(
    symbol: str, session_name: str, regime: str, direction: str, current_atr_pips: float
) -> tuple[bool, str]:
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
