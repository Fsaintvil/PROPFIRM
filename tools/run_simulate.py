import sys
import os
from importlib import import_module

ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def run():
    m = import_module('MT5_FTMO_IA.scripts._execute_recommendations_live')
    import sys as _sys
    _sys.argv = ['', '--auth-token', 'DRY-TOKEN', '--simulate']
    m.main()


if __name__ == '__main__':
    run()
