import pytest

from src.utils.mt5_safe import send_order, Mt5OrderError


class _FakeResult:
    def __init__(self, retcode=10009, order=12345, comment="ok"):
        self.retcode = retcode
        self.order = order
        self.comment = comment


class _FakeSInfo:
    def __init__(self, volume_min=0.01, volume_step=0.01, digits=5, point=1e-5):
        self.volume_min = volume_min
        self.volume_step = volume_step
        self.digits = digits
        self.point = point


class _FakeMt5:
    TRADE_RETCODE_DONE = 10009

    def __init__(self, s_info: _FakeSInfo):
        self._sinfo = s_info
        self.last_sent = None

    def symbol_info(self, symbol):
        return self._sinfo

    def order_send(self, request):
        # record what was sent
        self.last_sent = dict(request)
        return _FakeResult(retcode=self.TRADE_RETCODE_DONE, order=999)

    def last_error(self):
        return (0, "no error")


def test_send_order_volume_rounds_and_succeeds():
    s_info = _FakeSInfo(volume_min=0.01, volume_step=0.01, digits=5, point=1e-5)
    fake = _FakeMt5(s_info)

    req = {
        "action": 0,
        "symbol": "EURUSD",
        "volume": 0.0105,
        "type": 0,
        "price": 1.12,
    }

    res = send_order(req.copy(), logger=None, mt5_module=fake)
    # ensure order result returned and fake recorded adjusted volume
    assert hasattr(res, "retcode")
    assert fake.last_sent is not None
    assert float(fake.last_sent["volume"]) == pytest.approx(0.01)


def test_send_order_volume_below_min_raises():
    s_info = _FakeSInfo(volume_min=0.05, volume_step=0.01, digits=5, point=1e-5)
    fake = _FakeMt5(s_info)

    req = {
        "action": 0,
        "symbol": "EURUSD",
        "volume": 0.03,
        "type": 0,
        "price": 1.12,
    }

    with pytest.raises(Mt5OrderError):
        send_order(req.copy(), logger=None, mt5_module=fake)
