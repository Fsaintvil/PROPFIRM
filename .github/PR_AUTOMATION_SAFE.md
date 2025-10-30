# PR: Automation-safe MT5 apply enhancements

Summary
-------
This patch adds non-invasive automation controls to the MT5 apply workflow. It does NOT remove existing safety checks (ALOW_MT5_SEND) — instead it layers on configurable automation modes and a required approval token for staged/full automation.

Files changed
-------------
- `tools/mt5_apply_sltp.py` — added:
  - AUTO_MODE environment variable: `manual` (default), `canary`, `staged`, `full`.
  - APPROVAL_TOKEN: required for `staged` and `full` modes.
  - CANARY_COUNT, STAGED_BATCH, VERIFY_AFTER_BATCH, MAX_TO_APPLY environment flags.
  - Canary/staged/full behavior: canary applies first N proposals; staged applies in batches and optionally runs verification between batches; full applies everything.
  - All behavior is opt-in via env vars; script still requires `ALLOW_MT5_SEND=1`.

- `tools/mt5_prioritize.py` — added:
  - Non-interactive apply support when `APPROVAL_TOKEN` is present in the environment. If absent the existing interactive confirmation phrase is still required.

Why
---
Operators requested a safer path to automated runs. The changes provide graduated automation (canary -> staged -> full) while keeping explicit opt-ins and an approval token. This reduces the risk compared to removing safeguards altogether.

How to use
----------
Examples (execute in PowerShell where you control the environment):

Manual (existing behavior):

```powershell
$env:ALLOW_MT5_SEND='1'
python tools/mt5_apply_sltp.py artifacts\mt5_backups\mt5_proposals_for_apply_YYYYMMDDT...json
```

Canary: apply first proposal only

```powershell
$env:ALLOW_MT5_SEND='1'
$env:AUTO_MODE='canary'
$env:CANARY_COUNT='1'
python tools/mt5_apply_sltp.py artifacts\mt5_backups\mt5_proposals_for_apply_YYYYMMDDT...json
```

Staged (requires APPROVAL_TOKEN):

```powershell
$env:ALLOW_MT5_SEND='1'
$env:AUTO_MODE='staged'
$env:APPROVAL_TOKEN='my-secret-token'
$env:STAGED_BATCH='5'
$env:VERIFY_AFTER_BATCH='1'  # optional
python tools/mt5_apply_sltp.py artifacts\mt5_backups\mt5_proposals_for_apply_YYYYMMDDT...json
```

Prioritize non-interactive apply (will skip the interactive phrase if APPROVAL_TOKEN present):

```powershell
$env:ALLOW_MT5_SEND='1'
$env:APPROVAL_TOKEN='my-secret-token'
python tools/mt5_prioritize.py --apply
```

Notes & safety
---------------
- APPROVAL_TOKEN is a convenience for non-interactive CI-style runs; keep it secret and rotate it regularly.
- These changes intentionally do not modify or disable the required `ALLOW_MT5_SEND` gate.
- `VERIFY_AFTER_BATCH` attempts to call `tools/mt5_verify_apply.py` after each batch — this is best-effort and will not abort if verification fails.

Testing & validation
--------------------
Recommended steps before merging:
1. Run flake8 and pytest in CI; address style warnings if desired.
2. Perform an end-to-end dry-run on a demo or demo-account with `AUTO_MODE=canary` and verify the intermediate artifacts.
3. Validate `APPROVAL_TOKEN` behavior in a staging environment.

Follow-ups
----------
- Optionally add an approval token file path and HMAC verification for stronger security.
- Add unit tests around batching behavior (mocking mt5.order_send) and verification calls.
- Consider an audit logger that writes approval events to an append-only store.

---

If you want, I can:
- create a dedicated branch and commit the changes there,
- run flake8/pytest and fix minor style issues,
- prepare a PR with this description and the changed files.

Tell me which of the above you'd like me to do next.