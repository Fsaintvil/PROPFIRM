try:
    from src.utils.mt5_safe import send_order, Mt5OrderError
    print('import send_order OK', send_order)
except Exception as e:
    print('import send_order FAIL:', type(e).__name__, e)
