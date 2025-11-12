import re
from pathlib import Path
from datetime import datetime

root = Path(__file__).resolve().parent.parent
paths = [
    root / 'artifacts' / 'live_trading' / 'monitor' / 'capture_out_20251112_115314.log',
    root / 'artifacts' / 'live_trading' / 'monitor' / 'capture_err_20251112_115314.log',
    root / 'artifacts' / 'live_trading' / 'production_live_20251112_113036.out.log',
    root / 'artifacts' / 'live_trading' / 'production_live_20251112_113036.err.log',
]
lines = []
for p in paths:
    if p.exists():
        try:
            lines.extend(p.read_text(encoding='utf-8', errors='ignore').splitlines())
        except Exception:
            try:
                lines.extend(p.read_text(encoding='latin-1', errors='ignore').splitlines())
            except Exception:
                pass

order_re = re.compile(r"(?P<time>\d{4}-\d{2}-\d{2}T[^ ]+).*Ordre exécuté:\s*(?P<side>buy|sell)\s+(?P<symbol>\S+)\s+(?P<lots>[0-9.]+)\s+lots\s+à\s+(?P<price>[0-9.,]+)", re.IGNORECASE)
error_re = re.compile(r"retcode|error|ERR|ERREUR|Exception|Traceback|10013|10027", re.IGNORECASE)

orders = []
errors = []
for l in lines:
    if 'Ordre exécuté' in l:
        m = order_re.search(l)
        if m:
            orders.append({
                'time': m.group('time'),
                'side': m.group('side'),
                'symbol': m.group('symbol'),
                'lots': float(m.group('lots')),
                'price': float(m.group('price').replace(',','')),
                'raw': l,
            })
        else:
            orders.append({'raw': l})
    elif error_re.search(l):
        errors.append({'raw': l})

out_dir = root / 'artifacts' / 'live_trading' / 'monitor'
out_dir.mkdir(parents=True, exist_ok=True)
now = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
md_path = out_dir / f'AUDIT_EXECUTIONS_{now}.md'
json_path = out_dir / f'AUDIT_EXECUTIONS_{now}.json'

md_lines = [f"# Audit des exécutions — {datetime.now().isoformat()}", "", f"## Résumé", f"- Exécutions trouvées: {len(orders)}", f"- Entrées d'erreur trouvées: {len(errors)}", "", "## Exécutions détaillées", ""]
for o in orders:
    if 'time' in o:
        md_lines.append(f"- {o['time']} | {o['side'].upper()} {o['symbol']} {o['lots']} lots @ {o['price']} | raw: {o['raw']}")
    else:
        md_lines.append(f"- {o['raw']}")

md_lines.append("")
md_lines.append("## Erreurs / retcodes détectés")
for e in errors:
    md_lines.append(f"- {e['raw']}")

md_text = '\n'.join(md_lines)
json_obj = {'generated_at': datetime.now().isoformat(), 'orders': orders, 'errors': errors}

md_path.write_text(md_text, encoding='utf-8')
import json
json_path.write_text(json.dumps(json_obj, indent=2, ensure_ascii=False), encoding='utf-8')
print('WROTE_MD:', md_path)
print('WROTE_JSON:', json_path)
print('ORDERS:', len(orders), 'ERRORS:', len(errors))
