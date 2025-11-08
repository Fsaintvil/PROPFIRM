Title: [PATCH] Regime detection — opt-in input validation, safe-clean and diagnostics

Description
-----------
This PR adds conservative, opt-in protections and operational diagnostics to the market regime detection pipeline. It does NOT change default behaviour when the new environment flags are not set.

What it contains
----------------
- Opt-in pre-clean validation: `REGIME_VALIDATE_RAW=1` — writes a diagnostic dump if validation fails (non-blocking).
- Opt-in pre-training validation: `REGIME_VALIDATE_INPUT=1` — if validation fails, pipeline falls back to KMeans (HMM bypass) and optionally writes a validation dump when `REGIME_VALIDATE_DUMP=1`.
- Safe-clean option: `REGIME_SAFE_CLEAN=1` — percentile clipping + `REGIME_MAX_RETURN` capping + use of RobustScaler for training.
- Diagnostic dumps written under `artifacts/diagnostics/` (e.g. `last_validation_raw_<ts>.json`, `regime_validation_<ts>.json`).
- Minimal pytest `tests/test_regime_validation.py` to assert validation rejects extreme returns.

Why
---
We observed gross upstream ingestion errors (massive/implausible returns). Training HMMs on such data creates unstable models and wrong signals. These changes provide evidence and a safe opt-in way to protect production while the ingestion team fixes sources.

How to test locally / in staging
--------------------------------
1) Run staging with opt-in flags (PowerShell example):

```powershell
$env:REGIME_VALIDATE_RAW='1'
$env:REGIME_VALIDATE_DUMP='1'
$env:REGIME_VALIDATE_INPUT='1'
$env:REGIME_SAFE_CLEAN='1'
$env:REGIME_MAX_RETURN='0.5'
python -m scripts.market_regime_detection
```

2) Confirm diagnostic files are created under `artifacts/diagnostics/`.
3) Run pytest:

```powershell
python -m pytest -q tests/test_regime_validation.py
```

Checklist before merge
----------------------
- [ ] CI: pytest passes (including `tests/test_regime_validation.py`).
- [ ] Run staging with flags above and inspect `artifacts/diagnostics/` for expected dumps.
- [ ] Ops & ingestion review: agree on thresholds (`REGIME_MAX_RETURN`, missing % limit) and remediation plan.

Rollback
--------
- All changes are opt-in; to rollback revert the PR or simply not set the environment variables in production. To revert the commit use `git revert <commit>` if necessary.

Notes for reviewers
-------------------
- No behavioural change when no env flags are set.
- Diagnostics are intentionally conservative and small (head/tail samples) to avoid large files.
- Suggest Ops to activate flags in canary, follow `docs/REGIME_DEPLOY_PLAYBOOK.md`.

Reviewers
---------
- Platform / ingestion
- Quant / Strategies
- Ops