"""generate_verification_report.py
Lit le JSON produit par la recherche dans les logs et génère :
- CSV résumé (ticket,order_id,found_in_logs,first_match_file,first_match_line)
- Markdown synthétique incluant quelques exemples
Usage: python tmp/generate_verification_report.py <json_path>
"""
import sys
import json
import csv
from pathlib import Path

if len(sys.argv) > 1:
    json_path = Path(sys.argv[1])
else:
    json_path = Path("artifacts/live_trading/orders_history_verification_logs_20251112T132154Z.json")

if not json_path.exists():
    print('JSON input not found:', json_path)
    sys.exit(2)

out_dir = json_path.parent
with open(json_path, 'r', encoding='utf-8') as f:
    data = json.load(f)

rows = []
for r in data.get('results', []):
    ticket = r.get('ticket')
    order_id = r.get('order_id')
    found = r.get('found_in_logs') or False
    matches = r.get('matches') or []
    first_file = ''
    first_line = ''
    if matches:
        first_file = matches[0].get('file','')
        first_line = matches[0].get('line','')
    rows.append((ticket, order_id, 'true' if found else 'false', first_file, first_line))

csv_path = out_dir / (json_path.stem + '.summary.csv')
md_path = out_dir / (json_path.stem + '.summary.md')

with open(csv_path, 'w', encoding='utf-8', newline='') as f:
    w = csv.writer(f)
    w.writerow(['ticket','order_id','found_in_logs','first_match_file','first_match_line'])
    for r in rows:
        w.writerow(r)

# Markdown
with open(md_path, 'w', encoding='utf-8') as f:
    f.write('# Rapport de vérification - recherche dans les logs\n\n')
    f.write(f'Généré à: {data.get("generated","?")}\n\n')
    f.write('## Résumé\n\n')
    total = len(rows)
    found = sum(1 for r in rows if r[2]=='true')
    f.write(f'- Total ordres dans CSV: {total}\\n')
    f.write(f'- Trouvés dans les logs: {found}\\n')
    f.write('\n## Exemples (premiers 10)\n\n')
    f.write('|ticket|order_id|found_in_logs|first_match_file|first_match_line|\n')
    f.write('|---|---:|---:|---|---|\n')
    for r in rows[:10]:
        # truncate long lines
        line = (r[4] or '').replace('|','\|')
        if len(line) > 120:
            line = line[:117] + '...'
        f.write(f'|{r[0]}|{r[1]}|{r[2]}|{r[3]}|{line}|\n')

print('Wrote', csv_path, 'and', md_path)
