# Merged preview for prefix: test
# Generated from 7 files

################################################################################
# FROM: tests\test_export_dryrun.py
################################################################################
import subprocess
import sys
from pathlib import Path
import pandas as pd
import pytest
 
 
def test_export_dryrun_creates_csv(tmp_path):
    script = Path("scripts/export_mt5_ohlcv_7y.py")
    if not script.exists():
        pytest.skip("Export script missing; skipping export dry-run test.")
    outdir = tmp_path / "ohlcv"
    cmd = [sys.executable, str(script), "--symbols", "BTCUSD", "--out", str(outdir), "--dry-run"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    csv = outdir / "BTCUSD_15m.csv"
    assert csv.exists(), f"Expected CSV file at {csv}"
    df = pd.read_csv(csv)
    expected = {"time", "open", "high", "low", "close", "volume"}
    assert expected.issubset(set(df.columns)), f"Missing expected columns: {expected - set(df.columns)}"


################################################################################
# FROM: tests\test_improvements.py
################################################################################
import os

from improvements.confidence_optimization import suggest_adaptive_threshold
from improvements.signal_conflict_resolution import resolve_signals


def test_suggest_adaptive_threshold_no_data():
    base = 0.6
    assert suggest_adaptive_threshold([], base) == base


def test_suggest_adaptive_threshold_with_data():
    recent = [10.0, -5.0, 2.0, 3.0, -1.0]
    new = suggest_adaptive_threshold(recent, 0.6)
    assert isinstance(new, float)
    assert 0.0 <= new <= 0.95


def test_resolve_signals_prefers_stronger():
    base = {
        "meta_learning": {"action": "buy", "confidence": 0.6},
        "regime_detection": {"action": "sell", "confidence": 0.2},
        "combined_signal": "hold",
        "confidence": 0.2,
    }
    resolved = resolve_signals(base)
    assert resolved["combined_signal"] in ("buy", "sell", "hold")
    assert 0.0 <= float(resolved.get("confidence", 0.0)) <= 0.85


################################################################################
# FROM: tests\test_mt5_safe.py
################################################################################
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


################################################################################
# FROM: tests\test_order_cadence.py
################################################################################
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


################################################################################
# FROM: tests\test_regime_validation.py
################################################################################
import pandas as pd
from scripts.market_regime_detection import MarketRegimeDetector



def test_validate_regime_input_detects_extreme_returns():
    # Build a small DataFrame with a huge jump to simulate corrupted ingestion
    df = pd.DataFrame({
        "close": [100.0, 101.0, 100000.0, 100001.0]
    })
    detector = MarketRegimeDetector(n_regimes=3)
    features = detector.extract_regime_features(df)

    ok, report = detector._validate_regime_input(df, features)
    assert ok is False
    assert "reason" in report or "exception" in report


################################################################################
# FROM: tests\smoke\test_config_loader.py
################################################################################
import json


def test_get_config_returns_empty_when_missing(tmp_path):
    cfg_path = tmp_path / "nonexistent.json"
    # Import locally to avoid side effects
    from tools.config_loader import get_config

    res = get_config(path=str(cfg_path))
    assert isinstance(res, dict)
    assert res == {}


def test_get_config_reads_file_and_get_key(tmp_path):
    data = {"confidence_threshold": 0.42, "other": "value"}
    cfg_file = tmp_path / "ai_advanced_config.json"
    cfg_file.write_text(json.dumps(data), encoding="utf-8")

    from tools.config_loader import get_config, get

    cfg = get_config(path=str(cfg_file))
    assert isinstance(cfg, dict)
    assert cfg.get("confidence_threshold") == 0.42

    # Test get helper
    assert get("confidence_threshold", None, path=str(cfg_file)) == 0.42
    assert get("missing", "def", path=str(cfg_file)) == "def"


################################################################################
# FROM: tests\smoke\test_jp225_sl.py
################################################################################
import math
import numpy as np
import pandas as pd
import importlib.util
from pathlib import Path


def test_jp225_atr_multiplier_strict():
    """Teste strictement que pour JP225 l'écart SL == ATR * 3.0 (± tol).

    Nous construisons une DataFrame synthétique identique à `tools/smoke_test_jp225.py`
    et appelons `LiveTradingEngine.calculate_dynamic_stop_loss` directement.
    """
    # Charger dynamiquement le module live_trading_engine (chemin relatif)
    ROOT = Path(__file__).resolve().parents[2]
    MODULE_PATH = ROOT / "scripts" / "live_trading_engine.py"
    spec = importlib.util.spec_from_file_location("live_trading_engine", str(MODULE_PATH))
    live_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(live_mod)
    LiveTradingEngine = getattr(live_mod, "LiveTradingEngine")

    eng = LiveTradingEngine(symbols=["JP225.cash"])

    # Générer les mêmes données synthétiques que le smoke script
    periods = 30
    np.random.seed(42)
    base = 36000.0
    moves = np.random.normal(0, 20, periods)
    closes = base + np.cumsum(moves)
    highs = closes + np.abs(np.random.normal(0, 8, periods))
    lows = closes - np.abs(np.random.normal(0, 8, periods))
    returns = np.concatenate([[0.0], np.diff(closes) / closes[:-1]])

    df = pd.DataFrame({"high": highs, "low": lows, "close": closes, "returns": returns})
    eng.live_data["JP225.cash"] = df

    entry = float(df["close"].iloc[-1])
    sl = eng.calculate_dynamic_stop_loss("JP225.cash", "buy", entry)

    # Reproduire le calcul ATR tel qu'utilisé dans le moteur
    price_range = (df["high"] - df["low"]).rolling(14).mean()
    atr_val = float(price_range.iloc[-1])
    expected_distance = atr_val * 3.0

    actual_distance = entry - float(sl)

    # Tolérance absolue: 1e-6 pour montants relatifs élevés, ou 0.5% relatif
    assert math.isfinite(atr_val) and atr_val > 0
    assert math.isclose(actual_distance, expected_distance, rel_tol=1e-3, abs_tol=1e-6)


# End of merged preview
