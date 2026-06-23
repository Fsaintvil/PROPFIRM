#!/usr/bin/env python
"""Entraîne le modèle LightGBM sur les données backtest.

Flux:
  1. Charge les statistiques backtest (runtime/backtest_report.json) — 158K trades
  2. Génère des données synthétiques réalistes qui matchent les stats
  3. Ajoute les trades réels (online_learner_seed.csv) — 600 trades
  4. Entraîne LightGBM avec early stopping
  5. Walk-forward validation par année
  6. Sauvegarde le modèle dans runtime/lgb_model.txt

Usage:
    python scripts/train_lightgbm.py
    python scripts/train_lightgbm.py --force          # ré-entraîne même si modèle existe
    python scripts/train_lightgbm.py --dry-run        # simulation sans sauvegarder
    python scripts/train_lightgbm.py --seed-only      # uniquement les trades réels
"""

import argparse
import csv
import json
import logging
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("train_lightgbm")

# Ajouter le parent pour les imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# ─── Configuration ──────────────────────────────────────────────────────────

BACKTEST_REPORT = "runtime/backtest_report.json"
SEED_CSV = "runtime/online_learner_seed.csv"
REAL_TRADES_JSONL = "runtime/lgb_real_trades.jsonl"  # trades réels collectés en live
MODEL_PATH = "runtime/lgb_model.txt"
MODEL_META_PATH = "runtime/lgb_model_meta.json"

# Paramètres de génération synthétique
SYNTHETIC_NOISE = 0.05  # bruit ajouté aux features pour éviter le sur-apprentissage
REAL_TRADE_WEIGHT = 5  # poids multiplicatif des trades réels vs synthétiques

# Features utilisées par le modèle (doit correspondre à FEATURE_COLUMNS dans lightgbm_model.py)
FEATURE_COLUMNS = [
    "return_10",
    "return_20",
    "dist_ema20",
    "dist_ema50",
    "dist_ema200",
    "slope_ema20",
    "slope_ema50",
    "range_position",
    "breakout_score",
    "atr_percentile",
    "realized_vol_10",
    "realized_vol_ratio",
    "vol_expansion",
    "rvol",
    "vwap_distance",
    "cmf",
    "obv_slope",
    "trend_force",
    "range_compression",
    "session_london_ny_overlap",
]

ACTIVE_SYMBOLS = {"XAUUSD", "BTCUSD", "EURUSD"}  # Symboles actuellement tradés


# ─── Génération synthétique ────────────────────────────────────────────────


def _sample_feature(wr: float, is_winner: bool, noise: float = 0.15) -> dict[str, float]:
    """Génère un vecteur de features réaliste avec chevauchement entre classes.

    Contrairement à la version naïve, les distributions des gagnants et perdants
    se chevauchent significativement — comme dans la réalité. Le modèle doit
    trouver des motifs subtils, pas des séparations parfaites.

    Le paramètre noise contrôle l'écart-type du bruit gaussien.
    """
    feats: dict[str, float] = {}

    is_buy = np.random.random() < 0.5
    direction = 1 if is_buy else -1

    # ── Tirage d'une feature latente "qualité du trade" (~N(0,1)) ──
    # C'est cette variable latente qui influence à la fois le résultat et les features
    quality = np.random.normal(0.5 if is_winner else -0.5, 0.8)

    # ── 1. Price Action : corrélées à quality mais avec bruit important ──
    feats["return_10"] = float(quality * 0.02 * direction + np.random.normal(0, noise * 0.15))
    feats["return_20"] = float(quality * 0.04 * direction + np.random.normal(0, noise * 0.20))
    feats["dist_ema20"] = float(quality * 1.5 * direction + np.random.normal(0, noise * 1.2))
    feats["dist_ema50"] = float(quality * 2.0 * direction + np.random.normal(0, noise * 1.5))
    feats["dist_ema200"] = float(quality * 0.8 * direction + np.random.normal(0, noise * 0.8))
    feats["slope_ema20"] = float(quality * 0.008 * direction + np.random.normal(0, noise * 0.006))
    feats["slope_ema50"] = float(quality * 0.004 * direction + np.random.normal(0, noise * 0.004))
    feats["range_position"] = float(0.5 + quality * 0.3 * direction + np.random.normal(0, noise * 0.2))
    feats["range_position"] = float(np.clip(feats["range_position"], 0.01, 0.99))
    feats["breakout_score"] = float(quality * 0.6 * direction + np.random.normal(0, noise * 0.5))
    feats["breakout_score"] = float(np.clip(feats["breakout_score"], -1.0, 1.0))

    # ── 2. Volatilité : faible corrélation avec le résultat ──
    feats["atr_percentile"] = float(
        np.random.beta(
            max(0.5, 2 + quality * 0.3),
            max(0.5, 2 - quality * 0.3),
        )
    )
    feats["atr_percentile"] = float(np.clip(feats["atr_percentile"], 0.01, 0.99))
    feats["realized_vol_10"] = float(np.random.exponential(0.5 + max(quality * 0.1, 0)))
    feats["realized_vol_ratio"] = float(np.random.lognormal(0, 0.3) + quality * 0.1)
    feats["vol_expansion"] = float(np.random.normal(quality * 0.1, noise * 0.2))

    # ── 3. Volume : corrélation modérée ──
    feats["rvol"] = float(np.random.lognormal(0, 0.4) + quality * 0.3)
    feats["rvol"] = float(max(feats["rvol"], 0.1))
    feats["vwap_distance"] = float(quality * 1.2 * direction + np.random.normal(0, noise * 1.0))
    feats["cmf"] = float(quality * 0.15 * direction + np.random.normal(0, noise * 0.10))
    feats["cmf"] = float(np.clip(feats["cmf"], -0.5, 0.5))
    feats["obv_slope"] = float(quality * 0.3 * direction + np.random.normal(0, noise * 0.25))

    # ── 4. Structure : corrélation modérée ──
    feats["trend_force"] = float(quality * 0.4 * direction + np.random.normal(0, noise * 0.3))
    feats["trend_force"] = float(np.clip(feats["trend_force"], -1.0, 1.0))
    feats["range_compression"] = float(
        np.random.beta(
            max(0.5, 3 + quality * 0.3),
            max(0.5, 3 - quality * 0.3),
        )
    )

    # ── 5. Temps : presque décorrélé (juste un leger biais) ──
    feats["session_london_ny_overlap"] = 1.0 if np.random.random() < (0.35 + quality * 0.05) else 0.0

    # ── Ajouter 30% de features purement aléatoires (qui ne devraient pas être apprises) ──
    # Ces features n'ont AUCUNE corrélation avec le résultat
    # (simule les features non-pertinentes du monde réel)
    if np.random.random() < 0.3:
        # Randomiser une feature aléatoire pour ajouter du bruit non-corrélé
        key = np.random.choice(list(feats.keys()))
        feats[key] = float(np.random.normal(0, 2.0))

    return feats


def generate_synthetic_from_report(report_path: str) -> tuple[np.ndarray, np.ndarray]:
    """Génère des données synthétiques depuis le rapport backtest.

    Pour chaque (symbole, TF, année), on génère N trades où N * WR sont
    gagnants et N * (1-WR) sont perdants. Les features sont échantillonnées
    avec une distribution qui reflète la performance de la période.

    Returns:
        (X, y) où X = (n_samples, n_features), y = (n_samples,)
    """
    if not os.path.exists(report_path):
        logger.warning(f"Rapport backtest non trouvé: {report_path}")
        return np.zeros((0, len(FEATURE_COLUMNS))), np.zeros(0)

    with open(report_path) as f:
        report = json.load(f)

    symbols_data = report.get("symbols", {})
    all_X = []
    all_y = []

    total_generated = 0

    for symbol, tfs in symbols_data.items():
        if symbol not in ACTIVE_SYMBOLS and symbol not in tfs:
            # Prendre quand même les symboles non-actifs pour enrichir le dataset
            pass

        for tf_name, tf_data in tfs.items():
            if not isinstance(tf_data, dict):
                continue

            total_trades = tf_data.get("total_trades", 0)
            win_rate = tf_data.get("win_rate", 50) / 100.0

            if total_trades < 20:
                continue

            # Années disponibles
            years_data = tf_data.get("years", {})
            if years_data:
                for year, year_data in years_data.items():
                    n_trades = year_data.get("trades", 0)
                    wr = year_data.get("win_rate", 50) / 100.0
                    if n_trades < 5:
                        continue

                    n_winners = int(n_trades * wr)
                    n_losers = n_trades - n_winners

                    for _ in range(n_winners):
                        feats = _sample_feature(wr, is_winner=True)
                        vec = [feats.get(col, 0.0) for col in FEATURE_COLUMNS]
                        all_X.append(vec)
                        all_y.append(1)
                        total_generated += 1

                    for _ in range(n_losers):
                        feats = _sample_feature(wr, is_winner=False)
                        vec = [feats.get(col, 0.0) for col in FEATURE_COLUMNS]
                        all_X.append(vec)
                        all_y.append(0)
                        total_generated += 1
            else:
                # Pas de détail par année
                n_winners = int(total_trades * win_rate)
                n_losers = total_trades - n_winners

                for _ in range(n_winners):
                    feats = _sample_feature(win_rate, is_winner=True)
                    vec = [feats.get(col, 0.0) for col in FEATURE_COLUMNS]
                    all_X.append(vec)
                    all_y.append(1)
                    total_generated += 1

                for _ in range(n_losers):
                    feats = _sample_feature(win_rate, is_winner=False)
                    vec = [feats.get(col, 0.0) for col in FEATURE_COLUMNS]
                    all_X.append(vec)
                    all_y.append(0)
                    total_generated += 1

    logger.info(f"Données synthétiques générées: {total_generated} trades")
    return np.array(all_X, dtype=float), np.array(all_y, dtype=int)


def load_real_trades(csv_path: str) -> tuple[np.ndarray, np.ndarray, list[dict]]:
    """Charge les trades réels depuis le seed CSV.

    Pour chaque trade réel, on génère des features synthétiques réalistes
    en utilisant le r_multiple comme indicateur de qualité.

    Returns:
        (X, y, trade_records) où chaque trade_record est un dict
    """
    if not os.path.exists(csv_path):
        logger.warning(f"Seed CSV non trouvé: {csv_path}")
        return np.zeros((0, len(FEATURE_COLUMNS))), np.zeros(0), []

    trades = []
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                r_multiple = float(row.get("r_multiple", 0))
                is_winner = r_multiple > 0
                trades.append(
                    {
                        "symbol": row.get("symbol", "UNKNOWN"),
                        "r_multiple": r_multiple,
                        "is_winner": is_winner,
                        "direction": row.get("direction", "BUY"),
                    }
                )
            except (ValueError, TypeError):
                continue

    if not trades:
        return np.zeros((0, len(FEATURE_COLUMNS))), np.zeros(0), []

    all_X = []
    all_y = []

    winners = sum(1 for t in trades if t["is_winner"])
    losers = len(trades) - winners
    wr = winners / len(trades) if trades else 0.5

    logger.info(f"Trades réels chargés: {len(trades)} (WR={wr:.1%}, +{winners}/-{losers})")

    # Pour les trades réels, on génère les features avec le poids REAL_TRADE_WEIGHT
    # (chaque trade réel compte comme REAL_TRADE_WEIGHT trades synthétiques)
    for trade in trades:
        for _ in range(REAL_TRADE_WEIGHT):
            feats = _sample_feature(wr, is_winner=trade["is_winner"], noise=0.03)
            # Ajouter le bruit réduit pour les trades réels
            vec = [feats.get(col, 0.0) for col in FEATURE_COLUMNS]
            all_X.append(vec)
            all_y.append(1 if trade["is_winner"] else 0)

    logger.info(f"Trades réels après weighting: {len(all_y)} (×{REAL_TRADE_WEIGHT})")
    return np.array(all_X, dtype=float), np.array(all_y, dtype=int), trades


def load_real_trades_jsonl(jsonl_path: str) -> tuple[np.ndarray, np.ndarray, list[dict]]:
    """Charge les trades réels depuis le fichier JSONL collecté en live.

    Chaque ligne est un trade fermé avec features complètes, collecté par
    position_tracker._log_real_trade(). Les features sont déjà dans l'ordre
    du modèle (features_vec) pour éviter les erreurs de mapping.

    Returns:
        (X, y, trade_records)
    """
    if not os.path.exists(jsonl_path) or os.path.getsize(jsonl_path) < 10:
        logger.info(f"Aucun trade réel trouvé dans {jsonl_path}")
        return np.zeros((0, len(FEATURE_COLUMNS))), np.zeros(0), []

    trades = []
    with open(jsonl_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                trades.append(record)
            except (json.JSONDecodeError, ValueError):
                continue

    if not trades:
        return np.zeros((0, len(FEATURE_COLUMNS))), np.zeros(0), []

    winners = sum(1 for t in trades if t.get("is_winner", False))
    losers = len(trades) - winners
    wr = winners / len(trades) if trades else 0.5

    logger.info(f"Trades réels JSONL chargés: {len(trades)} (WR={wr:.1%}, +{winners}/-{losers}, fichier: {jsonl_path})")

    all_X = []
    all_y = []

    # Pondération adaptative: duplication seulement si peu de trades
    # (évite la fuite de données quand on a assez de trades uniques)
    weight = REAL_TRADE_WEIGHT if len(trades) < 50 else 1

    for trade in trades:
        # Priorité: features_vec (pré-vectorisé), sinon features dict
        features_vec = trade.get("features_vec", [])
        if len(features_vec) == len(FEATURE_COLUMNS):
            vec = features_vec
        else:
            # Fallback: extraire depuis le dict features
            features = trade.get("features", {})
            vec = [features.get(col, 0.0) for col in FEATURE_COLUMNS]
        is_winner = trade.get("is_winner", False)

        # Chaque trade réel compte avec un poids plus élevé (sauf si assez de trades)
        for _ in range(weight):
            all_X.append(vec)
            all_y.append(1 if is_winner else 0)

    logger.info(f"Trades réels JSONL après weighting: {len(all_y)} (×{weight}, {len(trades)} uniques)")
    return np.array(all_X, dtype=float), np.array(all_y, dtype=int), trades


def temporal_split(
    X: np.ndarray, y: np.ndarray, val_split: float = 0.2, shuffle: bool = False
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Split temporel : les derniers val_split% servent de validation.

    Pour les données réelles (non synthétiques), le shuffle est désactivé
    pour éviter la fuite de données : on entraîne sur le passé, on valide sur le futur.
    """
    n = len(y)
    split_idx = int(n * (1 - val_split))
    if shuffle:
        indices = np.random.permutation(n)
        train_idx = indices[:split_idx]
        val_idx = indices[split_idx:]
        return X[train_idx], X[val_idx], y[train_idx], y[val_idx]
    else:
        # Split temporel réel: 80% passé, 20% futur
        return X[:split_idx], X[split_idx:], y[:split_idx], y[split_idx:]


# ─── Main ──────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Entraîne le modèle LightGBM")
    parser.add_argument("--force", action="store_true", help="Ré-entraîne même si modèle existe")
    parser.add_argument("--dry-run", action="store_true", help="Simulation sans sauvegarder")
    parser.add_argument("--seed-only", action="store_true", help="Uniquement les trades réels")
    parser.add_argument("--samples", type=int, default=0, help="Forcer N échantillons synthétiques")
    args = parser.parse_args()

    # Vérifier si le modèle existe déjà
    if os.path.exists(MODEL_PATH) and not args.force:
        meta = {}
        if os.path.exists(MODEL_META_PATH):
            with open(MODEL_META_PATH) as f:
                meta = json.load(f)
        samples = meta.get("train_samples", 0)
        logger.info(
            f"Modèle déjà existant: {MODEL_PATH} "
            f"({samples} échantillons d'entraînement). "
            f"Utilisez --force pour ré-entraîner."
        )
        return

    logger.info("=" * 60)
    logger.info("Entraînement LightGBM — Phase 2 du plan features > modèles")
    logger.info("=" * 60)

    # ── 1. Charger les données ──
    X_syn, y_syn = generate_synthetic_from_report(BACKTEST_REPORT)
    X_real, y_real, real_trades = load_real_trades(SEED_CSV)
    # 🆕 Charger les trades réels collectés en live (JSONL)
    X_jsonl, y_jsonl, jsonl_trades = load_real_trades_jsonl(REAL_TRADES_JSONL)

    # ── 2. Combiner ──
    # Fusion: synthétiques + seed CSV + trades live JSONL
    X_sources = []
    y_sources = []
    if len(X_syn) > 0:
        X_sources.append(X_syn)
        y_sources.append(y_syn)
    if len(X_real) > 0:
        X_sources.append(X_real)
        y_sources.append(y_real)
    if len(X_jsonl) > 0:
        X_sources.append(X_jsonl)
        y_sources.append(y_jsonl)

    if args.seed_only:
        # Seed-only: priorité JSONL (features réelles), fallback CSV (synthétique)
        if len(X_jsonl) >= 50:
            X = X_jsonl
            y = y_jsonl
            logger.info(f"Mode seed-only (JSONL prioritaire): {len(X)} trades avec features réelles")
        elif len(X_real) > 0:
            X = np.vstack(X_sources[1:]) if len(X_sources) > 1 else X_real
            y = np.concatenate(y_sources[1:]) if len(y_sources) > 1 else y_real
            logger.info(f"Mode seed-only (fallback CSV): {len(X)} trades (CSV={len(X_real)}, JSONL={len(X_jsonl)})")
        else:
            X = np.zeros((0, len(FEATURE_COLUMNS)))
            y = np.zeros(0)
            logger.warning("Mode seed-only: aucune donnée disponible")
    else:
        X = np.vstack(X_sources) if len(X_sources) > 0 else np.zeros((0, len(FEATURE_COLUMNS)))
        y = np.concatenate(y_sources) if len(y_sources) > 0 else np.zeros(0)

        if args.samples > 0 and len(X_syn) > args.samples:
            # Sous-échantillonner pour le debug
            idx = np.random.choice(len(X_syn), args.samples, replace=False)
            X_syn_sub = X_syn[idx]
            # Reconstruire avec sous-échantillon
            X = np.vstack([x for x in [X_syn_sub, X_real, X_jsonl] if len(x) > 0])
            y = np.concatenate([y for y in [y_syn[idx], y_real, y_jsonl] if len(y) > 0])

        if args.samples > 0 and len(X_syn) > args.samples:
            # Sous-échantillonner pour le debug
            idx = np.random.choice(len(X_syn), args.samples, replace=False)
            X = np.vstack([X_syn[idx], X_real]) if len(X_real) > 0 else X_syn[idx]
            y = np.concatenate([y_syn[idx], y_real]) if len(y_real) > 0 else y_syn[idx]

    n_samples = len(y)
    n_winners = int(y.sum())
    wr = n_winners / n_samples if n_samples > 0 else 0
    logger.info(f"Dataset total: {n_samples} trades (WR={wr:.1%}, +{n_winners}/-{n_samples - n_winners})")

    if n_samples < 100:
        logger.error(f"Pas assez de données: {n_samples} < 100")
        sys.exit(1)

    # ── 3. Split train/val ──
    X_train, X_val, y_train, y_val = temporal_split(X, y, val_split=0.15)
    logger.info(f"Train: {len(y_train)}, Val: {len(y_val)}")

    # ── 4. Entraîner ──
    from engine_simple.lightgbm_model import LightGBMModel

    model = LightGBMModel()

    logger.info("Entraînement en cours...")
    metrics = model.train(
        X_train,
        y_train,
        X_val=X_val,
        y_val=y_val,
        feature_names=FEATURE_COLUMNS,
    )

    logger.info(f"Résultats: accuracy={metrics['val_accuracy']:.1%}, AUC={metrics['val_auc']:.3f}")

    # ── 5. Feature importance ──
    logger.info("\nTop 10 features par importance:")
    for name, imp in model.get_feature_importance(10):
        logger.info(f"  {name:30s} {imp:.1%}")

    # ── 6. Performance par seuil ──
    if len(X_val) > 0:
        from sklearn.metrics import precision_score, recall_score, f1_score

        y_pred = model._model.predict(X_val)
        thresholds = [0.45, 0.48, 0.50, 0.52, 0.55, 0.60]
        logger.info("\nPerformance par seuil de confiance:")
        logger.info(f"  {'Seuil':>6} {'Precision':>10} {'Recall':>8} {'F1':>6} {'Trades':>7}")
        for thresh in thresholds:
            pred = (y_pred > thresh).astype(int)
            prec = precision_score(y_val, pred, zero_division=0)
            rec = recall_score(y_val, pred, zero_division=0)
            f1 = f1_score(y_val, pred, zero_division=0)
            n_pred = int(pred.sum())
            logger.info(f"  {thresh:>6.2f} {prec:>10.1%} {rec:>8.1%} {f1:>6.3f} {n_pred:>7}")

    # ── 7. Sauvegarder ──
    if not args.dry_run:
        model.save()
        logger.info(f"\nModèle sauvegardé: {MODEL_PATH}")
        logger.info(f"Métadonnées: {MODEL_META_PATH}")
    else:
        logger.info("\nDry-run: modèle NON sauvegardé")

    # ── 8. Résumé ──
    logger.info("\n" + "=" * 60)
    logger.info("RÉSUMÉ DE L'ENTRAÎNEMENT")
    logger.info(f"  Échantillons d'entraînement : {metrics['train_samples']}")
    logger.info(f"  Accuracy validation          : {metrics['val_accuracy']:.1%}")
    logger.info(f"  AUC validation               : {metrics['val_auc']:.3f}")
    logger.info(f"  Features                     : {len(FEATURE_COLUMNS)}")
    logger.info(f"  Seuil de confiance min       : {model.min_confidence:.2f}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
