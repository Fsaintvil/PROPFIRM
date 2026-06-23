#!/usr/bin/env python
"""Extraction des features pour les trades réels depuis MT5.

Lecture:  runtime/robot_state.json (trade_history → 151 trades)
Connexion MT5 → télécharge OHLCV → calcule features → sauvegarde
Sortie:   runtime/lgb_real_trades_with_features.jsonl

Usage:
    python scripts/extract_real_features.py                    # extraction seule
    python scripts/extract_real_features.py --train            # extraction + retraining LGB
    python scripts/extract_real_features.py --dry-run          # simulation sans sauvegarder
    python scripts/extract_real_features.py --force            # force re-extract même si fichier existe
"""

import argparse
import json
import logging
import os
import sys
import time as _time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s %(message)s",
)
logger = logging.getLogger("extract_features")

sys.path.insert(0, str(Path(__file__).parent.parent))

# ─── Imports du projet ──────────────────────────────────────────────────────

ROBOT_STATE = "runtime/robot_state.json"
OUTPUT_JSONL = "runtime/lgb_real_trades_with_features.jsonl"
REAL_TRADES_JSONL = "runtime/lgb_real_trades.jsonl"  # trades live existants

# Les 20 features utilisées par le modèle LGB (ordre exact)
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

# Timeframe par symbole (doit correspondre à config_simple.SYMBOL_LIMITS)
SYMBOL_TF = {
    "XAUUSD": "H4",
    "BTCUSD": "H1",
    "EURUSD": "H1",
}

# Mapping des timeframes MT5
TF_MAP = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "H1": 60,
    "H4": 240,
    "D1": 1440,
}


def parse_trade_time(trade: dict) -> datetime | None:
    """Convertit le timestamp d'un trade en datetime UTC.

    Supporte: string ISO, int unix, float unix.
    """
    t = trade.get("time")
    if t is None:
        return None
    if isinstance(t, (int, float)):
        return datetime.fromtimestamp(t, tz=timezone.utc)
    if isinstance(t, str):
        # Essayer format ISO
        for fmt in [
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
        ]:
            try:
                return datetime.strptime(t, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        # Dernier recours: timestamp numérique dans une string
        try:
            return datetime.fromtimestamp(float(t), tz=timezone.utc)
        except (ValueError, TypeError):
            pass
    logger.warning(f"Impossible de parser le time: {t!r} (type={type(t).__name__})")
    return None


def get_mt5_data(symbol: str, tf_name: str, from_date: datetime) -> np.ndarray | None:
    """Télécharge les données OHLCV depuis MT5 jusqu'à la date donnée.

    Prend AU MOINS 300 bougies avant from_date pour garantir
    suffisamment de data pour EMA200 et autres features long-terme.
    """
    import MetaTrader5 as mt5

    tf_map_mt5 = {
        "M1": mt5.TIMEFRAME_M1,
        "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "H1": mt5.TIMEFRAME_H1,
        "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1,
    }
    tf = tf_map_mt5.get(tf_name, mt5.TIMEFRAME_H1)

    # On veut des données AVANT from_date
    # copy_rates_from_range donne [from_date, to_date]
    # On utilise copy_rates_from pour avoir N bougies avant une date

    # Demander 500 bougies avant la date pour être sûr d'avoir assez de données
    # pour les features long-terme (EMA200 = 200 bougies mini)
    rates = mt5.copy_rates_from(symbol, tf, from_date, 500)
    if rates is None or len(rates) < 50:
        logger.warning(
            f"Pas assez de données MT5 pour {symbol} {tf_name} "
            f"avant {from_date}: {len(rates) if rates is not None else 0} bars"
        )
        return None

    return rates


def compute_outcome(profit: float, direction: str | None = None) -> bool:
    """Détermine si un trade est gagnant.

    Un trade est gagnant si profit > 0 (après spread/commission).
    """
    return profit > 0


def extract_features_for_trades(
    trades: list[dict],
    output_path: str,
    dry_run: bool = False,
    force: bool = False,
) -> int:
    """Extrait les features pour tous les trades via MT5.

    Args:
        trades: Liste des trades depuis robot_state.json
        output_path: Chemin de sortie JSONL
        dry_run: Si True, ne sauvegarde pas
        force: Si True, extrait même si le fichier existe déjà

    Returns:
        Nombre de trades avec features extraites avec succès
    """
    import MetaTrader5 as mt5

    # Vérifier si le fichier de sortie existe déjà
    if os.path.exists(output_path) and not force:
        logger.info(f"Fichier de sortie existe déjà: {output_path} ({os.path.getsize(output_path)} bytes)")
        logger.info("Utilisez --force pour ré-extraire")
        count = sum(1 for _ in open(output_path) if _.strip())
        logger.info(f"Contient déjà {count} trades avec features")
        return count

    # Initialiser MT5
    logger.info("Connexion à MT5...")
    if not mt5.initialize(timeout=15000, portable=True):
        logger.error(f"Impossible d'initialiser MT5: {mt5.last_error()}")
        return 0

    # Activer les symboles
    for sym in SYMBOL_TF:
        mt5.symbol_select(sym, True)

    try:
        # Grouper les trades par symbole pour optimiser les appels MT5
        trades_by_symbol: dict[str, list[dict]] = defaultdict(list)
        for t in trades:
            sym = t.get("symbol", "?")
            trades_by_symbol[sym].append(t)

        logger.info(f"Trades à traiter: {len(trades)} répartis sur {len(trades_by_symbol)} symboles")
        for sym, sym_trades in trades_by_symbol.items():
            logger.info(f"  {sym}: {len(sym_trades)} trades")

        # Statistiques
        total_extracted = 0
        total_errors = 0
        total_skipped_no_data = 0
        total_skipped_bad_features = 0

        # Stocker les métadonnées sur les trades exclus/échoués
        error_details: list[dict] = []

        # Traiter chaque symbole
        for symbol, symbol_trades in trades_by_symbol.items():
            tf_name = SYMBOL_TF.get(symbol, "H1")
            logger.info(f"\n{'=' * 60}")
            logger.info(f"Traitement: {symbol} ({tf_name}) — {len(symbol_trades)} trades")

            # Trier les trades par temps croissant
            symbol_trades_with_time = []
            for t in symbol_trades:
                dt = parse_trade_time(t)
                if dt is None:
                    error_details.append({"ticket": t.get("ticket"), "symbol": symbol, "reason": "parse_time"})
                    total_errors += 1
                    continue
                symbol_trades_with_time.append((dt, t))

            symbol_trades_with_time.sort(key=lambda x: x[0])

            if not symbol_trades_with_time:
                logger.warning(f"Aucun trade avec timestamp valide pour {symbol}")
                continue

            # Pour optimiser les appels MT5, on télécharge toutes les données
            # du plus récent trade jusqu'à 500 bougies avant (pour avoir assez d'historique)
            earliest_time = symbol_trades_with_time[0][0]
            latest_time = symbol_trades_with_time[-1][0]

            logger.info(f"Plage temporelle: {earliest_time} → {latest_time}")

            # Télécharger 500 bougies terminant AU PLUS RÉCENT trade
            # (les données sont ordonnées ancien → récent, donc on aura toute la plage)
            rates = get_mt5_data(symbol, tf_name, latest_time)
            if rates is None:
                logger.error(f"Pas de données MT5 pour {symbol}, tous les trades seront ignorés")
                for dt, t in symbol_trades_with_time:
                    error_details.append({"ticket": t.get("ticket", "?"), "symbol": symbol, "reason": "no_mt5_data"})
                    total_skipped_no_data += 1
                continue

            # Convertir en arrays numpy pour compute_all_features
            # Les données MT5 sont ordonnées chronologiquement (ancien → récent)
            mt5_time = np.array([r[0] for r in rates], dtype=np.int64)
            mt5_close = np.array([r[4] for r in rates], dtype=float)
            mt5_high = np.array([r[2] for r in rates], dtype=float)
            mt5_low = np.array([r[3] for r in rates], dtype=float)
            mt5_volume = np.array([r[5] for r in rates], dtype=float)  # tick_volume
            mt5_spread = np.array([r[6] for r in rates], dtype=float)

            logger.info(f"Données MT5 chargées: {len(rates)} bougies {tf_name}")

            # Traiter chaque trade
            for idx, (trade_dt, trade) in enumerate(symbol_trades_with_time):
                ticket = trade.get("ticket", idx)
                profit = trade.get("profit", 0)
                direction = trade.get("direction", trade.get("type", "BUY"))

                # Convertir le timestamp MT5 (secondes) en datetime pour comparaison
                # Trouver l'index de la bougie juste avant ou égale au trade
                trade_ts = int(trade_dt.timestamp())

                # On cherche la barre qui était la plus récente au moment du trade
                # c'est-à-dire la barre avec time <= trade_ts
                mask = mt5_time <= trade_ts
                if not mask.any():
                    error_details.append({"ticket": ticket, "symbol": symbol, "reason": "no_bar_before_trade"})
                    total_skipped_no_data += 1
                    continue

                last_idx = np.where(mask)[0][-1]

                # Vérifier qu'on a assez de données avant ce trade
                if last_idx < 200:
                    error_details.append({"ticket": ticket, "symbol": symbol, "reason": "not_enough_bars"})
                    total_skipped_no_data += 1
                    continue

                # Trancher les données: de 0 à last_idx (inclus)
                close_slice = mt5_close[: last_idx + 1]
                high_slice = mt5_high[: last_idx + 1]
                low_slice = mt5_low[: last_idx + 1]
                volume_slice = mt5_volume[: last_idx + 1]
                spread_slice = mt5_spread[: last_idx + 1]

                # Calculer les features
                try:
                    from engine_simple.feature_pipeline import compute_all_features

                    features = compute_all_features(
                        close=close_slice,
                        high=high_slice,
                        low=low_slice,
                        volume=volume_slice,
                        spread=float(spread_slice[-1]),
                        spread_history=spread_slice.tolist() if len(spread_slice) > 20 else None,
                        symbol=symbol,
                    )
                except Exception as e:
                    logger.warning(f"Erreur compute_all_features pour trade {ticket} ({symbol}): {e}")
                    error_details.append({"ticket": ticket, "symbol": symbol, "reason": f"compute_error: {e}"})
                    total_errors += 1
                    continue

                # ⚠️ CORRECTION: time_features() utilise datetime.now() → override avec l'heure du trade
                hour = trade_dt.hour
                weekday = trade_dt.weekday()
                features["hour_utc"] = float(hour)
                features["weekday"] = float(weekday)
                features["is_weekend"] = 1.0 if weekday >= 5 else 0.0
                features["is_monday"] = 1.0 if weekday == 0 else 0.0
                features["is_friday"] = 1.0 if weekday == 4 else 0.0
                # Sessions
                features["session_asia"] = 1.0 if hour in list(range(0, 9)) else 0.0
                features["session_london"] = 1.0 if hour in list(range(9, 17)) else 0.0
                features["session_ny"] = 1.0 if hour in list(range(13, 22)) else 0.0
                features["session_london_ny_overlap"] = 1.0 if hour in list(range(13, 17)) else 0.0
                features["session_asia_london_overlap"] = 1.0 if hour == 9 else 0.0

                # Vérifier qu'on a au moins 10 features
                if len(features) < 10:
                    total_skipped_bad_features += 1
                    error_details.append({"ticket": ticket, "symbol": symbol, "reason": "too_few_features"})
                    continue

                # Construire le vecteur pour LGB (20 features dans l'ordre)
                features_vec = [features.get(col, 0.0) for col in FEATURE_COLUMNS]

                # Déterminer si gagnant
                is_winner = compute_outcome(profit, direction)

                # Score de confiance du signal (on n'a pas le score réel dans l'historique)
                # On estime la confiance à partir de r_multiple ou profit/ATR
                r_multiple = trade.get("r_multiple", 0) or (profit / max(abs(profit), 1))

                # Construire l'enregistrement
                record = {
                    "ticket": ticket,
                    "symbol": symbol,
                    "direction": direction,
                    "profit": profit,
                    "is_winner": is_winner,
                    "r_multiple": r_multiple,
                    "time": trade_dt.isoformat(),
                    "timeframe": tf_name,
                    "features": features,
                    "features_vec": features_vec,
                    "n_features": len(features),
                    "source": "historical_extraction",
                }

                # Sauvegarder dans le JSONL
                if not dry_run:
                    with open(output_path, "a") as f:
                        f.write(json.dumps(record, default=str) + "\n")

                total_extracted += 1

                if (idx + 1) % 20 == 0:
                    logger.info(
                        f"  {symbol}: {idx + 1}/{len(symbol_trades_with_time)} trades traités ({total_extracted} extraits)"
                    )

        # Résumé final
        logger.info("\n" + "=" * 60)
        logger.info("RÉSUMÉ DE L'EXTRACTION")
        logger.info(f"  Trades traités          : {len(trades)}")
        logger.info(f"  Features extraites      : {total_extracted} ✅")
        logger.info(f"  Erreurs MT5/features    : {total_errors}")
        logger.info(f"  Pas de données MT5      : {total_skipped_no_data}")
        logger.info(f"  Features insuffisantes  : {total_skipped_bad_features}")
        logger.info(f"  Total exclus            : {total_errors + total_skipped_no_data + total_skipped_bad_features}")

        # Vérifier le fichier de sortie
        if not dry_run and total_extracted > 0:
            final_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
            logger.info(f"  Fichier de sortie       : {output_path} ({final_size} bytes)")

            # Compter les lignes
            with open(output_path) as f:
                lines = [l for l in f if l.strip()]
            logger.info(f"  Lignes dans le fichier  : {len(lines)}")

            # Statistiques
            winners = sum(1 for l in lines if json.loads(l).get("is_winner", False))
            losers = len(lines) - winners
            wr = winners / len(lines) * 100 if lines else 0
            logger.info(f"  Winners/Losers          : {winners}/{losers} (WR={wr:.1f}%)")

            # Par symbole
            by_symbol: dict[str, list[bool]] = defaultdict(list)
            for line in lines:
                rec = json.loads(line)
                by_symbol[rec["symbol"]].append(rec["is_winner"])
            for sym, outcomes in sorted(by_symbol.items()):
                w = sum(outcomes)
                l = len(outcomes) - w
                logger.info(f"    {sym}: {len(outcomes)} trades (WR={w / len(outcomes) * 100:.1f}%, +{w}/-{l})")

        if total_errors > 0:
            logger.warning(f"  Erreurs rencontrées: {total_errors}")
            for err in error_details[:5]:
                logger.warning(f"    {err}")

        return total_extracted

    finally:
        mt5.shutdown()
        logger.info("Déconnexion MT5")


def main():
    parser = argparse.ArgumentParser(description="Extraction des features pour trades réels")
    parser.add_argument("--train", action="store_true", help="Extraction + retraining LGB")
    parser.add_argument("--dry-run", action="store_true", help="Simulation sans sauvegarder")
    parser.add_argument("--force", action="store_true", help="Force re-extract même si fichier existe")
    args = parser.parse_args()

    # 1. Charger les trades depuis robot_state.json
    if not os.path.exists(ROBOT_STATE):
        logger.error(f"Fichier non trouvé: {ROBOT_STATE}")
        sys.exit(1)

    with open(ROBOT_STATE) as f:
        state = json.load(f)

    trades = state.get("trade_history", [])
    if not trades:
        logger.error("Aucun trade dans trade_history")
        sys.exit(1)

    logger.info(f"Chargé {len(trades)} trades depuis {ROBOT_STATE}")
    logger.info(
        f"Symboles: {dict(sorted(__import__('collections').Counter(t.get('symbol', '?') for t in trades).items()))}"
    )

    # Afficher la plage temporelle
    times = [parse_trade_time(t) for t in trades if parse_trade_time(t)]
    if times:
        logger.info(f"Plage temporelle: {min(times)} → {max(times)}")

    # 2. Extraire les features
    logger.info("\n" + "=" * 60)
    logger.info("EXTRACTION DES FEATURES VIA MT5")
    logger.info("=" * 60)

    # Supprimer l'ancien fichier si force
    if args.force and os.path.exists(OUTPUT_JSONL):
        backup = OUTPUT_JSONL + ".bak"
        if os.path.exists(backup):
            os.remove(backup)
        os.rename(OUTPUT_JSONL, backup)
        logger.info(f"Ancien fichier sauvegardé: {backup}")

    extracted = extract_features_for_trades(
        trades=trades,
        output_path=OUTPUT_JSONL,
        dry_run=args.dry_run,
        force=args.force,
    )

    if extracted == 0:
        logger.error("Aucune feature extraite, arrêt.")
        sys.exit(1)

    # 3. Optionnel: Retraîner LGB
    if args.train:
        logger.info("\n" + "=" * 60)
        logger.info("RETRAINING LIGHTGBM AVEC DONNÉES RÉELLES")
        logger.info("=" * 60)

        import subprocess

        cmd = [
            sys.executable,
            "scripts/train_lightgbm.py",
            "--seed-only",
            "--force",
        ]

        logger.info(f"Lancement: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=False, timeout=300)

        if result.returncode == 0:
            logger.info("✅ Retraining LGB réussi !")
        else:
            logger.error(f"❌ Retraining échoué (code={result.returncode})")

    # 4. Résumé
    if os.path.exists(OUTPUT_JSONL):
        with open(OUTPUT_JSONL) as f:
            all_records = [json.loads(l) for l in f if l.strip()]

        # Compter les vrais trades (features réelles) vs synthétiques
        real_count = sum(1 for r in all_records if r.get("n_features", 0) >= 10)
        logger.info(f"\nFichier final: {OUTPUT_JSONL}")
        logger.info(f"  Total enregistrements: {len(all_records)}")
        logger.info(f"  Avec features réelles: {real_count}")
        logger.info(f"  Taille: {os.path.getsize(OUTPUT_JSONL)} bytes")

        # Vérifier la qualité des features
        if all_records:
            avg_features = sum(r.get("n_features", 0) for r in all_records) / len(all_records)
            logger.info(f"  Features moyennes par trade: {avg_features:.0f}")

    logger.info("\nTerminé.")


if __name__ == "__main__":
    main()
