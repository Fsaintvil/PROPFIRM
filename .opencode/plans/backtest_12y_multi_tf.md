# Plan: Backtest MOM20x3 12 ans — H1/H4/D1, 15 symboles

## Objectif
Backtester la stratégie MOM20x3 sur 12+ années de données historiques,
sur 15 symboles × 3 timeframes (H1, H4, D1), avec rapports par année/mois.

## Scripts à créer

### 1. `scripts/download_historical_data.py`
- Télécharge H1/H4/D1 pour 15 symboles via MT5 avec pagination
- Recycle les parquets existants dans `data/raw/` (5 symboles)
- Stocke dans `data/historical/{SYM}_{TF}.parquet`
- 45 fichiers total

### 2. `scripts/backtest_multi_tf.py`
- Charge chaque parquet de `data/historical/`
- Exécute `mom20x3_signal()` barre par barre
- Simule SL/TP ATR + trailing 4 niveaux + partial TP
- Sauvegarde les trades dans `runtime/trades_backtest.pkl`

### 3. `scripts/report_backtest_multi.py`
- Charge les trades du pkl
- Agrège par (symbole, timeframe, année, mois)
- Calcule Trades, WR, PnL, PF, DD Max par groupe
- Sortie: console + `runtime/backtest_report.csv` + `runtime/backtest_report.json`

## Ordre d'exécution
1. `python scripts/download_historical_data.py`
2. `python scripts/backtest_multi_tf.py`
3. `python scripts/report_backtest_multi.py`

## Données disponibles
- 5 symboles déjà en parquet (AUDUSD, EURUSD, GBPUSD, USDCAD, USDCHF) — H1/H4/D1
- 10 symboles à télécharger via MT5
- H1: ~100K barres (2010→2026)
- H4: ~40K barres (2000→2026)
- D1: ~6.8K barres (2000→2026)

## Statut
- [x] Plan approuvé par l'utilisateur
- [ ] Script 1 créé
- [ ] Script 2 créé
- [ ] Script 3 créé
- [ ] Exécution terminée
