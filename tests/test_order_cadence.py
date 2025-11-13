from src.utils import order_cadence as oc


def test_can_record_and_cadence(tmp_path):
    # use an isolated file for persistence
    oc.LAST_FILE = tmp_path / "last.json"
    now = 1_000_000.0

    # initially we may send
    assert oc.can_send("SYM", cooldown_s=10, now=now)

    # record a send at `now`
    oc.record_send("SYM", now=now)

    # within cooldown we cannot send
    assert not oc.can_send("SYM", cooldown_s=10, now=now + 5)

    # after cooldown we can send
    assert oc.can_send("SYM", cooldown_s=10, now=now + 11)


def test_is_exposure_aged():
    now = 2_000_000.0
    assert not oc.is_exposure_aged(now, max_age_s=300, now=now)
    assert oc.is_exposure_aged(now - 301, max_age_s=300, now=now)
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
