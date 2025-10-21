# Pipeline MTF (15m) + 7 techniques + 7 fondamentaux + Backtest 7 ans

- Construction dataset: `scripts/build_mtf_dataset.py <SYMBOL>`
- Backtest 7y: `scripts/backtest_mtf_7y.py <DATASET>`
- Orchestration multi-symbols: `scripts/build_and_backtest_mtf.py --symbols EURUSD,XAUUSD,BTCUSD`

Données attendues:
- OHLCV 15m 7 ans: `data/ohlcv/<SYMBOL>_15m.csv` avec colonnes `time,open,high,low,close,volume`
- Fondamentaux (CSV): `data/fundamentals/*.csv` avec colonnes `date,value` (7 séries: inflation_yoy,gdp_growth_qoq,unemployment_rate,interest_rate,m2_growth_yoy,cpi_core_yoy,sentiment_index).

Sorties:
- Dataset enrichi: `artifacts/datasets/<SYMBOL>_mtf_15m.parquet|csv`
- Rapport backtest: impression console (et peut être enrichi vers JSON/PNG si besoin)
