import glob
import os
import subprocess
from datetime import datetime

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
PATTERN = os.path.join(ROOT, '**', 'orders_audit_*.json')
ENRICH = os.path.join(ROOT, 'tools', 'enrich_orders_with_mt5.py')

if __name__ == '__main__':
    files = glob.glob(PATTERN, recursive=True)
    # skip already enriched outputs
    files = [f for f in files if 'enriched' not in os.path.basename(f)]
    if not files:
        print('no audit files found')
        raise SystemExit(0)
    print(
        'found', len(files), 'audit files, starting enrichment pass:', datetime.utcnow().isoformat()
    )
    for f in files:
        print('-> enriching', f)
        try:
            subprocess.run(['python', ENRICH, f], check=False)
        except Exception as e:
            print('error on', f, e)
    print('batch enrichment done')
