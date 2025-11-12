import importlib, traceback, inspect
try:
    m = importlib.import_module('src.utils.mt5_safe')
    print('module repr:', repr(m))
    print('module file:', getattr(m, '__file__', None))
    print('module type:', type(m))
    names = dir(m)
    print('len(dir)=', len(names))
    print('first 200 names:', names[:200])
    # getmembers
    try:
        members = inspect.getmembers(m)
        print('members count:', len(members))
    except Exception as e:
        print('inspect.getmembers failed:', e)
except Exception:
    traceback.print_exc()
