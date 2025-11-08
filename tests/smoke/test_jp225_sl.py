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
