import time
from src.utils import order_cadence


def test_can_send_and_record(tmp_path, monkeypatch):
    # Use a temp artifacts dir by monkeypatching the module constant
    tmp = tmp_path / 'artifacts' / 'live_trading'
    tmp.mkdir(parents=True)
    monkeypatch.setattr(order_cadence, 'OUT_DIR', tmp)
    monkeypatch.setattr(
        order_cadence, 'LAST_FILE', tmp / 'last_send_by_symbol.json'
    )

    now = 1_600_000_000.0
    symbol = 'TESTSYMBOL'

    # Initially can_send should be True
    assert order_cadence.can_send(symbol, cooldown_s=930, now=now)

    # Record a send at now
    order_cadence.record_send(symbol, now=now)

    # Immediately after, can_send should be False
    assert not order_cadence.can_send(symbol, cooldown_s=930, now=now + 1)

    # After cooldown, it should be allowed
    assert order_cadence.can_send(symbol, cooldown_s=930, now=now + 1000)


def test_is_exposure_aged():
    now = time.time()
    assert order_cadence.is_exposure_aged(now - 2000, max_age_s=1800, now=now)
    assert not order_cadence.is_exposure_aged(
        now - 1000, max_age_s=1800, now=now
    )
