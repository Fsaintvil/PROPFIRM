# Merged preview for prefix: 
# Generated from 6 files

################################################################################
# FROM: scripts\__init__.py
################################################################################
"""Scripts package for PROPFIRM trading system.

This package contains all trading scripts and utilities.
"""

# Package marker for scripts
__version__ = "1.0.0"


################################################################################
# FROM: scripts\_make_merge_previews.py
################################################################################
"""
Create merged preview files grouped by filename prefix.
Usage: python scripts/_make_merge_previews.py --dirs scripts tools improvements tests --out patches/merged_preview
This script is non-destructive: it only writes preview files into the out dir.
"""
import argparse
import os
from pathlib import Path


def compute_prefix(name: str) -> str:
    base = name
    if base.endswith('.py'):
        base = base[:-3]
    if '_' in base:
        return base.split('_', 1)[0]
    return base


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dirs', nargs='+', default=['scripts', 'tools', 'improvements', 'tests'])
    ap.add_argument('--out', default='patches/merged_preview')
    ap.add_argument('--root', default='.')
    ap.add_argument('--min-group', type=int, default=2, help='Minimum files to create a preview')
    args = ap.parse_args()

    root = Path(args.root).resolve()
    outdir = (root / args.out).resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    files = []
    for d in args.dirs:
        p = root / d
        if not p.exists():
            continue
        for f in p.rglob('*.py'):
            # skip venv inside tree
            if '\\.venv\\' in str(f) or '/.venv/' in str(f):
                continue
            # skip files under patches/merged_preview
            if 'patches' in f.parts and 'merged_preview' in f.parts:
                continue
            files.append(f)

    groups = {}
    for f in files:
        prefix = compute_prefix(f.name)
        groups.setdefault(prefix, []).append(f)

    created = []
    for prefix, flist in sorted(groups.items()):
        if len(flist) < args.min_group:
            continue
        # sort by path then filename
        flist_sorted = sorted(flist, key=lambda p: (str(p.parent), p.name))
        lastfile = flist_sorted[-1]
        out_name = f"{lastfile.stem}.merged_preview.py"
        out_path = outdir / out_name
        with open(out_path, 'w', encoding='utf-8') as out_f:
            out_f.write('# Merged preview for prefix: {}\n'.format(prefix))
            out_f.write('# Generated from {} files\n'.format(len(flist_sorted)))
            out_f.write('\n')
            for src in flist_sorted:
                out_f.write('#' * 80 + '\n')
                out_f.write(f"# FROM: {str(src.relative_to(root))}\n")
                out_f.write('#' * 80 + '\n')
                try:
                    content = src.read_text(encoding='utf-8')
                except Exception:
                    content = f'# ERROR reading {src}\n'
                out_f.write(content)
                out_f.write('\n\n')
            out_f.write('# End of merged preview\n')
        created.append(str(out_path.relative_to(root)))

    print('Groups found:', len(groups))
    print('Preview files created:', len(created))
    for c in created:
        print(' -', c)

if __name__ == '__main__':
    main()


################################################################################
# FROM: scripts\_smoke_test_start_production.py
################################################################################


################################################################################
# FROM: scripts\ops\_send_eurusd_order.py
################################################################################
# migration: try import safe sender (fail-open)
try:
    from src.utils.mt5_safe import send_order as _mt5_send_safe
except Exception:
    _mt5_send_safe = None

#!/usr/bin/env python3
"""Send a single EURUSD market order (small helper script).
This script is intended to be run from the repository root.
"""
import sys
try:
    import MetaTrader5 as mt5
except Exception as e:
    print("ERROR: cannot import MetaTrader5:", e)
    sys.exit(2)

SYMBOL = "EURUSD"
VOLUME = 0.01

def main():
    if not mt5.initialize():
        print("ERROR: mt5.initialize() failed")
        return 2

    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        print("ERROR: tick unavailable for", SYMBOL)
        mt5.shutdown()
        return 3

    price = tick.ask
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": VOLUME,
        "type": mt5.ORDER_TYPE_BUY,
        "price": price,
        "deviation": 20,
        "magic": 234000,
        "comment": "Manual EURUSD extra order",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    try:
        from src.utils.mt5_safe import send_order
    except Exception:
        send_order = None

    if send_order is not None:
        try:
            res = send_order(request, logger=None, mt5_module=mt5)
        except Exception as e:
            print("SEND_ERROR", e)
            mt5.shutdown()
            return 1
    else:
        res = _mt5_send_safe(request)

    try:
        # Print the important fields if available (avoid overly long single-line)
        rc = getattr(res, "retcode", None)
        oid = getattr(res, "order", None)
        comm = getattr(res, "comment", None)
        print(f"ORDER_RET {rc} ORDER_ID {oid} COMMENT {comm}")
    except Exception:
        print("ORDER_RESULT_OBJ", res)

    mt5.shutdown()
    return 0

if __name__ == '__main__':
    rc = main()
    sys.exit(rc)


################################################################################
# FROM: scripts\ops\_write_active_model_manual.py
################################################################################
from pathlib import Path
from datetime import datetime
p = Path('artifacts') / 'auto_improve' / 'best_lightgbm.txt'
model = str(p.resolve()) if p.exists() else 'None'
s = Path('control')
s.mkdir(parents=True, exist_ok=True)
tmp = s / '.active_model.tmp'
with open(tmp, 'w', encoding='utf-8') as f:
    f.write(f"loaded_model_path: {model}\n")
    f.write(f"timestamp: {datetime.utcnow().isoformat()}Z\n")
    f.write(f"pid: 30400\n")
try:
    tmp.replace(s / 'active_model.txt')
except Exception:
    tmp.rename(s / 'active_model.txt')
print('WROTE', s / 'active_model.txt')


################################################################################
# FROM: tools\__init__.py
################################################################################
"""Package initialiser for tools utilities.

This file ensures that `import tools` works reliably in the project
and provides a lightweight version marker for runtime checks.
"""
__all__ = []

__version__ = "0.1.0"

def is_tools_package():
    return True


# End of merged preview
