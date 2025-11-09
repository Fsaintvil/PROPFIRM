# Merged preview for prefix: check
# Generated from 9 files

################################################################################
# FROM: scripts\check_artifact_integrity.py
################################################################################
#!/usr/bin/env python3
"""
Check artifact integrity against a manifest CSV (path,size,sha256).

Usage:
    python scripts/check_artifact_integrity.py \
        [--manifest tmp/file_hashes.csv] \
        [--prefix artifacts\\auto_improve]

Exits with code 0 if all checked files match the manifest, 1 otherwise.
"""
import argparse
import csv
import hashlib
import sys
from pathlib import Path


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest().upper()


def load_manifest(manifest_path: Path, prefix: str):
    entries = {}
    with manifest_path.open('r', encoding='utf-8', errors='ignore') as f:
        reader = csv.reader(f)
        for row in reader:
            if not row:
                continue
            p = row[0].strip('"')
            # Normalize separators
            if prefix.replace('/', '\\') in p.replace('/', '\\'):
                expected_hash = row[2].strip() if len(row) > 2 else ''
                entries[Path(p)] = expected_hash.upper()
    return entries


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--manifest', default='tmp/file_hashes.csv')
    ap.add_argument('--prefix', default='artifacts/auto_improve')
    args = ap.parse_args()

    manifest = Path(args.manifest)
    if not manifest.exists():
        print(f"Manifest not found: {manifest}")
        sys.exit(2)

    entries = load_manifest(manifest, args.prefix)
    if not entries:
        print(f"No entries found in manifest for prefix '{args.prefix}'")
        sys.exit(0)

    failures = []
    for p, expected in entries.items():
        # Convert manifest absolute path into workspace-relative when possible
        target = Path(str(p))
        if not target.exists():
            print(f"MISSING: {target} (expected hash {expected})")
            failures.append((target, 'MISSING', expected, ''))
            continue
        actual = sha256_of(target)
        if actual != expected:
            print(f"MISMATCH: {target}\n  expected: {expected}\n  actual:   {actual}")
            failures.append((target, 'MISMATCH', expected, actual))
        else:
            print(f"OK: {target} {actual}")

    if failures:
        print(f"\nIntegrity check failed: {len(failures)} problems")
        sys.exit(1)
    print("\nAll checked artifacts match manifest.")
    sys.exit(0)


if __name__ == '__main__':
    main()


################################################################################
# FROM: scripts\check_tradable_remaining.py
################################################################################
"""
Check tradability of remaining positions listed in
artifacts/live_trading/close_all_positions_result.json
Writes artifacts/live_trading/remaining_tradability.json and prints a compact report.
"""
import os
import json
import time

from pathlib import Path

OUT_DIR = Path('artifacts') / 'live_trading'
OUT_DIR.mkdir(parents=True, exist_ok=True)

infile = Path('artifacts') / 'live_trading' / 'close_all_positions_result.json'
if not infile.exists():
    print('INPUT_NOT_FOUND:', infile)
    raise SystemExit(1)

j = json.loads(infile.read_text(encoding='utf-8'))
closed = j.get('closed', [])
not_closed = [r for r in closed if not (r.get('order_result', {}).get('deal'))]
symbols = sorted({r.get('symbol') for r in not_closed})

report = {
    'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    'symbols_checked': [],
}

# try to import mt5
try:
    import MetaTrader5 as mt5
except Exception as e:
    print('MT5_IMPORT_FAILED:', e)
    # still write a simple report
    for s in symbols:
        report['symbols_checked'].append({'symbol': s, 'trade_allowed': None, 'note': 'mt5_import_failed'})
    out = OUT_DIR / 'remaining_tradability.json'
    out.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
    raise SystemExit(1)

# load credentials from config if present
creds_path = Path(__file__).parent.parent / 'config' / 'mt5_credentials.env'
if creds_path.exists():
    for line in creds_path.read_text(encoding='utf-8').splitlines():
        line=line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k,v = line.split('=',1)
        os.environ.setdefault(k.strip(), v.strip())

init_kwargs = {}
login = os.getenv('MT5_LOGIN') or os.getenv('MT5_ACCOUNT')
password = os.getenv('MT5_PASSWORD') or os.getenv('MT5_PWD')
server = os.getenv('MT5_SERVER')
if login:
    try:
        init_kwargs['login'] = int(login)
    except Exception:
        init_kwargs['login'] = login
if password:
    init_kwargs['password'] = password
if server:
    init_kwargs['server'] = server

ok = mt5.initialize(**init_kwargs) if init_kwargs else mt5.initialize()
if not ok:
    print('MT5_INITIALIZE_FAILED')

for sym in symbols:
    info = mt5.symbol_info(sym)
    tick = mt5.symbol_info_tick(sym)
    entry = {'symbol': sym, 'trade_allowed': None, 'visible': None, 'volume_step': None, 'volume_min': None, 'digits': None, 'tick': None, 'note': ''}
    if info is None:
        entry['note'] = 'no_symbol_info'
    else:
        entry['trade_allowed'] = bool(info.trade_allowed) if hasattr(info, 'trade_allowed') else None
        entry['visible'] = bool(info.visible) if hasattr(info, 'visible') else None
        try:
            entry['volume_step'] = float(info.volume_step) if info.volume_step is not None else None
            entry['volume_min'] = float(info.volume_min) if info.volume_min is not None else None
            entry['digits'] = int(info.digits) if info.digits is not None else None
        except Exception:
            pass
    if tick is not None:
        try:
            entry['tick'] = {'bid': float(tick.bid), 'ask': float(tick.ask)}
        except Exception:
            entry['tick'] = None
    report['symbols_checked'].append(entry)

out = OUT_DIR / 'remaining_tradability.json'
out.write_text(json.dumps(report, indent=2), encoding='utf-8')

# print compact
print('checked', len(report['symbols_checked']), 'symbols')
for e in report['symbols_checked']:
    s=e['symbol']
    ta = e['trade_allowed']
    vis = e['visible']
    note = e.get('note','')
    vs = e.get('volume_step')
    vm = e.get('volume_min')
    digits = e.get('digits')
    tick = e.get('tick')
    print(f"{s}: trade_allowed={ta} visible={vis} step={vs} min={vm} digits={digits} tick={tick} note={note}")

mt5.shutdown()
print('\nWrote', out)


################################################################################
# FROM: scripts\ops\check_active_model_and_mt5.py
################################################################################
#!/usr/bin/env python3
"""Simple health check: read `control/active_model.txt` and verify recent MT5 connection in logs.

Usage:
  python scripts/ops/check_active_model_and_mt5.py

This script is read-only and non-intrusive.
"""
from pathlib import Path
from datetime import datetime
import re


def read_active_model(path: Path):
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return None


def find_recent_mt5_success(log_dir: Path, within_minutes: int = 60):
    pattern = re.compile(
        r"Connexion MT5 réussie|Connexion MT5 établie|MT5 connected", re.IGNORECASE
    )
    # We intentionally keep this simple: search logs for MT5-success lines.
    results = []
    for p in sorted(log_dir.glob("*.log"), reverse=True):
        try:
            for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines():
                if pattern.search(ln):
                    results.append((p.name, ln.strip()))
                    if len(results) > 10:
                        return results
        except Exception:
            continue
    return results


def main():
    active = read_active_model(Path("control/active_model.txt"))
    print("control/active_model.txt:")
    print(active or "<absent>")
    print()
    print("MT5 recent success lines (searching logs):")
    res = find_recent_mt5_success(Path("logs"), within_minutes=240)
    if not res:
        print("  (no matching lines found in logs)")
    else:
        for fn, line in res[:20]:
            print(f"  {fn}: {line}")


if __name__ == "__main__":
    main()


################################################################################
# FROM: scripts\ops\check_order.py
################################################################################
#!/usr/bin/env python3
"""Check MT5 for an order/deal by id and archive details.

Usage:
  python scripts/ops/check_order.py <order_id>

Writes: artifacts/reports/order_<order_id>_<ts>.json
"""
import sys
from pathlib import Path
import json
from datetime import datetime, timedelta

try:
    import MetaTrader5 as mt5
except Exception as e:
    print("ERROR: cannot import MetaTrader5:", e)
    sys.exit(2)

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / 'artifacts' / 'reports'

def find_order(order_id: int):
    if not mt5.initialize():
        print('ERROR: mt5.initialize() failed')
        return None

    now = datetime.now()
    # search last 7 days
    since = now - timedelta(days=7)
    try:
        deals = mt5.history_deals_get(since, now)
    except Exception:
        deals = None

    orders = None
    try:
        orders = mt5.history_orders_get(since, now)
    except Exception:
        orders = None

    found = {'deals': [], 'orders': []}
    if deals:
        for d in deals:
            try:
                if int(getattr(d, 'order', -1)) == order_id or int(getattr(d, 'ticket', -1)) == order_id:
                    found['deals'].append(d._asdict() if hasattr(d, '_asdict') else d.__dict__)
            except Exception:
                continue

    if orders:
        for o in orders:
            try:
                if int(getattr(o, 'ticket', -1)) == order_id or int(getattr(o, 'order', -1)) == order_id:
                    found['orders'].append(o._asdict() if hasattr(o, '_asdict') else o.__dict__)
            except Exception:
                continue

    mt5.shutdown()
    return found

def main():
    if len(sys.argv) < 2:
        print('usage: check_order.py <order_id>')
        return 2
    try:
        oid = int(sys.argv[1])
    except Exception:
        print('order_id must be integer')
        return 3

    res = find_order(oid)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    out = OUT_DIR / f'order_{oid}_{ts}.json'
    with out.open('w', encoding='utf-8') as f:
        json.dump(res, f, default=str, indent=2)

    print('WROTE', out)
    print('FOUND_SUMMARY deals=%d orders=%d' % (len(res.get('deals', [])), len(res.get('orders', []))))
    return 0

if __name__ == '__main__':
    rc = main()
    sys.exit(rc)


################################################################################
# FROM: scripts\ops\check_order_more.py
################################################################################
#!/usr/bin/env python3
"""Extra MT5 probes to find an order by id using multiple APIs.

Usage: python scripts/ops/check_order_more.py <order_id>
"""
import sys
from datetime import datetime, timedelta

try:
    import MetaTrader5 as mt5
except Exception as e:
    print('ERROR import MT5', e)
    sys.exit(2)

def main():
    if len(sys.argv) < 2:
        print('usage')
        return 2
    oid = int(sys.argv[1])
    if not mt5.initialize():
        print('mt5.init failed')
        return 3

    try:
        o = mt5.orders_get(ticket=oid)
        print('orders_get ->', o)
    except Exception as e:
        print('orders_get error', e)

    try:
        p = mt5.positions_get(ticket=oid)
        print('positions_get ->', p)
    except Exception as e:
        print('positions_get error', e)

    try:
        since = datetime.now() - timedelta(days=30)
        deals = mt5.history_deals_get(since, datetime.now())
        print('history_deals_get last30 count ->', len(deals) if deals else 0)
        if deals:
            for d in deals:
                if int(getattr(d,'order', -1)) == oid or int(getattr(d,'ticket',-1)) == oid:
                    print('FOUND DEAL:', d)
    except Exception as e:
        print('history_deals_get error', e)

    mt5.shutdown()
    return 0

if __name__ == '__main__':
    sys.exit(main())


################################################################################
# FROM: tools\check_imports.py
################################################################################
import traceback

modules = [
    'scripts.trade_persistence',
    'MT5_FTMO_IA.scripts._execute_recommendations_live',
]

for m in modules:
    try:
        __import__(m)
        print(f'OK import {m}')
    except Exception:
        print(f'FAIL import {m}')
        traceback.print_exc()


################################################################################
# FROM: tools\check_mt5.py
################################################################################
#!/usr/bin/env python3
"""Vérification simple et non invasive de la disponibilité de MetaTrader5.

Ce script importe MetaTrader5, tente d'initialiser la connexion, affiche
quelques informations et se ferme proprement. Il ne réalise aucune opération
de trading ni envoi d'ordres.
"""
from __future__ import annotations

import sys


def main() -> int:
    try:
        import MetaTrader5 as mt5
    except ImportError:
        print("MetaTrader5 package: NOT INSTALLED")
        return 2

    try:
        ok = False
        try:
            ok = mt5.initialize()
            print("mt5.initialize() ->", ok)
        except Exception as e:
            print("mt5.initialize() -> ERROR", e)

        try:
            ver = getattr(mt5, "__version__", None)
            if not ver and hasattr(mt5, "version"):
                try:
                    ver = mt5.version()
                except Exception:
                    ver = None
            if ver:
                print("mt5.version():", ver)
        except Exception:
            pass

        # Ne pas effectuer d'actions de trading. Shutdown propre.
        try:
            mt5.shutdown()
            print("mt5.shutdown() -> done")
        except Exception as e:
            print("mt5.shutdown() -> ERROR", e)

        return 0 if ok else 1

    except Exception as e:
        print("Unexpected error while checking MT5:", e)
        return 3


if __name__ == "__main__":
    code = main()
    sys.exit(code or 0)


################################################################################
# FROM: tools\check_readme_safety.py
################################################################################
#!/usr/bin/env python3
"""
Outil simple pour vérifier que les README du dépôt ne suppriment pas les garde-fous
ou n'encouragent pas des pratiques dangereuses (commit de secrets, suppression de preflight, etc.).

Usage:
    python tools/check_readme_safety.py

Exit code: 0 si OK, 1 si trouvées des issues critiques.
"""
import sys
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
PATTERNS_CRITICAL = [
    re.compile(r"remove.*preflight", re.I),
    re.compile(r"dry-?run removed", re.I),
    re.compile(r"100% live", re.I),
    re.compile(r"la seule règle", re.I),
    re.compile(r"supprim.*lockfile", re.I),
    re.compile(r"setx\s+", re.I),
    re.compile(r"commit.*secret", re.I),
]

PATTERNS_WARNING = [
    re.compile(r"YES GO", re.I),
    re.compile(r"AUTO_APPLY", re.I),
    re.compile(r"CONFIRM_RUN_PHRASE", re.I),
    re.compile(r"ALLOW_MT5_SEND", re.I),
]

exclusions = [".venv", "node_modules"]

issues = []

for p in ROOT.rglob("README*.md"):
    if any(part in p.parts for part in exclusions):
        continue
    text = p.read_text(encoding="utf-8", errors="ignore")
    for pat in PATTERNS_CRITICAL:
        if pat.search(text):
            issues.append({"file": str(p.relative_to(ROOT)), "type": "CRITICAL", "pattern": pat.pattern})
    # check for statements that claim single-literal authorization
    if re.search(r"La seule règle obligatoire|La seule règle", text):
        issues.append({"file": str(p.relative_to(ROOT)), "type": "CRITICAL", "pattern": "La seule règle"})
    # detect direct instructions to commit secrets or use setx persistently
    if re.search(r"setx\s+|commit .*secret|commit .*\.env", text, re.I):
        issues.append({"file": str(p.relative_to(ROOT)), "type": "CRITICAL", "pattern": "setx/commit secrets"})

# Warnings: missing mention of preflight/lockfile
for p in ROOT.rglob("README*.md"):
    if any(part in p.parts for part in exclusions):
        continue
    text = p.read_text(encoding="utf-8", errors="ignore")
    if not re.search(r"preflight|pre-?flight|tools/preflight_live_check.py", text, re.I):
        issues.append({"file": str(p.relative_to(ROOT)), "type": "WARNING", "pattern": "no preflight mention"})
    if not re.search(r"control/production.lock|production.lock|lockfile", text, re.I):
        issues.append({"file": str(p.relative_to(ROOT)), "type": "WARNING", "pattern": "no lockfile mention"})

if not issues:
    print("OK: aucune issue critique detectee dans les README projet (excl. .venv).")
    sys.exit(0)

# Print report
crit = [i for i in issues if i["type"] == "CRITICAL"]
print("README safety scan report\n")
for i in issues:
    print(f"[{i['type']}] {i['file']} => pattern: {i['pattern']}")

if crit:
    print("\nACTION REQUISE: Des issues critiques ont été detectees. Corrigez-les avant d'autoriser un run live.")
    sys.exit(1)
else:
    print("\nWarnings seulement. Veuillez examiner les fichiers listés.")
    sys.exit(0)


################################################################################
# FROM: tools\check_regime_features.py
################################################################################
"""Diagnostic non-invasif pour les features utilisées par le détecteur HMM.

Usage: exécuter localement (hors production) pour vérifier NaN/Inf/constantes
et pour lancer un entraînement rapide du détecteur de régimes (mode verbose).

Ce script n'envoie aucun ordre et n'altère aucune donnée en production.
"""
import os
import json
from datetime import datetime

import numpy as np
import pandas as pd


def load_sample_or_synth():
    path = os.path.join("data", "features_sample.csv")
    if os.path.exists(path):
        df = pd.read_csv(path)
        if "Unnamed: 0" in df.columns:
            df = df.set_index("Unnamed: 0")
            try:
                df.index = pd.to_datetime(df.index)
            except Exception:
                pass
        return df

    # Générer données synthétiques non-invasives
    idx = pd.date_range(end=pd.Timestamp.now(), periods=500, freq="1T")
    np.random.seed(42)
    returns = np.random.normal(0, 0.0005, len(idx))
    price = 1.0 + np.cumsum(returns)
    df = pd.DataFrame({
        "open": price,
        "high": price * (1 + np.abs(np.random.normal(0, 0.0002, len(idx)))),
        "low": price * (1 - np.abs(np.random.normal(0, 0.0002, len(idx)))),
        "close": price,
        "volume": np.random.randint(10, 100, len(idx)),
    }, index=idx)
    return df


def basic_feature_check(df):
    # Extraire features via le module de détection s'il existe
    from scripts.market_regime_detection import MarketRegimeDetector

    detector = MarketRegimeDetector(n_regimes=3)
    features = detector.extract_regime_features(df)

    report = {
        "generated_at": datetime.now().isoformat(),
        "n_rows": int(features.shape[0]),
        "n_columns": int(features.shape[1]),
        "columns": {},
    }

    for c in features.columns:
        s = features[c]
        report["columns"][c] = {
            "dtype": str(s.dtype),
            "n_null": int(s.isna().sum()),
            "n_inf": int(np.isinf(s.values).sum()),
            "n_unique": int(s.nunique()),
            "min": float(s.min()) if s.size else None,
            "max": float(s.max()) if s.size else None,
            "var": float(s.var()) if s.size else None,
        }

    # Tenter un entraînement HMM (non-invasif) et capturer le score
    try:
        regimes, probs, X_scaled = detector.fit_hmm_model(features)
        try:
            score = detector.hmm_model.score(X_scaled) if detector.hmm_model is not None else None
        except Exception:
            score = None
        report["hmm_score"] = float(score) if score is not None else None
        report["hmm_used"] = detector.hmm_model is not None
    except Exception as e:
        report["hmm_error"] = str(e)

    # Exporter un résumé CSV avec percentiles pour faciliter l'inspection
    try:
        out_dir = os.path.join("artifacts", "diagnostics")
        os.makedirs(out_dir, exist_ok=True)
        csv_path = os.path.join(out_dir, "regime_features_summary.csv")
        percentiles = [0.001, 0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99, 0.999]
        rows = []
        for c in features.columns:
            arr = features[c].replace([np.inf, -np.inf], np.nan).dropna().values
            if arr.size == 0:
                q = {p: None for p in percentiles}
            else:
                q = {p: float(np.quantile(arr, p)) for p in percentiles}
            rows.append({"feature": c, **{f"p{int(p*1000)}": q[p] for p in percentiles}})

        pd.DataFrame(rows).to_csv(csv_path, index=False)
        report["summary_csv"] = csv_path
    except Exception:
        report["summary_csv"] = None

    out_path = os.path.join(out_dir, f"regime_features_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print("Diagnostic écrit:", out_path)
    print("Résumé CSV:", report.get("summary_csv"))
    return report


def main():
    df = load_sample_or_synth()
    report = basic_feature_check(df)
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()


# End of merged preview
