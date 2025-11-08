import sys
import json
from collections import Counter

def analyze(path, sample_limit=10):
    total = 0
    per_symbol = Counter()
    enh_count = 0
    actions_counter = Counter()
    samples = []

    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                # try eval-like fallback for some dumped reprs
                try:
                    rec = json.loads(line.replace("'", '"'))
                except Exception:
                    continue
            total += 1
            sym = rec.get('symbol', 'UNKNOWN')
            per_symbol[sym] += 1
            dec = rec.get('decision', {})
            if isinstance(dec, dict) and dec.get('enhancement_applied'):
                enh_count += 1
            action = dec.get('action') if isinstance(dec, dict) else None
            if action:
                actions_counter[action] += 1

            # collect samples where action != 'hold' or enhancement applied
            if (isinstance(dec, dict) and dec.get('enhancement_applied')) or (action and action.lower() != 'hold'):
                if len(samples) < sample_limit:
                    samples.append(rec)

    out = {
        'path': path,
        'total_lines': total,
        'per_symbol_counts': dict(per_symbol.most_common()),
        'enhancement_applied_count': enh_count,
        'action_counts': dict(actions_counter.most_common()),
        'samples': samples,
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: analyze_decision_dump.py <path>')
        sys.exit(2)
    analyze(sys.argv[1])
