import os
from pathlib import Path

root = Path('MT5_FTMO_IA')
ctrl = root / 'control'
ks = ctrl / 'kill_switch'
auto = ctrl / 'auto_approve'
sec = ctrl / 'auto_approve_secondary'
print('KILL_SWITCH exists:', ks.exists(), str(ks))
print('AUTO_APPROVE exists:', auto.exists(), str(auto))
print('AUTO_APPROVE_SECOND exists:', sec.exists(), str(sec))
print('ALLOW_LIVE_SEND env:', os.getenv('ALLOW_LIVE_SEND'))

# Run package-level diagnostics
try:
    from MT5_FTMO_IA.scripts import mt5_connector, safety
    print('safety.can_send_live():', safety.can_send_live())
    info = mt5_connector.check_mt5_compatibility()
    print(
        'mt5 check: ok=', info.get('ok'),
        'mt5_imported=', info.get('mt5_imported')
    )
    print('mt5 path:', info.get('mt5_path'))
    print('numpy:', info.get('numpy'))
except Exception as e:
    import traceback
    traceback.print_exc()
    print('DIAG ERROR:', e)
