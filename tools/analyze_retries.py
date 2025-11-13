"""
Analyze retry artifacts and produce a summary JSON and console table.
Writes: artifacts/live_trading/retry_analysis_<TS>.json
"""
import json,glob,os
from datetime import datetime
BASE=os.path.abspath(os.path.join(os.path.dirname(__file__),'..'))
ART=os.path.join(BASE,'artifacts','live_trading')

# find files
aggs=glob.glob(os.path.join(ART,'mt5_apply_retry_aggressive_*.json'))
stops=glob.glob(os.path.join(ART,'mt5_apply_retry_stops_level_*.json'))
enfs=glob.glob(os.path.join(ART,'mt5_enforce_sltp_rr_*.json'))
diagnostics=glob.glob(os.path.join(ART,'diagnostic_symbols_*.json'))

files={'aggressive':max(aggs,key=os.path.getmtime) if aggs else None,
       'stops':max(stops,key=os.path.getmtime) if stops else None,
       'enforce':max(enfs,key=os.path.getmtime) if enfs else None,
       'diagnostic':max(diagnostics,key=os.path.getmtime) if diagnostics else None}

summary={'timestamp':datetime.utcnow().strftime('%Y%m%dT%H%M%SZ'),'files':files,'counts':{},'errors':{},'symbols':{}}

# helper to ingest entries
def ingest_list(entries, source):
    for e in entries:
        ticket = e.get('ticket')
        sym = e.get('symbol')
        # determine error key
        err = None
        if 'error' in e and e.get('error'):
            err = e.get('error')
        elif 'result' in e and isinstance(e['result'],dict):
            err = 'retcode_'+str(e['result'].get('retcode'))
        else:
            err = 'unknown'
        # counts
        summary['counts'].setdefault(err,0)
        summary['counts'][err]+=1
        # errors detail list
        summary['errors'].setdefault(err,[])
        summary['errors'][err].append({'ticket':ticket,'symbol':sym,'entry':e,'source':source})
        # per-symbol tally
        if sym:
            s=summary['symbols'].setdefault(sym,{'tickets':set(),'errors':{}})
            s['tickets'].add(ticket)
            s['errors'].setdefault(err,0)
            s['errors'][err]+=1

# load and ingest aggressive
if files['aggressive']:
    with open(files['aggressive'],'r',encoding='utf-8') as f:
        d=json.load(f)
    entries=d.get('results') or d.get('entries') or d.get('data') or []
    ingest_list(entries,'aggressive')

# stops
if files['stops']:
    with open(files['stops'],'r',encoding='utf-8') as f:
        d=json.load(f)
    entries=d.get('results') or d.get('entries') or d.get('data') or []
    ingest_list(entries,'stops')

# enforce
if files['enforce']:
    with open(files['enforce'],'r',encoding='utf-8') as f:
        d=json.load(f)
    entries=d.get('results') or []
    # ingest using ticket, symbol, and result retcode
    for e in entries:
        ticket=e.get('ticket')
        sym=e.get('symbol')
        res=e.get('result')
        err = None
        if isinstance(res,dict) and 'retcode' in res:
            err='retcode_'+str(res.get('retcode'))
        else:
            err='dry-run' if e.get('result')=='dry-run' else 'unknown'
        summary['counts'].setdefault(err,0)
        summary['counts'][err]+=1
        summary['errors'].setdefault(err,[]).append({'ticket':ticket,'symbol':sym,'entry':e,'source':'enforce'})
        if sym:
            s=summary['symbols'].setdefault(sym,{'tickets':set(),'errors':{}})
            s['tickets'].add(ticket)
            s['errors'].setdefault(err,0)
            s['errors'][err]+=1

# load diagnostic to get delta_2_4 per symbol
diag_map={}
if files['diagnostic']:
    with open(files['diagnostic'],'r',encoding='utf-8') as f:
        dd=json.load(f)
    for sym,info in dd.get('symbols',{}).items():
        diag_map[sym]=info.get('delta_2_4')

# prepare per-symbol proposals
for sym,info in summary['symbols'].items():
    delta_relaxed = diag_map.get(sym)
    if delta_relaxed is None:
        # fallback heuristic
        delta_relaxed = 0.0002 if sym.endswith('.cash')==False else 0.1
    info['tickets'] = sorted([t for t in info['tickets'] if t])
    info['delta_2_4'] = delta_relaxed
    # convert errors to list
    info['errors'] = info['errors']

# convert errors lists to top N concise lists
top_errors = {k: [{'ticket':x['ticket'],'symbol':x['symbol']} for x in v[:10]] for k,v in summary['errors'].items()}
summary['top_errors']=top_errors

# finalize counts sort
summary['counts'] = dict(sorted(summary['counts'].items(), key=lambda kv: -kv[1]))

# write output
TS=datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
out=os.path.join(ART,f'retry_analysis_{TS}.json')
with open(out,'w',encoding='utf-8') as f:
    json.dump(summary,f,indent=2,ensure_ascii=False)

# print short table
print('Retry analysis written to',out)
print('\nCounts per error/retcode:')
for k,v in summary['counts'].items():
    print(f'  {k}: {v}')
print('\nTop errors samples:')
for k,v in top_errors.items():
    print(f'\n== {k} ({len(summary["errors"].get(k,[]))} samples) ==')
    for row in v:
        print('  ',row)

print('\nPer-symbol suggestions (delta_2_4):')
for sym,info in summary['symbols'].items():
    print(f'  {sym}: delta_2_4={info.get("delta_2_4")} tickets={len(info.get("tickets",[]))} errors={info.get("errors")}')

# exit

