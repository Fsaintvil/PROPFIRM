Title: Urgent: Upstream ingestion anomalies — implausible price/return values observed

Summary
-------
During staging runs of the market regime detector we observed many implausible returns and extreme price values. These corruptions break downstream models and must be handled at ingestion.

Diagnostics available (examples):
- `artifacts/diagnostics/last_validation_raw_<ts>.json` (contains raw_validation_report, sample head/tail of returns)
- `artifacts/diagnostics/regime_input_validation.json`

Example findings (from staging):
- n: 6600
- abs_gt_max: 4420
- max_abs: 20454.749098767836
- reason: many_extreme_returns

Recommended immediate rules to add at ingestion
-----------------------------------------------
1. Reject prices <= 0 or NaN.
2. If any pct_change (abs) > MAX_RETURN_THRESHOLD (suggested default: 50 i.e. 5000%), quarantine the symbol and create a ticket for manual review.
3. If > 20% NA in the recent window, quarantine and alert.
4. Reject duplicate timestamps and non-monotonic timestamps.
5. Log a structured JSON for every rejection with: symbol, timeframe, timestamps range, reason, sample head/tail (max 10 rows).

Suggested minimal snippet to implement
-------------------------------------
```python
def validate_price_series(prices: pd.Series, max_return=50.0, max_na_pct=0.2):
    if prices.isna().all():
        return False, "all_missing"
    if (prices <= 0).any():
        return False, "non_positive_price"
    rets = prices.pct_change().abs()
    if (rets > max_return).any():
        return False, "extreme_return"
    if prices.isna().mean() > max_na_pct:
        return False, "too_many_missing"
    # Additional checks: duplicates, monotonic timestamps
    return True, "ok"
```

Action items for ingestion team
-------------------------------
- Implement above checks in ingestion pipeline before writing to feature store.
- For quarantined series, write the structured dump in a `quarantine/` bucket/folder and open an automated ticket.
- Add unit tests simulating extreme and edge cases.
- Share a short runbook explaining remediation steps for operations.

If you want, I can prepare a small PR on the ingestion repo implementing these checks and adding tests. Please confirm which repo/branch to target.