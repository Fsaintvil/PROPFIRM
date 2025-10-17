import traceback

modules = [
    'scripts.trade_persistence',
    'MT5_FTMO_IA.scripts._execute_recommendations_live',
]

for m in modules:
    try:
        __import__(m)
        print(f'OK import {m}')
    except Exception:
        print(f'FAIL import {m}')
        traceback.print_exc()
