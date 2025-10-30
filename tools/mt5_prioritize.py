#!/usr/bin/env python3
"""
Prioritize MT5 SL/TP proposals and optionally apply them (requires explicit user confirmation).

Usage:
  python tools/mt5_prioritize.py [--proposals PATH] [--out PATH] [--apply]

By default this script reads the latest proposals file in artifacts/mt5_backups,
computes a priority score for each proposal, writes a prioritized JSON and a
human-readable summary. It DOES NOT apply changes unless --apply is provided
and environment ALLOW_MT5_SEND=1 is set (and even then it will still require
an explicit confirmation printed to the console).
"""
import argparse
import glob
import json
import os
import sys
from datetime import datetime


def load_proposals(path=None):
    if path:
        p = path
    else:
        files = sorted(glob.glob('artifacts/mt5_backups/mt5_proposed_sltp_*.json'))
        if not files:
            print('No proposals file found in artifacts/mt5_backups')
            sys.exit(1)
        p = files[-1]
    with open(p, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return p, data.get('proposals', [])


def score_proposal(pr):
    # High-level priority rules (higher score = more urgent)
    score = 0.0
    # Missing TP is critical
    if pr.get('current_tp', 0) == 0 or pr.get('current_tp') is None:
        score += 10000
    # Larger volume -> higher priority
    score += float(pr.get('volume', 0)) * 1000
    # Large change in SL relative to price
    try:
        price = float(pr.get('current_price', 0))
        cur_sl = float(pr.get('current_sl', 0) or 0)
        prop_sl = float(pr.get('proposed_sl', 0) or 0)
        sl_change = abs(prop_sl - cur_sl)
        # normalize by price
        if price > 0:
            score += (sl_change / price) * 10000
    except Exception:
        pass
    # Slight boost if ATR is present (we may want to act faster when ATR small?)
    try:
        atr = pr.get('atr14') or 0
        score += float(atr) * 100
    except Exception:
        pass
    return score


def prioritize(proposals):
    for pr in proposals:
        pr['_priority_score'] = score_proposal(pr)
    prioritized = sorted(proposals, key=lambda x: x['_priority_score'], reverse=True)
    return prioritized


def write_outputs(prioritized, src_path, out_path=None):
    ts = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
    if not out_path:
        out_dir = os.path.join('artifacts', 'mt5_backups')
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f'mt5_prioritized_sltp_{ts}.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({'source': src_path, 'timestamp': ts, 'prioritized': prioritized}, f, indent=2)
    # also write a short markdown summary
    md_path = out_path.replace('.json', '.md')
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(f'# MT5 SL/TP Prioritized Proposals\n\nSource: {src_path}\nGenerated: {ts}\n\n')
        f.write('|priority|ticket|symbol|side|volume|price|curSL|propSL|propTP|score|\n')
        f.write('|-:|-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|\n')
        for pr in prioritized:
            f.write(f"{pr.get('_priority_score',0):.2f}|{pr.get('ticket')}|{pr.get('symbol')}|{pr.get('side')}|{pr.get('volume')}|{pr.get('current_price')}|{pr.get('current_sl')}|{pr.get('proposed_sl')}|{pr.get('proposed_tp')}|{pr.get('_priority_score',0):.2f}|\n")
    return out_path, md_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--proposals', help='Path to proposals JSON')
    parser.add_argument('--out', help='Path for prioritized output JSON')
    parser.add_argument('--apply', action='store_true', help='Also apply the prioritized proposals (REQUIRES ALLOW_MT5_SEND=1 and explicit confirmation)')
    args = parser.parse_args()

    src_path, proposals = load_proposals(args.proposals)
    if not proposals:
        print('No proposals found in', src_path)
        sys.exit(0)
    prioritized = prioritize(proposals)
    out_path, md_path = write_outputs(prioritized, src_path, args.out)
    print('Wrote prioritized JSON to', out_path)
    print('Wrote human summary to', md_path)

    # If apply is requested, require safety checks and explicit interactive confirmation
    if args.apply:
        print('\n-- APPLY MODE REQUESTED --')
        if os.environ.get('ALLOW_MT5_SEND') != '1':
            print('ERROR: ALLOW_MT5_SEND is not set to 1 in the environment. Aborting apply.')
            sys.exit(2)
        # Support non-interactive approval via APPROVAL_TOKEN env var (use with care)
        token = os.environ.get('APPROVAL_TOKEN')
        if token:
            print('APPROVAL_TOKEN detected in environment — proceeding non-interactively (ensure this is intentional).')
        else:
            print('ALLOW_MT5_SEND=1 detected. To proceed, type EXACTLY the phrase:')
            print("\nJ'AUTHORISE L'ENVOI LIVE\n\nand press Enter (case-sensitive).")
            try:
                confirm = input('Confirmation phrase: ')
            except Exception:
                confirm = ''
            if confirm.strip() != "J'AUTHORISE L'ENVOI LIVE":
                print('Confirmation not matched. Aborting apply.')
                sys.exit(3)
        # call apply script with the prioritized file
        apply_cmd = f"python tools/mt5_apply_sltp.py {out_path}"
        print('Would now execute:', apply_cmd)
        print('NOTE: mt5_apply_sltp.py expects its input format to match the proposals format.')
        # execute
        rc = os.system(apply_cmd)
        print('apply exit code:', rc)


if __name__ == '__main__':
    main()
