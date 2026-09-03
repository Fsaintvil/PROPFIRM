"""
MOM20x3 — Stratégie Momentum 20 périodes avec filtres avancés.

⚠️ ATTENTION — DUAL SOURCE OF TRUTH ⚠️
Ce fichier (strategy.py:SYMBOL_CONFIG) est la VRAIE source des paramètres
techniques du signal (momentum_period, thresholds SL/TP, ADX slope).
Le fichier config/default.yaml est la DOCUMENTATION de ces paramètres —
un changement dans le YAML n'a AUCUN effet sur les signaux réels.
Pour modifier un paramètre de signal, il faut changer strategy.py.

Principe :
  c[i] - c[i-20] > seuil × ATR  →  Breakout haussier (BUY)
  c[i-20] - c[i] > seuil × ATR  →  Breakout baissier (SELL)

Seuils adaptatifs selon ADX (validés backtest 12+ ans, 67% WR) :
  ADX ≥ 22 (trending) : seuil = 2.5 × ATR (unifié avec regime.py 22/18)
  ADX < 22 (ranging)  : seuil = 2.0 × ATR
  Plafonné à 2.5 × ATR max, plancher 1.5 × ATR

Filtres additionnels (Juin 2026 — Audit Profond) :
  - ADX slope : ADX doit être en hausse (Wilder's smoothing, half=len/3)
  - +DI/-DI    : BUY nécessite +DI > -DI×0.8, SELL nécessite -DI > +DI×0.8
  - Pullback   : entrée différée après retracement vers EMA20 (ATR-based band)
  - DI Override: short-term momentum (5 périodes) peut inverser si ADX≥22
  - NaN guard  : momentum NaN/Inf → skip silencieux

Aucun overlay ICT/SMC (FVG, Order Blocks, Killzones, etc.)
"""

import logging
from types import MappingProxyType

import numpy as np

from engine_simple.indicators import adx, adx_arrays, atr, ema
from engine_simple.ftmo_config import PULLBACK_FILTER_SCORE_THRESHOLD

try:
    from engine_simple.market_structure import analyze_market_structure
except Exception:
    analyze_market_structure = None

logger = logging.getLogger("strategy")

# THRESHOLD_TRENDING / THRESHOLD_RANGING sont DÉPRÉCIÉS (Juin 2026)
# Les seuils réels viennent de SYMBOL_CONFIG (per-symbol) ou DEFAULT_SYMBOL_CONFIG.
# Ces constantes ne sont plus utilisées dans le calcul du signal.
THRESHOLD_MAX = 6.0  # 🔧 27 Juil 2026: ↑ 3.0→6.0 (Solution A: BTCUSD 5.0×ATR, XAUUSD 4.0×ATR)
THRESHOLD_MIN = 1.5  # Plancher absolu (clamping sécurité)
# Pour compatibilité avec les utilitaires/backtests
# Ces constantes sont principalement documentaires — les valeurs
# par symbole dans `SYMBOL_CONFIG` restent prioritaires.
THRESHOLD_TRENDING = 2.5
THRESHOLD_RANGING = 2.0

# ============================================================================
# PARAMÈTRES SPÉCIFIQUES PAR ACTIF — 27 Symboles Actifs (1er Juillet 2026)
# ============================================================================
# Chaque symbole a sa propre configuration calibrée individuellement :
#   - momentum_period, SL/TP par régime, seuils ATR trending/ranging
#   - Filtre ADX slope (scare), pullback bands
#   - Sessions préférées, protection news
#
# Sources:
#   - Backtest 12+ ans (158,964 trades)
#   - Données live FTMO (analyse WR/PnL par symbole)
#   - Caractéristiques de marché (ATR, spread, sessions, volatilité)
#   - Règles FTMO (DD 10%, daily loss 2%)
# ============================================================================

# ── SL/TP par actif et par régime ────────────────────────────────────────
# XAUUSD H4: Or — volatilité élevée (ATR≈90-100pts H4), tendances longues
#   → Migration de H1 (perdant 12 ans) vers H4 (WR 68.6%, DD 6.9%)
# BTCUSD H1: Bitcoin — volatilité EXTRÊME (ATR 8.7% H1), momentum rapide
#   → risk_mult 0.50 pour DD FTMO-safe (~9.0% attendu)
# US30.cash H1: Dow Jones — tendances longues, forte liquidité
#   → Nouveau 28 Juin 2026 (remplace EURUSD — Supreme Council)

SYMBOL_CONFIG = {
    # ═══════════════════════════════════════════════════════════════════════
    # XAUUSD H1 — Or (PASSAGE H1 — 27 Juillet 2026)
    # Caractéristiques: ATR~90-100pts H4, tendances longues, London+NY overlap
    # Backtest 12+ ans H4: WR 68.6%, PF 1.16, DD 6.9%
    # Timeframe: H1 (passage H4→H1 le 27 Juillet 2026)
    # Justification complète dans config/default.yaml:XAUUSD
    # ═══════════════════════════════════════════════════════════════════════
    "XAUUSD": {
        # ═══════════════════════════════════════════════════════════════════
        # SOLUTION A — 27 Juillet 2026: Optimisé FTMO
        # Threshold 4.0×ATR, SL 1.5×ATR, TP 6.0×ATR
        # Trailing: DÉSACTIVÉ, Partial TP: DÉSACTIVÉ
        # Backtest H4 2012-2026: PF=1.31, DD=0.29%, +$2,606 ✅✅ FTMO-safe
        # ═══════════════════════════════════════════════════════════════════
        "momentum_period": 20,
        # 🔧 28 Août 2026: SL 1.5→2.5×ATR (uniformisé partout). TP 6.0→7.5 (RR 3.0 conservé).
        "sl_atr_trending": 2.5,
        "tp_atr_trending": 7.5,
        # 🔧 FIX 30 Aout 2026: XAUUSD SL ranging 2.5→2.0 (revenir à l'ancienne config).
        # En ranging, la volatilité est plus basse → SL plus serré protège mieux sur les faux signaux.
        # En trending, le SL 2.5 est correct (laisse respirer le trade).
        "sl_atr_ranging": 2.0,
        "tp_atr_ranging": 6.0,
        "threshold_trending": 2.5,  # 🔧 ALIGNÉ 06 Aout 2026: 4.0→2.5 (le pipeline réel utilise déjà 2.5 via OL base_thresh — le 4.0 était inerte/incohérent, calé sur backtest H4 mais robot en H1)
        "threshold_ranging": 2.0,  # 🔧 ALIGNÉ 06 Aout 2026: 4.0→2.0 (même logique, aligné OL base_thresh=2.5 trending / 2.0 ranging)
        "adx_slope_threshold": -9.0,
        "adx_slope_threshold_strong": -14.0,
        "pullback_band_trending": 0.5,
        "pullback_band_ranging": 0.3,
        "timeframe": "H1",
        "max_spread_points": 60,
        "cmf_threshold": 0.10,
        "obv_div_penalty_high": 0.70,
        "obv_div_penalty_low": 0.85,
        "conf": 0.85,
        "preferred_hours": list(range(24)),
        "news_minutes_before": 10,
        "news_minutes_after": 10,
        "min_score": 0.85,  # 🔧 3 Sept 2026: −10% (0.85→0.77)
        "adx_thresh": 22,
        "min_rr": 2.0,
        "risk_mult": 1.0,  # 🔧 ALIGNÉ 14 Aout 2026: 0.0→1.0 (décision utilisateur — réactivation XAUUSD)
        #    AVERTISSEMENT historique: le 14 Août seul config/default.yaml avait été mis à jour
        #    (0.0→1.0) mais PAS strategy.py → XAUUSD restait gelé car get_symbol_param lit
        #    strategy.py, PAS le YAML (c'était "la doc"). Aligné le 14 Aout sur la décision
        #    utilisateur. Signal risk_mult = 1.0 × OL → calculate_lot → lot réel.
        "cooldown_minutes": 0,
        "auto_pause_losses": 999,
        "lot_base": 0.01,
        "lot_max": 0.10,
        "daily_loss_limit_pct": 0.02,
        "max_dd_pct": 0.10,
    },
    # ═══════════════════════════════════════════════════════════════════════
    # BTCUSD H1 — Bitcoin (PASSAGE H1 — 27 Juillet 2026)
    # Caractéristiques: Volatilité EXTRÊME (ATR 8.7% H1), 24/7
    # Backtest 12+ ans H1: WR 69.8%, PF 1.19, DD 5.6%
    # Timeframe: H1 (passage H4→H1 le 27 Juillet 2026)
    # Justification complète dans config/default.yaml:BTCUSD
    # ═══════════════════════════════════════════════════════════════════════
    "BTCUSD": {
        # ═══════════════════════════════════════════════════════════════════
        # SOLUTION A — 27 Juillet 2026: Optimisé FTMO (H1)
        # Threshold aligné BACKTEST 16 Août 2026: 2.5/2.0 (au lieu de 5.0/5.0)
        # — backtest validé scripts/backtest_with_costs.py: PF 1.18, 6250 trades
        # — 5.0/5.0 = anomalie (le backtest n'a JAMAIS été validé à 5.0)
        # SL 1.5×ATR, TP 6.0×ATR, Trailing: DÉSACTIVÉ, Partial TP: DÉSACTIVÉ
        # Garde-fou (16 Août 2026): si WR BTCUSD < 50% après 15-20 trades
        #   RÈGLE D'OR → revenir à 3.5 ou plafonner contribution ≤ 25%
        # ═══════════════════════════════════════════════════════════════════
        "momentum_period": 20,
        # 🔧 2 Sept 2026: SL 2.5→2.0 (edge prouvé p=0.0012, SL plus serré = RR meilleur)
        "sl_atr_trending": 2.0,
        "tp_atr_trending": 5.0,
        "sl_atr_ranging": 2.0,
        "tp_atr_ranging": 5.0,
        "threshold_trending": 2.5,
        "threshold_ranging": 2.0,
        "adx_slope_threshold": -8.0,  # 🔧 29 Juil: assoupli ×1.3 (était -6.0) — dégelé
        "adx_slope_threshold_strong": -12.0,  # 🔧 29 Juil: assoupli (était -9.0) — aligné DEFAULT
        "pullback_band_trending": 0.8,
        "pullback_band_ranging": 0.5,
        "timeframe": "H1",
        "max_spread_points": 150,
        "cmf_threshold": 0.20,
        "obv_div_penalty_high": 0.85,
        "obv_div_penalty_low": 0.92,
        "conf": 0.85,
        "preferred_hours": list(range(24)),
        "news_minutes_before": 15,
        "news_minutes_after": 15,
        "min_score": 0.65,
        "adx_thresh": 15,  # 🔧 3 Sept 2026: 20→15. Crypto ADX naturellement bas (16-17), seuil bloquant
        "min_rr": 2.0,
        "risk_mult": 1.0,  # 🔓 DÉBLOQUÉ 13 Août 2026 (décision utilisateur): PF backtest 1.18, groupe CRYPTO indépendant
        "cooldown_minutes": 0,
        "auto_pause_losses": 999,
        "lot_base": 0.01,
        "lot_max": 0.10,
        "daily_loss_limit_pct": 0.02,
        "max_dd_pct": 0.10,
    },
    "GBPUSD": {
        "momentum_period": 20,
        "sl_atr_trending": 2.5,
        "tp_atr_trending": 6.25,
        "sl_atr_ranging": 2.5,
        "tp_atr_ranging": 6.67,
        "threshold_trending": 2.5,
        "threshold_ranging": 2.0,
        "adx_slope_threshold": -8.0,
        "adx_slope_threshold_strong": -12.0,
        "pullback_band_trending": 0.5,
        "pullback_band_ranging": 0.3,
        "preferred_hours": list(range(24)),  # 🔧 3 Sept 2026: 24/5 (FTMO autorise)
        "news_minutes_before": 5,
        "news_minutes_after": 5,
        "min_score": 0.69,  # 🔧 2 Sept 2026: user request — collecte qualifiée
        "adx_thresh": 22,
        "min_rr": 1.5,
        "risk_mult": 1.0,
        "cooldown_minutes": 0,
        "auto_pause_losses": 999,
    },
    "AUDUSD": {
        "momentum_period": 20,
        "sl_atr_trending": 2.5,
        "tp_atr_trending": 6.25,
        "sl_atr_ranging": 2.5,
        "tp_atr_ranging": 6.67,
        "threshold_trending": 2.5,
        "threshold_ranging": 2.0,
        "adx_slope_threshold": -5.0,
        "adx_slope_threshold_strong": -8.0,
        "pullback_band_trending": 0.5,
        "pullback_band_ranging": 0.3,
        "preferred_hours": list(range(24)),  # 🔧 3 Sept 2026: 24/5 (FTMO autorise)
        "news_minutes_before": 5,
        "news_minutes_after": 5,
        "min_score": 0.79,  # 🔧 2 Sept 2026: user request — perdant structurel, filtré
        "adx_thresh": 22,
        "min_rr": 1.5,
        "risk_mult": 1.0,  # 🔧 3 Sept 2026: dégelé (décision user)
        "cooldown_minutes": 0,
        "auto_pause_losses": 999,
    },
    # ═══════════════════════════════════════════════════════════════════════
    # US500.cash — DÉSACTIVÉ 25 Juin 2026 (PF 0.39 toxique)
    # Retiré de SYMBOL_CONFIG. Utilise DEFAULT_SYMBOL_CONFIG si réactivé.
    # ═══════════════════════════════════════════════════════════════════════
    # ═══════════════════════════════════════════════════════════════════════
    # US30.cash H1 — Dow Jones Industrial Average (AJOUTÉ 28 Juin 2026)
    # Caractéristiques: Indice US, tendances longues, forte liquidité
    # Backtest H1 12+ ans avec coûts: WR 74.8%, PF 1.19, DD 8.5%
    #   → p<0.001 — edge robuste après coûts
    #   → Remplace EURUSD (PF 0.75 après coûts) — Supreme Council 28 Juin
    # Timeframe: H1
    # Justification complète dans config/default.yaml:US30.cash
    # ═══════════════════════════════════════════════════════════════════════
    "US30.cash": {
        # Momentum 20 périodes H1 = 20h (standard MOM20x3)
        "momentum_period": 20,
        # 🔧 28 Août 2026: SL 1.5/1.2→2.5×ATR (uniformisé). TP 4.5/3.0→10.0 (RR 4.0 conservé).
        "sl_atr_trending": 2.5,
        "tp_atr_trending": 10.0,
        "sl_atr_ranging": 2.5,
        "tp_atr_ranging": 10.0,
        # Seuils ATR standard
        "threshold_trending": 2.5,  # 🔧 FIX #7: Assoupli (était 3.0)  # 🔧 FIX #5
        "threshold_ranging": 2.0,  # 🐛 FIX #14: 2.0 en ranging (était 2.5 — identique à trending!)
        # Filtres ADX standard (indices US)
        "adx_slope_threshold": -9.0,
        "adx_slope_threshold_strong": -14.0,
        # Pullback bandes serrées (indices font peu de pullbacks profonds)
        "pullback_band_trending": 0.3,
        "pullback_band_ranging": 0.2,
        # (cmf_threshold, obv_div_penalty gérés par signal_pipeline depuis default.yaml)
        # Sessions US market hours
        "preferred_hours": list(range(24)),  # 🔧 3 Sept 2026: 24/5 (FTMO autorise)
        # News filter (protection news US)
        "news_minutes_before": 15,
        "news_minutes_after": 15,
        # 🔒 RENFORCÉ 1er Juillet 2026 — WR 28.6% live
        "min_score": 0.80,  # 🔧 3 Sept 2026: US30 PF 0.18 — filtrage strict
        "adx_thresh": 22,
        "min_rr": 1.5,
        "risk_mult": 1.0,  # 🔧 3 Sept 2026: dégelé (récolte continuation)
        "cooldown_minutes": 0,
        "auto_pause_losses": 999,
    },
    # ═══════════════════════════════════════════════════════════════════════
    # Symboles REACTIVÉS 29 Juin 2026 — High Confidence Only (≥90%)
    # Ces symboles ne trade QUE si confidence ≥ 0.90 (gate dans ftmo_protector.py)
    # Paramètres basés sur DEFAULT_SYMBOL_CONFIG (standard MOM20x3)
    # ═══════════════════════════════════════════════════════════════════════
    "EURUSD": {
        "momentum_period": 20,
        # 🔧 28 Août 2026: SL 3.0/2.25→2.5×ATR (uniformisé). TP 7.5/6.0→6.25/6.67 (RR 2.5/2.67).
        "sl_atr_trending": 2.5,
        "tp_atr_trending": 6.25,
        "sl_atr_ranging": 2.5,
        "tp_atr_ranging": 6.67,
        "threshold_trending": 2.5,  # 🔧 FIX #7: Assoupli (était 3.0)  # 🔧 FIX #5
        "threshold_ranging": 2.0,  # 🐛 FIX #14: 2.0 en ranging (était 2.5 — identique à trending!)
        "adx_slope_threshold": -8.0,
        "adx_slope_threshold_strong": -12.0,
        "pullback_band_trending": 0.5,
        "pullback_band_ranging": 0.3,
        # 🔧 19 Août 2026 (Backtest Optimizations): session LDN-NY overlap 13-17h GMT
        # (DD backtest 30.3%→9.0%, pertes −74%). Voir rapport runtime/backtest_optimizations.json.
        "preferred_hours": list(range(24)),  # 🔧 3 Sept 2026: 24/5 (FTMO autorise)
        "news_minutes_before": 5,
        "news_minutes_after": 5,
        "min_score": 0.69,  # 🔧 10 Juil 2026: ↑ 0.80→0.85 — symbole réactivé, sélectif max
        "adx_thresh": 22,
        "min_rr": 1.5,
        "risk_mult": 1.0,  # 🔧 3 Sept 2026: EDGE confirmé (PF 1.53)
        "cooldown_minutes": 0,
        "auto_pause_losses": 999,
    },
    "USDJPY": {
        "momentum_period": 20,
        # 🔧 28 Août 2026: SL 3.0/2.25→2.5×ATR (uniformisé). TP 7.5/6.0→6.25/6.67 (RR 2.5/2.67).
        "sl_atr_trending": 2.5,
        "tp_atr_trending": 6.25,
        "sl_atr_ranging": 2.5,
        "tp_atr_ranging": 6.67,
        "threshold_trending": 2.5,  # 🔧 FIX #7: Assoupli (était 3.0)  # 🔧 FIX #5
        "threshold_ranging": 2.0,  # 🐛 FIX #14: 2.0 en ranging (était 2.5 — identique à trending!)
        "adx_slope_threshold": -8.0,
        "adx_slope_threshold_strong": -12.0,
        "pullback_band_trending": 0.5,
        "pullback_band_ranging": 0.3,
        # 🔧 19 Août 2026: session LDN-NY overlap 13-17h GMT (DD −66%, pertes −74%)
        "preferred_hours": list(range(24)),  # 🔧 3 Sept 2026: 24/5 (FTMO autorise)
        "news_minutes_before": 5,
        "news_minutes_after": 5,
        "min_score": 0.75,  # 🔧 10 Juil 2026: ↓ 0.85→0.80 — trop restrictif, scores plafonnent à 0.61
        "adx_thresh": 22,
        "min_rr": 1.5,
        "risk_mult": 1.0,  # 🔧 3 Sept 2026: dégelé (récolte continuation)
        "cooldown_minutes": 0,
        "auto_pause_losses": 999,
    },
    # ═══════════════════════════════════════════════════════════════════════
    # USDCAD H1 — gelé, aucun edge validé (PF 0.84, 16 trades, -$2.52)
    # ═══════════════════════════════════════════════════════════════════════
    "USDCAD": {
        "momentum_period": 20,
        "sl_atr_trending": 2.5,
        "tp_atr_trending": 6.25,
        "sl_atr_ranging": 2.5,
        "tp_atr_ranging": 6.67,
        "threshold_trending": 2.5,
        "threshold_ranging": 2.0,
        "adx_slope_threshold": -5.0,
        "adx_slope_threshold_strong": -8.0,
        "pullback_band_trending": 0.5,
        "pullback_band_ranging": 0.3,
        "preferred_hours": list(range(24)),  # 🔧 3 Sept 2026: 24/5 (FTMO autorise)
        "news_minutes_before": 5,
        "news_minutes_after": 5,
        "min_score": 0.82,  # 🔧 3 Sept 2026: USDCAD PF 0.84 — filtrage strict
        "adx_thresh": 22,
        "min_rr": 1.5,
        "risk_mult": 1.0,  # 🔧 3 Sept 2026: dégelé (récolte continuation)
        "cooldown_minutes": 0,
        "auto_pause_losses": 999,
    },
    # ═══════════════════════════════════════════════════════════════════════
    # USDCHF H1 — gelé, WR 12.5% historique
    # ═══════════════════════════════════════════════════════════════════════
    "USDCHF": {
        "momentum_period": 20,
        "sl_atr_trending": 2.5,
        "tp_atr_trending": 6.25,
        "sl_atr_ranging": 2.5,
        "tp_atr_ranging": 6.67,
        "threshold_trending": 2.5,
        "threshold_ranging": 2.0,
        "adx_slope_threshold": -5.0,
        "adx_slope_threshold_strong": -8.0,
        "pullback_band_trending": 0.5,
        "pullback_band_ranging": 0.3,
        "preferred_hours": list(range(24)),  # 🔧 3 Sept 2026: 24/5 (FTMO autorise)
        "news_minutes_before": 5,
        "news_minutes_after": 5,
        "min_score": 0.65,
        "adx_thresh": 22,
        "min_rr": 1.5,
        "risk_mult": 1.0,  # 🔧 3 Sept 2026: dégelé (récolte continuation)
        "cooldown_minutes": 0,
        "auto_pause_losses": 999,
    },
    "NZDUSD": {
        "momentum_period": 20,
        # 🔧 28 Août 2026: SL 3.0/2.25→2.5×ATR (uniformisé). TP 7.5/6.0→6.25/6.67 (RR 2.5/2.67).
        "sl_atr_trending": 2.5,
        "tp_atr_trending": 6.25,
        "sl_atr_ranging": 2.5,
        "tp_atr_ranging": 6.67,
        "threshold_trending": 2.5,  # 🔧 FIX #7: Assoupli (était 3.0)  # 🔧 FIX #5
        "threshold_ranging": 2.0,  # 🐛 FIX #14: 2.0 en ranging (était 2.5 — identique à trending!)
        "adx_slope_threshold": -8.0,
        "adx_slope_threshold_strong": -12.0,
        "pullback_band_trending": 0.5,
        "pullback_band_ranging": 0.3,
        # 🔧 19 Août 2026: session LDN-NY overlap 13-17h GMT (DD −66%, pertes −74%)
        "preferred_hours": list(range(24)),  # 🔧 3 Sept 2026: 24/5 (FTMO autorise)
        "news_minutes_before": 5,
        "news_minutes_after": 5,
        "min_score": 0.79,  # 🔧 14 Juil 2026: ↓ 0.75→0.60 — rétabli, le dynamic min_score (signal_validator) monte si WR<50%
        "adx_thresh": 22,
        "min_rr": 1.3,
        "risk_mult": 1.0,
        "cooldown_minutes": 0,
        "auto_pause_losses": 999,
    },
    "ETHUSD": {
        "momentum_period": 20,
        # 🔧 28 Août 2026: SL 2.0/1.5→2.5×ATR (uniformisé). TP 5.0/4.0→5.0 (RR 2.0 conservé).
        "sl_atr_trending": 2.5,
        "tp_atr_trending": 5.0,
        "sl_atr_ranging": 2.5,
        "tp_atr_ranging": 5.0,
        "threshold_trending": 2.5,  # 🔧 FIX #7: Assoupli (était 3.0)  # 🔧 FIX #5
        "threshold_ranging": 2.0,  # 🐛 FIX #14: 2.0 en ranging (était 2.5 — identique à trending!)
        "adx_slope_threshold": -8.0,
        "adx_slope_threshold_strong": -12.0,
        "pullback_band_trending": 0.5,
        "pullback_band_ranging": 0.3,
        # Paramètres étendus per-symbol (1er Juillet 2026)
        "timeframe": "H1",
        "max_spread_points": 120,
        "cmf_threshold": 0.20,
        "obv_div_penalty_high": 0.85,
        "obv_div_penalty_low": 0.92,
        "conf": 0.90,
        "preferred_hours": list(range(24)),
        "news_minutes_before": 0,
        "news_minutes_after": 0,
        # 🔒 SOFT BLOCK 1er Juillet 2026 — WR 29.4% live, -$139
        # risk_mult=0.05 = 95% de réduction de risque, micro-lot 0.05 (×5)
        # min_score=0.90 = seuls les signaux exceptionnels passent
        "min_score": 0.65,  # très sélectif
        "adx_thresh": 20,
        "min_rr": 2.0,
        "risk_mult": 0.0,  # 🔴 GEL TEMPORAIRE par automation: freeze symbol due to large losses
        "risk_per_trade": 0.001,  # risque réduit (soft block)
        "cooldown_minutes": 0,
        "auto_pause_losses": 999,
        "lot_base": 0.05,
        "lot_max": 0.10,  # plafonné (soft block ×5)
        "daily_loss_limit_pct": 0.01,  # 1% max par jour (soft block)
        "max_dd_pct": 0.05,  # 5% max drawdown (soft block)
    },
    # ═══════════════════════════════════════════════════════════
    # US100.cash H1 — Nasdaq 100 (AJOUTÉ 29 Juin 2026 — Target 80% WR)
    # Backtest avec coûts: WR 74.0%, PF 1.09, DD 6.4%
    # ═══════════════════════════════════════════════════════════════════════
    "US100.cash": {
        # ═══════════════════════════════════════════════════════════════════
        # SOLUTION A — 27 Juillet 2026: Optimisé FTMO
        # Threshold 4.0×ATR, SL 1.5×ATR, TP 6.0×ATR
        # Trailing: DÉSACTIVÉ, Partial TP: DÉSACTIVÉ
        # Backtest 2012-2026: PF=1.24, DD=5.1%, +$24,434 ✅✅ FTMO
        # Validation 2024-2026: PF=1.24, DD=5.1%, +$24,434
        # ═══════════════════════════════════════════════════════════════════
        "momentum_period": 20,
        # 🔧 28 Août 2026: SL 1.5→2.5×ATR (uniformisé). TP 6.0→10.0 (RR 4.0 conservé).
        "sl_atr_trending": 2.5,
        "tp_atr_trending": 10.0,
        "sl_atr_ranging": 2.5,
        "tp_atr_ranging": 10.0,
        "threshold_trending": 3.0,  # 🔧 ↓ 4.0→3.0 (Robot Manager 28 Juillet — plus de signaux)
        "threshold_ranging": 3.0,  # 🔧 ↓ 4.0→3.0 (unifié)
        "adx_slope_threshold": -9.0,
        "adx_slope_threshold_strong": -14.0,
        "pullback_band_trending": 0.3,
        "pullback_band_ranging": 0.2,
        "preferred_hours": list(range(24)),  # 🔧 3 Sept 2026: 24/5 (FTMO autorise)
        "news_minutes_before": 15,
        "news_minutes_after": 15,
        "min_score": 0.75,  # ↓ 0.60→0.50 (threshold 3.0×ATR assez sélectif)
        "adx_thresh": 22,
        "min_rr": 2.0,  # ↑ 1.5→2.0 (TP 6.0 / SL 1.5 = RR 4.0)
        "risk_mult": 1.0,  # 🔧 3 Sept 2026: dégelé (récolte continuation)
        "cooldown_minutes": 0,
        "auto_pause_losses": 999,
    },
    # ═══════════════════════════════════════════════════════════════════════
    # US500.cash H1 — S&P 500 (AJOUTÉ 29 Juin 2026 — Target 80% WR)
    # Backtest avec coûts: WR 73.6%, PF 1.04, DD 10.5%
    # ═══════════════════════════════════════════════════════════════════════
    "US500.cash": {
        # ═══════════════════════════════════════════════════════════════════
        # SOLUTION A — 27 Juillet 2026: Optimisé FTMO
        # Threshold 4.0×ATR, SL 1.5×ATR, TP 6.0×ATR
        # Trailing: DÉSACTIVÉ, Partial TP: DÉSACTIVÉ
        # Backtest 2012-2026: PF=1.39, DD=5.5%, +$81,667 ✅✅ FTMO
        # Validation 2024-2026: PF=1.33, DD=3.5%, +$28,882
        # ═══════════════════════════════════════════════════════════════════
        "momentum_period": 20,
        # 🔧 28 Août 2026: SL 1.5→2.5×ATR (uniformisé). TP 6.0→10.0 (RR 4.0 conservé).
        "sl_atr_trending": 2.5,
        "tp_atr_trending": 10.0,
        "sl_atr_ranging": 2.5,
        "tp_atr_ranging": 10.0,
        "threshold_trending": 3.0,  # 🔧 ↓ 4.0→3.0 (Robot Manager 28 Juillet — plus de signaux)
        "threshold_ranging": 3.0,  # 🔧 ↓ 4.0→3.0 (unifié)
        "adx_slope_threshold": -8.0,
        "adx_slope_threshold_strong": -12.0,
        "pullback_band_trending": 0.3,
        "pullback_band_ranging": 0.2,
        "preferred_hours": list(range(24)),  # 🔧 3 Sept 2026: 24/5 (FTMO autorise)
        "news_minutes_before": 15,
        "news_minutes_after": 15,
        "min_score": 0.65,
        "adx_thresh": 22,
        "min_rr": 2.0,
        "risk_mult": 1.0,
        "cooldown_minutes": 0,
        "auto_pause_losses": 999,
    },
    "XAGUSD": {
        "momentum_period": 20,
        # 🔧 28 Août 2026: SL 2.0/1.5→2.5×ATR (uniformisé). TP 5.0/4.0→6.25/6.67 (RR 2.5/2.67).
        "sl_atr_trending": 2.5,
        "tp_atr_trending": 6.25,
        "sl_atr_ranging": 2.5,
        "tp_atr_ranging": 6.67,
        "threshold_trending": 2.5,  # 🔧 FIX #7: Assoupli (était 3.0)  # 🔧 FIX #5
        "threshold_ranging": 2.0,  # 🐛 FIX #14: 2.0 en ranging (était 2.5 — identique à trending!)
        "adx_slope_threshold": -8.0,
        "adx_slope_threshold_strong": -12.0,
        "pullback_band_trending": 0.5,
        "pullback_band_ranging": 0.3,
        "preferred_hours": list(range(24)),
        "news_minutes_before": 5,
        "news_minutes_after": 5,
        # 🔒 SOFT BLOCK 1er Juillet 2026 — WR 33.3%, -$1,265 (hémorragie)
        # risk_mult=0.05 = 95% de réduction de risque, micro-lot 0.05 (×5)
        # min_score=0.90 = seuls les signaux exceptionnels passent
        # Si les signaux redeviennent bons, l'OL détectera la guérison
        # 🔧 FIX_SUPREME_COUNCIL 2 Juillet 2026: ADX 22→25 pour réduire faux signaux
        "min_score": 0.65,  # très sélectif
        "adx_thresh": 25,
        "min_rr": 2.0,
        "risk_mult": 0.0,  # 🔴 GEL TEMPORAIRE par automation: freeze symbol due to large losses
        "cooldown_minutes": 0,
        "auto_pause_losses": 999,
    },
    # ═══════════════════════════════════════════════════════════════════════
    # GBPJPY H1 — Livre Sterling / Yen Japonais (AJOUTÉ 29 Juin 2026)
    # Caractéristiques: Forex cross, forte volatilité, tendances longues
    # Backtest 12+ ans: WR 68.0%, PF 1.36, +$624,210 (meilleur PnL)
    # ═══════════════════════════════════════════════════════════════════════
    "GBPJPY": {
        "momentum_period": 20,
        # 🔧 28 Août 2026: SL 2.0/1.5→2.5×ATR (uniformisé). TP 5.0/4.0→6.25/6.67 (RR 2.5/2.67).
        "sl_atr_trending": 2.5,
        "tp_atr_trending": 6.25,
        "sl_atr_ranging": 2.5,
        "tp_atr_ranging": 6.67,
        "threshold_trending": 2.5,  # 🔧 FIX #7: Assoupli (était 3.0)  # 🔧 FIX #5
        "threshold_ranging": 2.0,  # 🐛 FIX #14: 2.0 en ranging (était 2.5 — identique à trending!)
        "adx_slope_threshold": -8.0,
        "adx_slope_threshold_strong": -12.0,
        "pullback_band_trending": 0.5,
        "pullback_band_ranging": 0.3,
        "preferred_hours": list(range(24)),
        "news_minutes_before": 5,
        "news_minutes_after": 5,
        "min_score": 0.65,  # 🔧 10 Juil 2026: débloqué min_score=0.85 — 11 trades, -$1, test progressif
        "adx_thresh": 22,
        "min_rr": 1.6,
        "risk_mult": 1.0,  # 🔓 DÉBLOQUÉ 29 Juillet 2026
        "cooldown_minutes": 0,
        "auto_pause_losses": 999,
    },
    # ═══════════════════════════════════════════════════════════════════════
    # JP225.cash H1 — Nikkei 225 (AJOUTÉ 29 Juin 2026)
    # Caractéristiques: Indice japonais, tendances longues, liquidité Asie
    # Backtest 12+ ans: WR 67.6%, PF 1.18, +$236,660, DD 8.4%
    # ═══════════════════════════════════════════════════════════════════════
    "JP225.cash": {
        # ═══════════════════════════════════════════════════════════════════
        # SOLUTION A — 27 Juillet 2026: Optimisé FTMO
        # Threshold 4.0×ATR, SL 1.5×ATR, TP 6.0×ATR
        # Trailing: DÉSACTIVÉ, Partial TP: DÉSACTIVÉ
        # Backtest 2012-2026: PF=1.15, DD=12.4% (borderline)
        # Validation 2024-2026: PF=1.21, DD=6.2%, +$19,564
        # risk_mult=0.7 pour garder DD sous 10%
        # ═══════════════════════════════════════════════════════════════════
        "momentum_period": 20,
        # 🔧 28 Août 2026: SL 1.5→2.5×ATR (uniformisé). TP 6.0→10.0 (RR 4.0 conservé).
        "sl_atr_trending": 2.5,
        "tp_atr_trending": 10.0,
        "sl_atr_ranging": 2.5,
        "tp_atr_ranging": 10.0,
        "threshold_trending": 3.0,  # 🔧 ↓ 4.0→3.0 (Robot Manager 28 Juillet — plus de signaux)
        "threshold_ranging": 3.0,  # 🔧 ↓ 4.0→3.0 (unifié)
        "adx_slope_threshold": -9.0,
        "adx_slope_threshold_strong": -14.0,
        "pullback_band_trending": 0.3,
        "pullback_band_ranging": 0.2,
        "preferred_hours": list(range(24)),  # 🔧 3 Sept 2026: 24/5 (FTMO autorise)
        "news_minutes_before": 15,
        "news_minutes_after": 15,
        "min_score": 0.69,  # 🔧 2 Sept 2026: user request
        "adx_thresh": 22,
        "min_rr": 2.0,  # ↑ 1.5→2.0
        "risk_mult": 1.0,  # 🔓 DÉBLOQUÉ 29 Juillet 2026
        "cooldown_minutes": 0,
        "auto_pause_losses": 999,
    },
    # ═══════════════════════════════════════════════════════════════════════
    # USOIL.cash H1 — US Oil (AJOUTÉ 29 Juin 2026)
    # Caractéristiques: Commodity volatile, news-driven, sessions US
    # Backtest 12+ ans: WR 68.4%, PF 1.06, +$24,281, DD 1.9% (très bas!)
    # ═══════════════════════════════════════════════════════════════════════
    "USOIL.cash": {
        "momentum_period": 20,
        # 🔧 28 Août 2026: SL 2.0/1.5→2.5×ATR (uniformisé). TP 5.0/4.0→6.25/6.67 (RR 2.5/2.67).
        "sl_atr_trending": 2.5,
        "tp_atr_trending": 6.25,
        "sl_atr_ranging": 2.5,
        "tp_atr_ranging": 6.67,
        "threshold_trending": 2.5,  # 🔧 FIX #7: Assoupli (était 3.0)  # 🔧 FIX #5
        "threshold_ranging": 2.0,  # 🐛 FIX #14: 2.0 en ranging (était 2.5 — identique à trending!)
        "adx_slope_threshold": -8.0,
        "adx_slope_threshold_strong": -12.0,
        "pullback_band_trending": 0.5,
        "pullback_band_ranging": 0.3,
        "preferred_hours": list(range(24)),  # 24/7
        "news_minutes_before": 15,
        "news_minutes_after": 15,
        "min_score": 0.69,  # 🔧 2 Sept 2026: user request
        "adx_thresh": 22,
        "min_rr": 1.6,
        "risk_mult": 1.0,  # 🔓 DÉBLOQUÉ 29 Juillet 2026
        "cooldown_minutes": 0,
        "auto_pause_losses": 999,
    },
    # ═══════════════════════════════════════════════════════════════════════
    # ═══════════════════════════════════════════════════════════════════════
    # EURGBP H1 — Euro / Livre Sterling (AJOUTÉ 1er Juillet 2026)
    # Forex cross, FAIBLE volatilité, comportement de range, sessions Londres
    # Backtest 12+ ans: WR 67.0%, range trading dominant
    # ═══════════════════════════════════════════════════════════════════════
    "EURGBP": {
        "momentum_period": 20,
        # 🔧 28 Août 2026: SL 2.0/1.5→2.5×ATR (uniformisé). TP 4.0/3.0→6.25/6.67 (RR 2.5/2.67).
        "sl_atr_trending": 2.5,
        "tp_atr_trending": 6.25,
        "sl_atr_ranging": 2.5,
        "tp_atr_ranging": 6.67,
        "threshold_trending": 2.5,  # 🔧 FIX #7: Assoupli (était 3.0)  # 🔧 FIX #5
        "threshold_ranging": 2.0,  # 🐛 FIX #14: 2.0 en ranging (était 2.5 — identique à trending!)
        "adx_slope_threshold": -8.0,  # 🔧 29 Juil: assoupli ×2 (était -4.0)
        "adx_slope_threshold_strong": -12.0,
        "pullback_band_trending": 0.5,
        "pullback_band_ranging": 0.3,
        "preferred_hours": list(range(24)),  # 🔧 3 Sept 2026: 24/5 (FTMO autorise)
        "news_minutes_before": 5,
        "news_minutes_after": 5,
        "min_score": 0.79,  # 🔧 14 Juil 2026: ↓ 0.75→0.60 — rétabli, le dynamic min_score (signal_validator) monte si WR<50%
        "adx_thresh": 20,
        "min_rr": 1.5,
        "risk_mult": 1.0,  # 🔧 ↑ 0.8→1.0 (bon performer)
        "cooldown_minutes": 0,
        "auto_pause_losses": 999,
    },
    # ═══════════════════════════════════════════════════════════════════════
    # ═══════════════════════════════════════════════════════════════════════
    # SOLUSD H1 — Solana (AJOUTÉ 1er Juillet 2026)
    # Crypto haute performance, forte volatilité, 24/7
    # ═══════════════════════════════════════════════════════════════════════
    "SOLUSD": {
        "momentum_period": 20,
        # 🔧 2 Sept 2026: SL 2.5→2.0 (crypto edge prouvé, SL plus serré = RR meilleur)
        "sl_atr_trending": 2.0,
        "tp_atr_trending": 5.0,
        "sl_atr_ranging": 2.0,
        "tp_atr_ranging": 5.0,
        "threshold_trending": 2.5,  # 🔧 FIX #7: Assoupli (était 3.0)  # 🔧 FIX #5  # crypto: seuils abaissés
        "threshold_ranging": 2.0,  # 🐛 FIX #14: 2.0 en ranging (était 2.5 — identique à trending!)
        "adx_slope_threshold": -8.0,
        "adx_slope_threshold_strong": -12.0,
        "pullback_band_trending": 0.8,  # bandes larges (volatilité)
        "pullback_band_ranging": 0.5,
        "preferred_hours": list(range(24)),  # 24/7
        "news_minutes_before": 0,
        "news_minutes_after": 0,
        "min_score": 0.65,  # 🔧 14 Juil 2026: ↓ 0.75→0.60 — rétabli, le dynamic min_score (signal_validator) monte si WR<50%
        "adx_thresh": 17,  # 🔧 3 Sept 2026: 20→17. SOLUSD ADX live=16-17, seuil 20 bloquait 61 signaux
        "min_rr": 1.8,
        "risk_mult": 1.0,  # 🔓 DÉBLOQUÉ 29 Juillet 2026
        "cooldown_minutes": 0,
        "auto_pause_losses": 999,
    },
    # ═══════════════════════════════════════════════════════════════════════
    # BNBUSD H1 — Binance Coin (AJOUTÉ 1er Juillet 2026)
    # Crypto exchange token, volatilité modérée, 24/7
    # ═══════════════════════════════════════════════════════════════════════
    "BNBUSD": {
        "momentum_period": 20,
        # 🔧 28 Août 2026: SL 2.5/2.0→2.5×ATR (uniformisé). TP 5.0/4.0→5.0 (RR 2.0 conservé).
        "sl_atr_trending": 2.5,
        "tp_atr_trending": 5.0,
        "sl_atr_ranging": 2.5,
        "tp_atr_ranging": 5.0,
        "threshold_trending": 2.5,  # 🔧 FIX #7: Assoupli (était 3.0)  # 🔧 FIX #5
        "threshold_ranging": 2.0,  # 🐛 FIX #14: 2.0 en ranging (était 2.5 — identique à trending!)
        "adx_slope_threshold": -8.0,
        "adx_slope_threshold_strong": -12.0,
        "pullback_band_trending": 0.8,
        "pullback_band_ranging": 0.5,
        "preferred_hours": list(range(24)),
        "news_minutes_before": 0,
        "news_minutes_after": 0,
        "min_score": 0.65,  # 🔧 14 Juil 2026: ↓ 0.75→0.60 — rétabli, le dynamic min_score (signal_validator) monte si WR<50%
        "adx_thresh": 20,  # Crypto, seuil bas (ETHUSD pattern)
        "min_rr": 1.8,
        "risk_mult": 1.0,  # 🔓 DÉBLOQUÉ 29 Juillet 2026
        "cooldown_minutes": 0,
        "auto_pause_losses": 999,
    },
    # ═══════════════════════════════════════════════════════════════════════
    # GER40.cash H1 — DAX 40 Allemand (AJOUTÉ 1er Juillet 2026)
    # Indice européen, blue chips, sessions Londres
    # ═══════════════════════════════════════════════════════════════════════
    "GER40.cash": {
        "momentum_period": 20,
        # 🔧 28 Août 2026: SL 1.5/1.2→2.5×ATR (uniformisé). TP 5.0/4.0→10.0 (RR 4.0 conservé).
        "sl_atr_trending": 2.5,
        "tp_atr_trending": 10.0,
        "sl_atr_ranging": 2.5,
        "tp_atr_ranging": 10.0,
        "threshold_trending": 2.5,  # 🔧 FIX #7: Assoupli (était 3.0)  # 🔧 FIX #5
        "threshold_ranging": 2.0,  # 🐛 FIX #14: 2.0 en ranging (était 2.5 — identique à trending!)
        "adx_slope_threshold": -8.0,
        "adx_slope_threshold_strong": -12.0,
        "pullback_band_trending": 0.3,
        "pullback_band_ranging": 0.2,
        "preferred_hours": list(range(24)),  # 🔧 3 Sept 2026: 24/5 (FTMO autorise)
        "news_minutes_before": 15,
        "news_minutes_after": 15,
        "min_score": 0.65,  # 🔧 14 Juil 2026: ↓ 0.75→0.60 — rétabli, le dynamic min_score (signal_validator) monte si WR<50%
        "adx_thresh": 22,
        "min_rr": 1.5,
        "risk_mult": 1.0,
        "cooldown_minutes": 0,
        "auto_pause_losses": 999,
    },
    # ═══════════════════════════════════════════════════════════════════════
    # UK100.cash H1 — FTSE 100 Britannique (AJOUTÉ 1er Juillet 2026)
    # Indice défensif, valeurs stables, sessions Londres
    # ═══════════════════════════════════════════════════════════════════════
    "UK100.cash": {
        "momentum_period": 20,
        # 🔧 28 Août 2026: SL 1.5/1.2→2.5×ATR (uniformisé). TP 5.0/4.0→10.0 (RR 4.0 conservé).
        "sl_atr_trending": 2.5,
        "tp_atr_trending": 10.0,
        "sl_atr_ranging": 2.5,
        "tp_atr_ranging": 10.0,
        "threshold_trending": 2.5,  # 🔧 FIX #7: Assoupli (était 3.0)  # 🔧 FIX #5
        "threshold_ranging": 2.0,  # 🐛 FIX #14: 2.0 en ranging (était 2.5 — identique à trending!)
        "adx_slope_threshold": -8.0,
        "adx_slope_threshold_strong": -12.0,
        "pullback_band_trending": 0.3,
        "pullback_band_ranging": 0.2,
        "preferred_hours": list(range(24)),  # 🔧 3 Sept 2026: 24/5 (FTMO autorise)
        "news_minutes_before": 15,
        "news_minutes_after": 15,
        "min_score": 0.65,  # 🔧 14 Juil 2026: ↓ 0.75→0.60 — rétabli, le dynamic min_score (signal_validator) monte si WR<50%
        "adx_thresh": 22,  # Aligné YAML — indice UK, standard 22
        "min_rr": 1.8,
        "risk_mult": 1.0,  # 🔓 DÉBLOQUÉ 29 Juillet 2026
        "cooldown_minutes": 0,
        "auto_pause_losses": 999,
    },
    # ═══════════════════════════════════════════════════════════════════════
    # UKOIL.cash H1 — Brent Oil (AJOUTÉ 1er Juillet 2026)
    # Commodité pétrolière, news-driven, sessions Londres + NY
    # ═══════════════════════════════════════════════════════════════════════
    "UKOIL.cash": {
        "momentum_period": 20,
        # 🔧 28 Août 2026: SL 2.0/1.5→2.5×ATR (uniformisé). TP 5.0/4.0→6.25/6.67 (RR 2.5/2.67).
        "sl_atr_trending": 2.5,
        "tp_atr_trending": 6.25,
        "sl_atr_ranging": 2.5,
        "tp_atr_ranging": 6.67,
        "threshold_trending": 2.5,  # 🔧 FIX #7: Assoupli (était 3.0)  # 🔧 FIX #5
        "threshold_ranging": 2.0,  # 🐛 FIX #14: 2.0 en ranging (était 2.5 — identique à trending!)
        "adx_slope_threshold": -8.0,
        "adx_slope_threshold_strong": -12.0,
        "pullback_band_trending": 0.5,
        "pullback_band_ranging": 0.3,
        "preferred_hours": list(range(24)),  # 🔧 3 Sept 2026: 24/5 (FTMO autorise)
        "news_minutes_before": 15,
        "news_minutes_after": 15,
        "min_score": 0.65,  # 🔧 14 Juil 2026: ↓ 0.75→0.60 — rétabli, le dynamic min_score (signal_validator) monte si WR<50%
        "adx_thresh": 22,
        "min_rr": 1.6,
        "risk_mult": 1.0,  # 🔓 DÉBLOQUÉ 29 Juillet 2026
        "cooldown_minutes": 0,
        "auto_pause_losses": 999,
    },
    # ═══════════════════════════════════════════════════════════════════════
    # NATGAS.cash H1 — Natural Gas (AJOUTÉ 1er Juillet 2026)
    # Commodité, volatilité EXTRÊME, news-driven, sessions NY
    # ═══════════════════════════════════════════════════════════════════════
    "NATGAS.cash": {
        "momentum_period": 20,
        # 🔧 28 Août 2026: SL 2.0/1.5→2.5×ATR (uniformisé). TP 5.0/3.5→6.25/6.67 (RR 2.5/2.67).
        "sl_atr_trending": 2.5,
        "tp_atr_trending": 6.25,
        "sl_atr_ranging": 2.5,
        "tp_atr_ranging": 6.67,
        "threshold_trending": 2.5,  # 🔧 FIX #7: Assoupli (était 3.0)  # 🔧 FIX #5
        "threshold_ranging": 2.0,  # 🐛 FIX #14: 2.0 en ranging (était 2.5 — identique à trending!)
        "adx_slope_threshold": -8.0,
        "adx_slope_threshold_strong": -12.0,
        "pullback_band_trending": 0.5,
        "pullback_band_ranging": 0.3,
        "preferred_hours": list(range(24)),  # 🔧 3 Sept 2026: 24/5 (FTMO autorise)  # NY session
        "news_minutes_before": 15,
        "news_minutes_after": 15,
        "min_score": 0.65,  # 🔧 14 Juil 2026: ↓ 0.75→0.60 — rétabli, le dynamic min_score (signal_validator) monte si WR<50%
        "adx_thresh": 22,
        "min_rr": 1.8,
        "risk_mult": 1.0,  # 🔓 DÉBLOQUÉ 29 Juillet 2026
        "cooldown_minutes": 0,
        "auto_pause_losses": 999,
    },
}

# Fallback par défaut — utilisé si un symbole n'a pas de config personnalisée
# Tous les 27 symboles actifs ont leur propre config, donc ce fallback
# n'est utilisé que pour les symboles non listés (cas d'erreur).
DEFAULT_SYMBOL_CONFIG = {
    # ── Stratégie (Strategy Registry) ──────────────────────────────────
    "strategy": "MOM20x3",  # Stratégie par défaut pour tous les symboles
    # ── Trading ────────────────────────────────────────────────────────
    "momentum_period": 20,
    "sl_atr_trending": 2.5,
    "tp_atr_trending": 6.25,
    "sl_atr_ranging": 2.5,
    "tp_atr_ranging": 6.67,  # 🔧 28 Août 2026: SL 2.0/1.5→2.5×ATR, TP aligné (RR 2.5/2.67)
    "threshold_trending": 2.5,  # 🔧 FIX #7: Assoupli (était 3.0)  # 🔧 FIX #5: Ultra-conservateur (était 2.5)
    "threshold_ranging": 2.0,  # 🐛 FIX #14: 2.0 en ranging (était 2.5 — identique à trending!): Ultra-conservateur (était 2.0)
    "adx_slope_threshold": -8.0,  # 🔧 29 Juil: unifié forex (était -9.0)
    "adx_slope_threshold_strong": -12.0,  # 🔧 29 Juil: unifié forex (était -14.0)
    "pullback_band_trending": 0.5,
    "pullback_band_ranging": 0.3,
    "timeframe": "H1",
    # ── Horaires ──────────────────────────────────────────────────────
    "preferred_hours": list(range(24)),
    "news_minutes_before": 5,
    "news_minutes_after": 5,
    # ── Filtres & Score ───────────────────────────────────────────────
    "min_score": 0.65,  # 🔧 14 Juil 2026: ↓ 0.75→0.60 — rétabli, le dynamic min_score (signal_validator) monte si WR<50%
    "conf": 0.85,  # seuil HIGH_CONF confidence
    "adx_thresh": 22,  # ADX minimum pour régime TREND
    "min_rr": 1.5,  # RR minimum exigé
    # ── Risque ────────────────────────────────────────────────────────
    "risk_mult": 1.0,  # multiplicateur risque (1.0 = normal)
    "cooldown_minutes": 0,  # pause après perte
    "auto_pause_losses": 999,  # pertes consécutives avant pause
    # ── Lots ──────────────────────────────────────────────────────────
    "lot_base": 0.05,  # lot minimum de départ (×5)
    "lot_max": 0.50,  # lot maximum après progression WR (×5)
    # ── Spreads & Positions ───────────────────────────────────────────
    "max_spread_points": 120,  # spread maximum en points
    "max_positions_per_symbol": 6,  # positions max par symbole
    # ── Volume Indicators ─────────────────────────────────────────────
    "cmf_threshold": 0.10,  # seuil Chaikin Money Flow
    "obv_div_penalty_high": 0.70,  # pénalité OBV divergence forte
    "obv_div_penalty_low": 0.85,  # pénalité OBV divergence faible
    # ── Protection ────────────────────────────────────────────────────
    "daily_loss_limit_pct": 0.02,  # perte journalière max (2%)
    "max_dd_pct": 0.10,  # drawdown max depuis peak (10%)
}

# Ensure commonly used symbols from `config/default.yaml` exist in SYMBOL_CONFIG
# If absent, inject a safe default based on DEFAULT_SYMBOL_CONFIG to avoid
# runtime inconsistencies between YAML doc and in-code source-of-truth.
_MISSING_SYMBOLS = [
    'GBPUSD', 'USDCAD', 'AUDUSD', 'USDCHF', 'EURJPY', 'AUDJPY'
]
# 🔧 19 Août 2026 (Backtest Optimizations): optimisation SL/TP + session LDN-NY pour
# les paires forex majeures. SL 2.0→3.0 / TP 5.0→7.5 (trending), 1.5→2.25 / 4.0→6.0
# (ranging), RR 2.5 conservé. preferred_hours 13-17h GMT (London-NY overlap).
# Backtest 7 paires H1 2012-2026: WR 57.4→64.3%, DD 30.3→10.2%, pertes −76%.
_FOREX_OPT_20260819 = {
    'sl_atr_trending': 2.5,
    'tp_atr_trending': 6.25,
    'sl_atr_ranging': 2.5,
    'tp_atr_ranging': 6.67,
    'preferred_hours': list(range(24)),  # 🔧 3 Sept 2026: 24/5 (FTMO autorise)
}
for _sym in _MISSING_SYMBOLS:
    if _sym not in SYMBOL_CONFIG:
        SYMBOL_CONFIG[_sym] = DEFAULT_SYMBOL_CONFIG.copy()
        SYMBOL_CONFIG[_sym].update({
            'timeframe': 'H1',
            'preferred_hours': list(range(24)),
        })
    if _sym in ('GBPUSD', 'USDCAD', 'AUDUSD', 'USDCHF'):
        SYMBOL_CONFIG[_sym].update(_FOREX_OPT_20260819)


# Compatibilité avec l'ancien code (momentum periods)
# 🔒 MappingProxyType = immutable — empêche les modifications à chaud depuis Phase 3
_SYMBOL_MOMENTUM_PERIODS = {sym: cfg["momentum_period"] for sym, cfg in SYMBOL_CONFIG.items()}
SYMBOL_MOMENTUM_PERIODS = MappingProxyType(_SYMBOL_MOMENTUM_PERIODS)

# Périodes par défaut
DEFAULT_SYMBOL_MOMENTUM_PERIOD = 20

# === Fix P8: Setters/getters pour momentum periods (remplace l'accès direct au dict mutable) ===
_MOMENTUM_OVERRIDES: dict[str, int] = {}  # overrides à chaud (OnlineLearner)


def set_momentum_period(symbol: str, period: int):
    """Définit une période momentum personnalisée pour un symbole (OnlineLearner)."""
    _MOMENTUM_OVERRIDES[symbol] = period


def get_momentum_period(symbol: str) -> int | None:
    """Retourne la période override pour un symbole, ou None."""
    return _MOMENTUM_OVERRIDES.get(symbol)


def _get_symbol_config(symbol: str | None) -> dict:
    """Retourne la configuration complète d'un symbole."""
    if symbol is None:
        return DEFAULT_SYMBOL_CONFIG
    return SYMBOL_CONFIG.get(symbol, DEFAULT_SYMBOL_CONFIG)


def get_symbol_full_config(symbol: str) -> dict:
    """Retourne la config complète d'un symbole, champs manquants comblés par DEFAULT."""
    cfg = DEFAULT_SYMBOL_CONFIG.copy()
    if symbol in SYMBOL_CONFIG:
        cfg.update(SYMBOL_CONFIG[symbol])
    return cfg


def _get_momentum_period(symbol: str | None) -> int:
    """Retourne la période momentum adaptée au symbole.
    Vérifie d'abord _MOMENTUM_OVERRIDES (OnlineLearner), puis SYMBOL_MOMENTUM_PERIODS.
    🔧 FIX #6 (3 Juillet 2026): _MOMENTUM_OVERRIDES était ignoré — l'optimisation des périodes
    par OnlineLearner ne produisait AUCUN effet sur les signaux."""
    if symbol is None:
        return DEFAULT_SYMBOL_MOMENTUM_PERIOD
    return _MOMENTUM_OVERRIDES.get(symbol) or SYMBOL_MOMENTUM_PERIODS.get(symbol, DEFAULT_SYMBOL_MOMENTUM_PERIOD)


def mom20x3_signal(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    open_: np.ndarray | None = None,
    period: int = 20,
    atr_period: int = 14,
    adx_period: int = 14,
    symbol: str | None = None,
    custom_thresh_trending: float | None = None,
    custom_thresh_ranging: float | None = None,
) -> dict | None:
    """Génère un signal MOM20x3 avec filtres ADX slope, +DI/-DI, pullback.

    Args:
        close: np.array de prix de clôture (au moins period + 1 éléments)
        high: np.array de prix hauts
        low: np.array de prix bas
        open_: np.array de prix d'ouverture (optionnel, fallback close × 0.999)
        period: période du momentum (défaut 20)
        atr_period: période ATR (défaut 14)
        adx_period: période ADX (défaut 14)
        symbol: nom du symbole pour période adaptative (None = défaut)
        custom_thresh_trending: seuil trending personnalisé (OnlineLearner)
        custom_thresh_ranging: seuil ranging personnalisé (OnlineLearner)

    Returns:
        dict avec 'action' ('BUY'/'SELL'), 'score', 'atr', 'adx',
        'plus_di', 'minus_di', 'adx_slope', 'pullback_active',
        'sl_atr', 'tp_atr', 'thresh_used', 'ol_thresh_applied',
        '_regime', 'structure_trend', 'structure_score'
        ou None si pas de signal
    """
    # Configuration spécifique par symbole
    sym_cfg = _get_symbol_config(symbol)

    # Période adaptative par symbole
    if symbol is not None:
        period = _get_momentum_period(symbol)

    if len(close) < period + max(adx_period, 5):
        return None
    if len(high) < max(atr_period + 5, 30):
        return None
    if len(low) < max(atr_period + 5, 30):
        return None

    # === ATR ===
    atr_val = atr(high, low, close, atr_period)
    if atr_val is None or len(atr_val) == 0:
        return None
    current_atr = float(atr_val[-1])
    if current_atr <= 0:
        current_atr = 0.001

    # === ADX complet avec +DI/-DI ===
    adx_val, plus_di, minus_di = adx(high, low, close, adx_period)

    # === ADX slope : vérifier si ADX est en hausse (fix P3: adx_arrays pour série temporelle complète) ===
    adx_slope = 0.0
    half = min(14, len(close) // 3)
    adx_arr, _, _ = adx_arrays(high, low, close, adx_period)
    valid = ~np.isnan(adx_arr)
    if np.sum(valid) > half:
        adx_last = float(adx_arr[valid][-1])
        adx_prev = float(adx_arr[valid][-half - 1])
        adx_slope = adx_last - adx_prev

    # === Seuil adaptatif selon ADX (calculé avant les filtres) ===
    # Support OnlineLearner: custom_thresh_* surcharge les hardcodés
    # Utilise les seuils spécifiques au symbole
    is_trending = adx_val >= 22  # unifié avec regime.py (22 entrée / 18 sortie)
    if is_trending:
        thresh = custom_thresh_trending if custom_thresh_trending is not None else sym_cfg["threshold_trending"]
    else:
        thresh = custom_thresh_ranging if custom_thresh_ranging is not None else sym_cfg["threshold_ranging"]
    thresh = max(THRESHOLD_MIN, min(THRESHOLD_MAX, thresh))

    # === Momentum brut (période adaptative) avec garde NaN ===
    mom = float(close[-1] - close[-period - 1])
    if np.isnan(mom) or np.isinf(mom):
        logger.debug(f"  [MOM20x3] {symbol}: momentum NaN/Inf → skip")
        return None
    mom_abs = abs(mom)
    threshold_value = thresh * current_atr

    # Score de confiance (0.0 - 1.0) — utilisé par les filtres
    if mom_abs > 0:
        raw_score = min(1.0, mom_abs / (threshold_value * 2))
    else:
        raw_score = 0.0

    # === Wick Ratio Filter (FIX 30 Août 2026 — Alpha Researcher) ===
    # Détecte la pression intra-bougie : un breakout haussier avec long upper wick
    # = pression vendeuse (rejet du prix). Un breakout baissier avec long lower wick
    # = pression acheteuse (rejet du prix). Seuil 0.40 = la moitié de la bougie est une mèche.
    try:
        last_h = float(high[-1])
        last_l = float(low[-1])
        last_c = float(close[-1])
        last_o = float(close[-2]) if len(close) >= 2 else last_c  # open ≈ previous close
        candle_range = last_h - last_l
        if candle_range > 0:
            if mom > 0:  # BUY signal → vérifier upper wick (rejection)
                upper_wick = last_h - max(last_o, last_c)
                wick_ratio = upper_wick / candle_range
                if wick_ratio > 0.40:
                    raw_score *= 0.80
                    logger.debug(f"  [WICK] {symbol}: BUY mais upper wick={wick_ratio:.2f} > 0.40 → score -20%")
            else:  # SELL signal → vérifier lower wick (rejection)
                lower_wick = min(last_o, last_c) - last_l
                wick_ratio = lower_wick / candle_range
                if wick_ratio > 0.40:
                    raw_score *= 0.80
                    logger.debug(f"  [WICK] {symbol}: SELL mais lower wick={wick_ratio:.2f} > 0.40 → score -20%")
    except Exception:
        pass  # fail-open si données manquantes

    # === Momentum Acceleration Filter (FIX 30 Août 2026 — Alpha Researcher) ===
    # Quand le momentum ralentit (mom_current < mom_prev × 0.7), le breakout a
    # moins de force. Élimine les signaux momentum décroissant.
    try:
        if len(close) >= period + 6:
            mom_prev = float(close[-6] - close[-period - 6])
            if not np.isnan(mom_prev) and abs(mom_prev) > 0:
                accel = mom / mom_prev if (mom * mom_prev > 0) else 0
                if accel < 0.7:  # momentum ralentit de >30%
                    raw_score *= 0.85
                    logger.debug(f"  [ACCEL] {symbol}: momentum ralentit ({accel:.2f} < 0.7) → score -15%")
    except Exception:
        pass  # fail-open si données manquantes

    # === Market Structure Filter (ICT/SMC) ===
    # Analyse BOS/CHOCH/OB pour confirmer ou infirmer la direction du signal
    _structure = None
    _struct_trend = "unknown"
    _struct_score = 0
    _unmitigated_obs = 0
    _unmitigated_fvgs = 0
    if analyze_market_structure is not None and len(high) >= 30 and len(low) >= 30:
        try:
            _structure = analyze_market_structure(high, low, close, lookback=100)
            _struct_trend = _structure.get("trend", "unknown")
            _struct_score = _structure.get("score", 0)
            _unmitigated_obs = _structure.get("unmitigated_obs", 0)
            _unmitigated_fvgs = _structure.get("unmitigated_fvgs", 0)

            # Pénalité si structure contredit le signal
            # 🔧 FIX 22 Juin 2026 — ADX guard : si ADX > 25 ET spread DI > 5,
            # l'ADX/DI est plus fiable que la structure ICT/SMC → pas de pénalité.
            # Évite la situation XAUUSD où ADX=32/-DI=27 était pénalisé
            # par une structure "bullish" ICT à contre-tendance.
            _adx_strong = adx_val >= 22 and abs(plus_di - minus_di) > 5
            if mom > 0 and _struct_trend == "bearish":
                if _adx_strong:
                    logger.debug(
                        f"  [STRUCT] {symbol}: BUY mais ADX={adx_val:.0f} fort → skip penalty structure bearish"
                    )
                else:
                    raw_score *= 0.80  # -20%
                    logger.debug(f"  [STRUCT] {symbol}: BUY mais structure bearish → score -20%")
            elif mom < 0 and _struct_trend == "bullish":
                if _adx_strong:
                    logger.debug(
                        f"  [STRUCT] {symbol}: SELL mais ADX={adx_val:.0f} fort → skip penalty structure bullish"
                    )
                else:
                    raw_score *= 0.80
                    logger.debug(f"  [STRUCT] {symbol}: SELL mais structure bullish → score -20%")

            # Bonus si structure confirme
            if mom > 0 and _struct_trend == "bullish":
                raw_score = min(1.0, raw_score * 1.10)  # +10%
                logger.debug(f"  [STRUCT] {symbol}: BUY + structure bullish → score +10%")
            elif mom < 0 and _struct_trend == "bearish":
                raw_score = min(1.0, raw_score * 1.10)
                logger.debug(f"  [STRUCT] {symbol}: SELL + structure bearish → score +10%")

            # 🔧 FIX 22 Juillet 2026: Suppression pénalités BOS/CHOCH (ICT/SMC).
            # Les BOS/CHOCH sont trop sensibles et créent des faux négatifs.
            # La structure trend (bearish/bullish) et les filtres ADX/DI suffisent.
        except Exception as e:
            logger.debug(f"  [STRUCT] {symbol}: erreur analyse market_structure: {e}")

    # Initialiser les variables utilisées par les filtres
    pullback_active = False  # False par défaut = bloquer sauf si pullback confirmé
    pullback_dist = 0.0
    pullback_band = 0.0

    # === Filtre ADX slope : refuser si ADX baisse significativement ===
    # 🔧 Audit 14 Juin 2026: seuils calibrés par symbole
    #   - XAUUSD: -6.0 standard, -10.0 fort signal (tendances longues)
    #   - BTCUSD: -5.0 standard, -8.0 fort signal (crypto bruité)
    #   - US500.cash: -6.0 standard, -10.0 fort signal (indices stables)
    adx_slope_ok = True
    adx_slope_threshold = sym_cfg["adx_slope_threshold"]

    if raw_score > 0.70:  # restauré valeur originale
        adx_slope_threshold = sym_cfg["adx_slope_threshold_strong"]

    if adx_slope < adx_slope_threshold:
        adx_slope_ok = False
        logger.debug(
            f"  [MOM20x3] ADX slope={adx_slope:.1f} < {adx_slope_threshold:.1f} → skip (raw_score={raw_score:.2f})"
        )

    # === Filtre +DI/-DI directionnel ===
    # Règle : +DI > -DI pour BUY, -DI > +DI pour SELL.
    # Mais en marché transitionnel, le momentum 20p peut encore être haussier
    # alors que les DIs sont déjà baissiers (-DI > +DI). Dans ce cas, on vérifie
    # le momentum COURT (5 périodes) : s'il confirme les DIs, on override le signal.
    #
    # 🐛 FIX 30 Juin 2026: JP225.cash skip direction filter (83 signaux bloqués en Asie)
    # Le filtre est trop conservateur pour les indices asiatiques en trend.
    # Le MOM20x3 + ADX + pullback suffisent comme garde-fous.
    dir_filter_ok = True
    di_suggests = None  # None=pas de suggestion, "BUY" ou "SELL" si override possible
    if symbol == "JP225.cash":
        pass  # skip DI direction filter pour JP225.cash (trop de faux négatifs)
    elif close[-1] > close[-period - 1]:  # BUY bias from momentum
        # H-09: Adoucissement ×0.8 — tolère un écart DI allant jusqu'à 20%
        # avant de bloquer. Évite les rejets en marché transitionnel où le
        # momentum 20p précède le croisement DI de quelques bougies.
        if plus_di <= minus_di * 0.8:
            dir_filter_ok = False
            di_suggests = "SELL"
            logger.debug(
                f"  [MOM20x3] FILTRE DIR: {symbol} BUY mais +DI={plus_di:.1f} <= -DI×0.8={minus_di * 0.8:.1f}"
                f" → vérification short-term"
            )
    else:  # SELL bias
        # H-09: Même adoucissement ×0.8 pour les signaux SELL
        if minus_di <= plus_di * 0.8:
            dir_filter_ok = False
            di_suggests = "BUY"
            logger.debug(
                f"  [MOM20x3] FILTRE DIR: {symbol} SELL mais -DI={minus_di:.1f} <= +DI×0.8={plus_di * 0.8:.1f}"
                f" → vérification short-term"
            )

    # === Signal directionnel (seuil dépassé) ===
    action = None
    score = 0.0

    if mom > 0 and mom_abs >= threshold_value:
        action = "BUY"
        score = 0.35 + raw_score * 0.60  # range [0.35-0.95] pour distinguer signaux faibles/forts
    elif mom < 0 and mom_abs >= threshold_value:
        action = "SELL"
        score = 0.35 + raw_score * 0.60

    if action is None:
        logger.debug(
            f"  [MOM20x3] {symbol}: mom={mom:.5f} < thresh={threshold_value:.5f} "
            f"(thresh={thresh:.2f}×ATR={current_atr:.5f}) → no signal"
        )
        return None

    # === DI Override : si le filtre directionnel bloque mais que le short-term
    # momentum confirme la direction suggérée par les DIs, on override ===
    # Seuil abaissé à 0.5×threshold (= 1.0×ATR au lieu de 2.0×ATR) car le DI cross
    # (+DI > -DI ou -DI > +DI) fournit déjà une confirmation directionnelle forte.
    # Cela permet de rattraper les transitions de marché plus tôt.
    if not dir_filter_ok and di_suggests is not None:
        short_period = 5
        if len(close) >= short_period + 2:
            short_mom = float(close[-1] - close[-short_period - 1])
            short_mom_abs = abs(short_mom)
            # 🔒 DI Override durci : pas d'override en RANGING (ADX<22)
            # En ranging, le short-term momentum n'est que du bruit.
            # Seuil ×4 (2.0×ATR au lieu de 0.5×) → quasiment jamais d'override.
            if adx_val < 22:
                override_thresh = threshold_value * 2.0
            else:
                override_thresh = threshold_value * 0.75  # ~1.5×ATR (moins permissif qu'avant)
            if di_suggests == "SELL" and short_mom < -override_thresh:
                # Short-term momentum confirme la baisse → override en SELL
                action = "SELL"
                short_raw_score = min(1.0, short_mom_abs / (threshold_value * 2))
                score = 0.35 + short_raw_score * 0.60
                dir_filter_ok = True
                logger.info(
                    f"  [MOM20x3] DI OVERRIDE: {symbol} 20p BUY→SELL "
                    f"(short_mom={short_mom:.5f} override_thresh={override_thresh:.5f})"
                )
            elif di_suggests == "BUY" and short_mom > override_thresh:
                # Short-term momentum confirme la hausse → override en BUY
                action = "BUY"
                short_raw_score = min(1.0, short_mom_abs / (threshold_value * 2))
                score = 0.35 + short_raw_score * 0.60
                dir_filter_ok = True
                logger.info(
                    f"  [MOM20x3] DI OVERRIDE: {symbol} 20p SELL→BUY "
                    f"(short_mom={short_mom:.5f} override_thresh={override_thresh:.5f})"
                )

    # 🔧 FIX 22 Juillet 2026: Suppression Liquidity Sweep Filter (ICT/SMC).
    # Les liquidity sweeps sont trop fréquents sur H1/H4 et créent des faux négatifs.
    # Le pullback filter + ADX slope suffisent pour filtrer les faux signaux.

    # === Appliquer les filtres (ADX slope + directionnel) ===
    if not adx_slope_ok:
        logger.debug(f"  [MOM20x3] {action} {symbol}: ADX slope={adx_slope:.1f} → skip")
        return None

    if not dir_filter_ok:
        logger.debug(f"  [MOM20x3] {action} {symbol}: direction filter → skip")
        return None

    # === Pullback check : prix proche de EMA20 ===
    # Bande de pullback calibrée par symbole (ATR-based)
    # XAUUSD: 0.5×ATR trending, 0.3×ATR ranging (or = tendances longues)
    # BTCUSD: 0.6×ATR trending, 0.4×ATR ranging (crypto = plus de bruit)
    # US500.cash: 0.4×ATR trending, 0.25×ATR ranging (indices = plus serré)
    ema_period = 20
    ema20_arr = ema(close, ema_period)
    if len(ema20_arr) > 0 and not np.isnan(ema20_arr[-1]):
        ema20_val = float(ema20_arr[-1])
        if ema20_val > 0:
            pullback_dist = (float(close[-1]) - ema20_val) / ema20_val * 100
            # Bande de pullback ATR-based (calibrée par symbole)
            atr_mult_pullback = sym_cfg["pullback_band_trending"] if is_trending else sym_cfg["pullback_band_ranging"]
            pullback_band = (atr_mult_pullback * current_atr) / ema20_val * 100
            pullback_band = max(0.05, min(1.0, pullback_band))  # clamp 0.05%-1.0%
            if abs(pullback_dist) < pullback_band:
                pullback_active = True

    # === Pullback info + filter pour signaux faibles ===
    # MOM20x3 est une stratégie momentum qui entre sur breakouts, pas sur retracements EMA20.
    # Le pullback est informatif pour les signaux forts (score >= seuil config).
    # Pour les signaux faibles (score < seuil), un pullback actif est requis comme confirmation.
    if not pullback_active and pullback_band > 0:
        if score < PULLBACK_FILTER_SCORE_THRESHOLD:
            logger.info(
                f"  [PULLBACK] {action} {symbol}: score={score:.2f} < {PULLBACK_FILTER_SCORE_THRESHOLD} + pas de pullback "
                f"(dist={pullback_dist:.2f}% > band={pullback_band:.2f}%) → skip"
            )
            return None
        logger.debug(
            f"  [MOM20x3] {action} {symbol}: pas de pullback vers EMA20 "
            f"(dist={pullback_dist:.2f}% > band={pullback_band:.2f}%) → OK (momentum)"
        )

    # SL/TP selon le régime ADX — paramètres spécifiques par symbole
    if is_trending:
        sl_atr = sym_cfg["sl_atr_trending"]
        tp_atr = sym_cfg["tp_atr_trending"]
    else:
        sl_atr = sym_cfg["sl_atr_ranging"]
        tp_atr = sym_cfg["tp_atr_ranging"]

    # Confidence basée sur le score final (qui reflète le momentum utilisé)
    confidence = min(0.95, 0.40 + (score - 0.50) / 0.45 * 0.50)
    confidence = max(0.40, confidence)

    logger.debug(
        f"  [MOM20x3] {action} {symbol or ''} | mom={mom:.5f} thresh={threshold_value:.5f} "
        f"ADX={adx_val:.1f} +DI={plus_di:.1f} -DI={minus_di:.1f} "
        f"slope={adx_slope:.1f} pullback={pullback_active} "
        f"pb_band={pullback_band:.3f}% score={score:.2f}"
    )

    # === Calcul MA20 slope pour régime (remplace price > EMA20, trop simpliste) ===
    # Fix 30 Juin 2026: utilise la pente de la MA20 sur 20 périodes
    # plutôt que price > EMA20 qui donnait des faux TREND_UP sur corrections
    _ma_slope = None
    if len(close) >= 40:
        try:
            ma20_now = float(np.mean(close[-20:]))
            ma20_before = float(np.mean(close[-40:-20]))
            _ma_slope = (ma20_now - ma20_before) / max(abs(ma20_before), 1e-4)
        except Exception:
            _ma_slope = None
    elif len(close) >= 22:
        try:
            _ma_slope = float(close[-1] - close[-21]) / max(abs(close[-21]), 1e-4)
        except Exception:
            _ma_slope = None

    return {
        "action": action,
        "score": min(0.99, score),
        "confidence": confidence,
        "atr": current_atr,
        "adx": round(adx_val, 1),
        "plus_di": round(plus_di, 1),
        "minus_di": round(minus_di, 1),
        "adx_slope": round(adx_slope, 1),
        "pullback_active": pullback_active,
        "pullback_dist": round(pullback_dist, 3),
        "pullback_band": round(pullback_band, 4),
        "sl_atr": sl_atr,
        "tp_atr": tp_atr,
        "thresh_used": round(thresh, 2),
        "ol_thresh_applied": (custom_thresh_trending is not None) or (custom_thresh_ranging is not None),
        "mom_abs": round(mom_abs, 5),
        "threshold_value": round(threshold_value, 5),
        "momentum_period": period,
        "is_trending": is_trending,
        # 🔧 FIX 30 Aout 2026: supprimer le fallback action-based qui assignait TREND_UP
        # à tout BUY en marché trending avec pente ambiguë (entre -0.002 et +0.002).
        # Cela contournait la phase 5 (counter-trend block) et laissait les BUY entrer
        # en TREND_DOWN. Maintenant: pente ambiguë = RANGING (paramètres conservateurs).
        "_regime": "TREND_UP"
        if (is_trending and _ma_slope is not None and _ma_slope > 0.002)
        else "TREND_DOWN"
        if (is_trending and _ma_slope is not None and _ma_slope < -0.002)
        else "RANGING",
        "_model_predictions": {"MOM20x3": action},
        # Market structure data (ICT/SMC)
        "structure_trend": _struct_trend,
        "structure_score": _struct_score,
        "unmitigated_obs": _unmitigated_obs,
        "unmitigated_fvgs": _unmitigated_fvgs,
        "_structure_obs": _structure.get("order_blocks", []) if _structure else [],
    }


class MOM20x3:
    """Wrapper de la stratégie MOM20x3 — utilise la période adaptative par symbole."""

    def __init__(self, rates: list, symbol: str, period: int | None = None):
        self.rates = rates
        self.symbol = symbol
        self.period = period or _get_momentum_period(symbol)
        self._parse_rates()

    def _parse_rates(self):
        if self.rates is None or len(self.rates) < self.period + 5:
            self._close = None
            self._high = None
            self._low = None
            self._open = None
            return
        self._close = np.array([r[4] for r in self.rates], dtype=float)
        self._high = np.array([r[2] for r in self.rates], dtype=float)
        self._low = np.array([r[3] for r in self.rates], dtype=float)
        self._open = np.array([r[1] for r in self.rates], dtype=float)

    def analyze(
        self, custom_thresh_trending: float | None = None, custom_thresh_ranging: float | None = None
    ) -> dict | None:
        if self._close is None:
            return None
        return mom20x3_signal(
            self._close,
            self._high,
            self._low,
            open_=self._open,
            period=self.period,
            symbol=self.symbol,
            custom_thresh_trending=custom_thresh_trending,
            custom_thresh_ranging=custom_thresh_ranging,
        )

    def __call__(self) -> dict | None:
        return self.analyze()
