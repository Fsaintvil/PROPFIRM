import json
from statistics import mean, median
p = r'c:/Users/saint/Documents/PROPFIRM/logs/decision_dumps.jsonl'
entries = []
with open(p, 'r', encoding='utf-8') as f:
    for l in f:
        l = l.strip()
        if not l:
            continue
        try:
            entries.append(json.loads(l))
        except Exception:
            continue
print('Total entries:', len(entries))
diffs = []
accepted = 0
rejected = 0
for e in entries:
    m = e.get('decision', {})
    dm = m.get('decision_metrics', {})
    conf = dm.get('confidence', 0.0)
    at = m.get('adaptive_threshold', 0.6)
    diff = conf - at
    diffs.append(diff)
    if conf >= at:
        accepted += 1
    else:
        rejected += 1
print('Accepted:', accepted, 'Rejected:', rejected)
if diffs:
    print('diffs min, max, mean, median:', round(min(diffs), 4), round(max(diffs),4), round(mean(diffs),4), round(median(diffs),4))
    med = median(diffs)
    if med < 0:
        suggest = abs(med)
        print('Suggested threshold decrease (approx):', round(suggest, 3))
    else:
        print('No decrease suggested (median >= 0)')
else:
    print('No diffs to analyze')
