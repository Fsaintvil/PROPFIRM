# Changelog

## 2025-10-17 - Re-pickled models
- Re-pickled models in `MT5_FTMO_IA/models` to match the running environment and avoid scikit-learn InconsistentVersionWarning.
- Backups of original pickles were moved to `MT5_FTMO_IA/models/archives/<timestamp>/` and archived as `model_baks_<timestamp>.zip`.
- Files touched:
  - `model_example.pkl`
  - `model_realtime.pkl`
  - `model_realtime_intraday.pkl`
  - `model_realtime_intraday_20251016_110711.pkl`
