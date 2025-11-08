#!/usr/bin/env python3
"""Analyser logs/decision_dumps.jsonl et proposer réglages.
Usage: python scripts/analyze_decision_dumps.py [path_to_jsonl]
"""
import sys
import json
from pathlib import Path
import statistics


def load_entries(path: Path):
    if not path.exists():
        print(f'No file: {path}')
        return []
    entries = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except Exception as e:
                print('skip bad line:', e)
    return entries


def summarize(entries):
    rows = []
    for e in entries:
        d = e.get('decision', {})
        dm = d.get('decision_metrics') or {}
        conf = dm.get('confidence')
        thr = d.get('adaptive_threshold')
        if conf is None or thr is None:
            continue
        rows.append((conf, thr))

    if not rows:
        print('No usable entries')
        return

    diffs = [thr - conf for conf, thr in rows]
    positives = [d for d in diffs if d > 0]
    negatives = [d for d in diffs if d <= 0]

    print('Entries total:', len(rows))
    print('Would be accepted (conf >= thr):', len(negatives))
    print('Would be rejected (conf < thr):', len(positives))

    def pctile(xs, p):
        if not xs:
            return None
        k = max(0, min(len(xs)-1, int(len(xs)*p)))
        return sorted(xs)[k]

    print('Diffs (thr - conf) stats:')
    if diffs:
        print('  min:', min(diffs))
        print('  max:', max(diffs))
        print('  mean:', statistics.mean(diffs))
        print('  median:', statistics.median(diffs))
        print('  75pct:', pctile(diffs, 0.75))
        print('  90pct:', pctile(diffs, 0.90))

    # Recommender heuristics
    # If many rejections but small median gap, suggest small smoothing/clamp reduction
    if positives:
        med_gap = statistics.median(positives)
        mean_gap = statistics.mean(positives)
        print('\nRecommendation heuristics:')
        print(f'  median positive gap: {med_gap:.4f}, mean positive gap: {mean_gap:.4f}')
        # Suggest lowering base threshold by median gap/2 up to a cap
        suggested_lower = min(0.15, med_gap / 2)
        print(f'  Suggest lowering base_confidence_threshold by ≈ {suggested_lower:.3f} (or boosting smoothing by same)')
        # Also compute target threshold to accept X% of samples
        target_percent = 0.75
        # compute value v such that thr - conf <= v for target_percent of positives
        sorted_pos = sorted(positives)
        idx = int(len(sorted_pos) * target_percent) - 1
        idx = max(0, min(len(sorted_pos)-1, idx))
        target_v = sorted_pos[idx]
        print(f'  To accept ~{int(target_percent*100)}% of currently rejected samples, reduce threshold by ~{target_v:.3f}')
    else:
        print('\nNo positive gaps: current thresholds are permissive enough for all samples')


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('logs/decision_dumps.jsonl')
    entries = load_entries(path)
    summarize(entries)


if __name__ == '__main__':
    main()
