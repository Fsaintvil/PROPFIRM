import math
from tools.order_manager_example import compute_sl_tp_and_lots


def test_compute_sl_tp_basic():
    # simple smoke test
    res = compute_sl_tp_and_lots('EURUSD', 'buy', atr=10,
                                 account_balance=100000,
                                 tick_value=10,
                                 tick_size=0.0001)
    assert 'sl_pts' in res and 'tp_pts' in res
    assert res['lots'] >= 0
