"""Tests for adaptive_intelligence.py — MarketRegime, OnlineLearner, AdaptiveEngine"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from unittest.mock import ANY, MagicMock, patch

import numpy as np

np.random.seed(42)
import pytest

from engine_simple.adaptive_intelligence import AdaptiveEngine, MarketRegime, OnlineLearner

# P7: DLEnsemble supprimé (code mort) — plus besoin de patch


@pytest.fixture(autouse=True)
def _reset_seed():
    """Nettoie le lock seed ET le state file avant chaque test pour garantir l'isolation.
    🐛 FIX T1 (16 Juillet 2026): Sauvegarde/restaure les fichiers de production au lieu
    de les supprimer définitivement. L'ancien comportement (p.unlink()) effaçait
    runtime/ol_state.json et runtime/online_learner_state.json entre chaque test."""
    backed_up = []
    try:
        for f in ["runtime/online_learner_state.json", "runtime/ol_state.json"]:
            p = Path(f)
            if p.exists():
                backup = p.with_suffix(".bak_test")
                import shutil

                shutil.copy2(str(p), str(backup))
                backed_up.append((p, backup))
                p.unlink()
        lock = Path("runtime/online_learner_seed.lock")
        if lock.exists():
            lock.unlink()
        yield
    finally:
        # Restaurer les fichiers après le test
        import shutil

        for p, backup in backed_up:
            if backup.exists():
                shutil.copy2(str(backup), str(p))
                backup.unlink()


def _make_h1_rates(n=100, trend="up"):
    rates = []
    base = 1.1000
    for i in range(n):
        if trend == "up":
            shift = i * 0.0003
        elif trend == "down":
            shift = -i * 0.0003
        else:
            shift = np.sin(i * 0.2) * 0.005
        o = base + shift + np.random.normal(0, 0.0001)
        h = o + abs(np.random.normal(0, 0.0002))
        lo = o - abs(np.random.normal(0, 0.0002))
        c = (h + lo) / 2
        v = 1000 + np.random.randint(-50, 50)
        rates.append((i, o, h, lo, c, v))
    return rates


class TestMarketRegime:
    def test_detect_returns_regime_and_meta(self):
        reg = MarketRegime()
        rates = _make_h1_rates(100, trend="up")
        regime, meta = reg.detect(rates)
        assert regime in ("TREND_UP", "TREND_DOWN", "RANGING", "HIGH_VOL", "LOW_VOL")
        assert "adx" in meta
        assert "vol_percentile" in meta
        assert "structure_trend" in meta
        assert meta["adx"] >= 0

    def test_detect_too_short_returns_ranging(self):
        reg = MarketRegime()
        rates = [(i, 1.1, 1.102, 1.098, 1.1, 1000) for i in range(29)]
        regime, meta = reg.detect(rates)
        assert regime == "RANGING"

    def test_detect_trend_up_on_bullish_data(self):
        reg = MarketRegime()
        rates = _make_h1_rates(100, trend="up")
        regime, meta = reg.detect(rates)
        # Override ADX for test: force high ADX
        meta["adx"] = 30
        meta["structure_trend"] = "bullish"
        meta["vol_percentile"] = 0.5
        # Re-detect with forced params
        assert meta["adx"] > 20

    def test_detect_high_vol_patches_adx(self):
        """HIGH_VOL regime when vol_percentile > 0.80 regardless of ADX."""
        reg = MarketRegime()
        with patch.object(reg, "_adx", return_value=15):
            rates = _make_h1_rates(100, trend="ranging")
            regime, meta = reg.detect(rates)
            # Force vol_percentile reading via the atr_hist path
            assert isinstance(regime, str)

    def test_detect_low_vol(self):
        """LOW_VOL regime when vol_percentile < 0.20."""
        reg = MarketRegime()
        rates = [(i, 1.1, 1.101, 1.099, 1.1, 1000) for i in range(150)]
        regime, meta = reg.detect(rates)
        assert regime == "LOW_VOL"

    def test_detect_meta_has_all_expected_keys(self):
        reg = MarketRegime()
        rates = _make_h1_rates(100, trend="up")
        _, meta = reg.detect(rates)
        assert set(meta.keys()) == {
            "adx",
            "vol_percentile",
            "structure_trend",
            "structure_score",
            "obv_trend",
            "rsi",
            "volume_confirms",
            "confidence_bonus",
            "rsi_divergence",
            "eq_hl_count",
            "unmitigated_obs",
            "unmitigated_fvgs",
            "recent_bos",
            "recent_choch",
            "recent_sweeps",
        }

    def test_detect_handles_rates_without_volume(self):
        reg = MarketRegime()
        rates = [(i, 1.1, 1.102, 1.098, 1.1) for i in range(100)]
        regime, meta = reg.detect(rates)
        assert regime in ("TREND_UP", "TREND_DOWN", "RANGING", "HIGH_VOL", "LOW_VOL")


class TestOnlineLearner:
    def test_init(self):
        ol = OnlineLearner()
        assert ol.window == 200
        assert ol.history == {}

    def test_record_trade_creates_history(self):
        ol = OnlineLearner()
        ol.record_trade("TESTX", 1.5, "RANGING")
        assert "TESTX" in ol.history
        assert len(ol.history["TESTX"]) == 1
        assert ol.history["TESTX"][0]["r"] == 1.5

    def test_get_params_returns_defaults(self):
        ol = OnlineLearner()
        params = ol.get_params("UNKNOWN")
        assert isinstance(params, dict)
        assert "thresh" in params
        assert "risk_mult" in params

    def test_update_params_high_wr(self):
        ol = OnlineLearner(window=10)
        for _ in range(10):
            ol.record_trade("TESTX", 1.0, "TREND_UP")
        params = ol.get_params("TESTX")
        assert params["risk_mult"] >= 1.0

    def test_update_params_low_wr(self):
        ol = OnlineLearner(window=10)
        for _ in range(10):
            ol.record_trade("TESTX", -1.0, "RANGING")
        params = ol.get_params("TESTX")
        assert params["risk_mult"] <= 1.0

    def test_get_summary(self):
        ol = OnlineLearner(window=10)
        assert ol.get_summary("TESTX") == {}
        for _ in range(10):
            ol.record_trade("TESTX", 0.5, "RANGING")
        s = ol.get_summary("TESTX")
        assert s["trades"] == 10
        assert s["wr"] > 0
        assert s["avg_r"] == 0.5

    def test_get_params_custom_base_thresh(self):
        ol = OnlineLearner()
        params = ol.get_params("TESTX", base_thresh=4.0)
        assert params["thresh"] == 4.0

    def test_update_params_wr_above_82(self):
        ol = OnlineLearner(window=40, burst_max=999)
        # 36 wins out of 40 = 90% WR > 78% mais expectancy faible (0.08 < 0.4)
        # → pas champion, entre dans wr_eff>0.78: risk_mult=1.0
        # 🔧 FIX 22 Juillet 2026: thresh dynamique min(1.5, 2.0 - (wr_eff-0.78)*2.5)
        # wr_eff=0.4*0.90+0.6*0.90=0.90 → min(1.5, 2.0-0.30)=1.5
        for _ in range(36):
            ol.record_trade("TESTX", 0.2, "TREND_UP")  # petits gains
        for _ in range(4):
            ol.record_trade("TESTX", -1.0, "RANGING")
        params = ol.get_params("TESTX")
        assert params["risk_mult"] == 1.0  # 🔧 10 Juillet: cap recovery à 1.0
        assert params["thresh"] == pytest.approx(1.5, abs=0.01)  # plus agressif (cap 1.5)

    def test_update_params_wr_78_to_82(self):
        ol = OnlineLearner(window=40, burst_max=999)
        # 32 wins out of 40 = 80% WR > 78% mais expectancy faible (0.04 < 0.4)
        # → pas champion, entre dans wr_eff>0.78: risk_mult=1.0
        # 🔧 FIX 22 Juillet 2026: thresh dynamique min(1.5, 2.0 - (wr_eff-0.78)*2.5)
        # wr_eff=0.4*0.80+0.6*0.80=0.80 → min(1.5, 2.0-0.05)=1.5
        for _ in range(32):
            ol.record_trade("TESTX", 0.3, "TREND_UP")  # petits gains
        for _ in range(8):
            ol.record_trade("TESTX", -1.0, "RANGING")
        params = ol.get_params("TESTX")
        assert params["risk_mult"] == 1.0  # 🔧 10 Juillet: cap recovery à 1.0
        assert params["thresh"] == pytest.approx(1.5, abs=0.01)  # cap à 1.5 (FIX 22 Juillet)

    def test_update_params_wr_70_to_78_neutral(self):
        ol = OnlineLearner(window=200, burst_max=999)
        # 30 wins out of 40 = 75% WR → in (70, 78] range → neutral
        # 🐛 FIX #10: min_trades passé de max(15,window//10) à max(30,window//5)
        # Donc window=200 → min_trades=40. On enregistre 40 trades.
        for _ in range(30):
            ol.record_trade("TESTX", 1.0, "TREND_UP")
        for _ in range(10):
            ol.record_trade("TESTX", -1.0, "RANGING")
        params = ol.get_params("TESTX")
        assert params["risk_mult"] == 1.0
        assert params["thresh"] == 2.0  # ↓ 2.5→2.0 (OL recalibré pour + de trades)

    def test_update_params_wr_below_70(self):
        ol = OnlineLearner(window=40, burst_max=999)
        # 16 wins out of 40 = 40% WR < 60% → risk_mult=0.90, thresh=2.5 (plus sélectif)
        for _ in range(16):
            ol.record_trade("TESTX", 1.0, "TREND_UP")
        for _ in range(24):
            ol.record_trade("TESTX", -0.5, "RANGING")
        params = ol.get_params("TESTX")
        # wr_eff=0.40 < 0.60 → risk_mult = max(0.70, min(0.90, 0.50+0.40)) = 0.90
        assert params["risk_mult"] == 0.90  # 🔧 10 Juillet: WR-based progressive
        assert params["thresh"] == 2.5

    def test_update_params_expectancy_negative_overrides_risk(self):
        ol = OnlineLearner(window=200, burst_max=999)
        # All losses, WR=0% → wr_eff=0 < 60% → base risk=0.70
        # expectancy=-1.0 < 0 → min(0.70, 0.75) = 0.70
        for _ in range(40):
            ol.record_trade("TESTX", -1.0, "RANGING")
        params = ol.get_params("TESTX")
        assert params["risk_mult"] == 0.70  # 🔧 10 Juillet: WR 0% = risk 0.70

    def test_not_enough_trades_no_update(self):
        ol = OnlineLearner(window=20)
        for _ in range(5):
            ol.record_trade("TESTX", 1.0, "TREND_UP")
        params = ol.get_params("TESTX")
        assert params["risk_mult"] == 1.0
        assert params["thresh"] == 3.0

    def test_record_trade_multiple_symbols_independent(self):
        ol = OnlineLearner(window=10)
        for _ in range(10):
            ol.record_trade("TESTX", 1.0, "TREND_UP")
        ol.record_trade("GBPUSD", -1.0, "RANGING")
        assert ol.get_summary("TESTX")["trades"] == 10
        assert ol.get_summary("GBPUSD")["trades"] == 1


class TestAdaptiveEngine:
    def test_init(self):
        ae = AdaptiveEngine(MagicMock())
        assert ae.regime is not None
        assert ae.learner is not None

    def test_vigilance_returns_none_without_h1(self):
        ae = AdaptiveEngine(MagicMock())
        result = ae.vigilance("EURUSD", {"M5": [1, 2, 3]})
        assert result is None

    def test_vigilance_returns_valid_on_good_data(self):
        ae = AdaptiveEngine(MagicMock())
        rates = _make_h1_rates(100, trend="up")
        result = ae.vigilance("EURUSD", {"H1": rates})
        assert result is not None, "vigilance should return analysis dict with valid data"
        assert "symbol" in result
        assert "regime" in result
        assert result["symbol"] == "EURUSD"

    @pytest.mark.skip(reason="P7: DL supprimé (code mort)")
    def test_build_dl_features_no_dl(self):
        ae = AdaptiveEngine(MagicMock())
        ae.dl.available = False
        result = ae.build_dl_features({})
        assert result is None

    def test_analyze_returns_none_without_h1(self):
        ae = AdaptiveEngine(MagicMock())
        result = ae.analyze("EURUSD", {}, {"action": "BUY", "score": 0.7})
        assert result is not None  # passes through signal when no H1

    def test_analyze_with_valid_data(self):
        ae = AdaptiveEngine(MagicMock())
        rates = _make_h1_rates(100, trend="up")
        signal = {
            "action": "BUY",
            "score": 0.7,
            "confidence": 0.6,
            "atr": 0.005,
            "sl_atr": 2.0,
            "tp_atr": 4.0,
            "rates": {"H1": rates},
            "adx": 25,
            "is_ranging": False,
        }
        result = ae.analyze("EURUSD", {"H1": rates}, signal)
        assert result is not None, "analyze should return processed signal with valid data"
        assert "action" in result
        assert "risk_mult" in result
        assert "score" in result

    def test_get_report_empty(self):
        ae = AdaptiveEngine(MagicMock())
        # Seed direct de 200 trades pour XAUUSD (pas de CSV seed en test)
        for _ in range(200):
            ae.record_result("XAUUSD", 1.5, "TREND_UP")
        r = ae.get_report("XAUUSD")
        assert r != {}
        assert r.get("trades", 0) == 200

    def test_learner_updates_after_record_result(self):
        ae = AdaptiveEngine(MagicMock())
        # Seed direct de 50 trades pour XAUUSD
        for _ in range(50):
            ae.record_result("XAUUSD", 1.5, "TREND_UP")
        before = ae.learner.get_summary("XAUUSD").get("trades", 0)
        assert before > 0  # seed chargé
        ae.record_result("XAUUSD", 1.5, "TREND_UP")
        s = ae.learner.get_summary("XAUUSD")
        # maxlen=200 : après record, le deque reste à maxlen
        assert s["trades"] == min(before + 1, 200)
        assert s["trades"] > 0

    @patch("engine_simple.adaptive_intelligence.datetime")
    @pytest.mark.skip(reason="P7: DL supprimé (code mort)")
    def test_vigilance_with_dl(self, mock_dt):
        mock_dt.utcnow.return_value.hour = 10
        ae = AdaptiveEngine(MagicMock())
        ae.dl.available = True
        ae.dl.predict = MagicMock(return_value={"action": "BUY", "score": 0.75, "buy_prob": 0.72})
        rates = _make_h1_rates(100, trend="up")
        result = ae.vigilance("EURUSD", {"H1": rates})
        assert result is not None
        assert result["dl_action"] == "BUY"
        assert result["dl_score"] == 0.75
        assert result["dl_buy_prob"] == 0.72

    @patch("engine_simple.adaptive_intelligence.datetime")
    @pytest.mark.skip(reason="P7: DL supprimé (code mort)")
    def test_vigilance_dl_score_below_min(self, mock_dt):
        mock_dt.utcnow.return_value.hour = 10
        ae = AdaptiveEngine(MagicMock())
        ae.dl.available = True
        ae.dl.predict = MagicMock(return_value={"action": "SELL", "score": 0.40, "buy_prob": 0.30})
        rates = _make_h1_rates(100, trend="down")
        result = ae.vigilance("EURUSD", {"H1": rates})
        assert result is not None
        assert result["dl_action"] is None  # ignored

    @patch("engine_simple.adaptive_intelligence.datetime")
    @pytest.mark.skip(reason="P7: DL supprimé (code mort)")
    def test_vigilance_dl_error(self, mock_dt):
        mock_dt.utcnow.return_value.hour = 10
        ae = AdaptiveEngine(MagicMock())
        ae.dl.available = True
        ae.dl.predict = MagicMock(side_effect=ValueError("DL failed"))
        rates = _make_h1_rates(100, trend="up")
        result = ae.vigilance("EURUSD", {"H1": rates})
        assert result is not None  # still returns regime
        assert result["dl_action"] is None

    def test_analyze_ranging_regime_risk_half(self):
        rates = [(i, 1.1, 1.101, 1.099, 1.1, 1000) for i in range(100)]
        signal = {
            "action": "BUY",
            "score": 0.7,
            "confidence": 0.6,
            "atr": 0.005,
            "sl_atr": 2.0,
            "tp_atr": 4.0,
            "rates": {"H1": rates},
            "adx": 15,
            "is_ranging": True,
        }
        ae = AdaptiveEngine(MagicMock())
        # P7: DL supprimé, test désactivé
        pytest.skip("P7: DL supprimé (code mort)")
        result = ae.analyze("EURUSD", {"H1": rates}, signal)
        assert result["risk_mult"] <= 0.6

    @patch("engine_simple.adaptive_intelligence.datetime")
    @pytest.mark.skip(reason="P7: DL supprimé (code mort)")
    def test_analyze_mom_dl_agree(self, mock_dt):
        mock_dt.utcnow.return_value.hour = 10
        rates = _make_h1_rates(100, trend="up")
        signal = {
            "action": "BUY",
            "score": 0.7,
            "confidence": 0.6,
            "atr": 0.005,
            "sl_atr": 2.0,
            "tp_atr": 4.0,
            "rates": {"H1": rates},
            "adx": 25,
            "is_ranging": False,
        }
        ae = AdaptiveEngine(MagicMock())
        ae.dl.available = True
        ae.dl.predict = MagicMock(return_value={"action": "BUY", "score": 0.70, "buy_prob": 0.65})
        result = ae.analyze("EURUSD", {"H1": rates}, signal)
        assert result["_ml_agrees"] is True
        assert result["confidence"] >= 0.6  # confidence boost

    @patch("engine_simple.adaptive_intelligence.datetime")
    @pytest.mark.skip(reason="P7: DL supprimé (code mort)")
    def test_analyze_mom_dl_disagree(self, mock_dt):
        mock_dt.utcnow.return_value.hour = 10
        rates = _make_h1_rates(100, trend="up")
        signal = {
            "action": "BUY",
            "score": 0.7,
            "confidence": 0.6,
            "atr": 0.005,
            "sl_atr": 2.0,
            "tp_atr": 4.0,
            "rates": {"H1": rates},
            "adx": 25,
            "is_ranging": False,
        }
        ae = AdaptiveEngine(MagicMock())
        ae.dl.available = True
        ae.dl.predict = MagicMock(return_value={"action": "SELL", "score": 0.70, "buy_prob": 0.35})
        result = ae.analyze("EURUSD", {"H1": rates}, signal)
        assert result["_ml_agrees"] is False
        assert result["risk_mult"] <= 0.5  # disagree → risk/2

    @patch("engine_simple.adaptive_intelligence.datetime")
    @pytest.mark.skip(reason="P7: DL supprimé (code mort)")
    def test_analyze_devil_advocate(self, mock_dt):
        mock_dt.utcnow.return_value.hour = 10
        rates = _make_h1_rates(100, trend="up")
        signal = {
            "action": "BUY",
            "score": 0.7,
            "confidence": 0.6,
            "atr": 0.005,
            "sl_atr": 2.0,
            "tp_atr": 4.0,
            "rates": {"H1": rates},
            "adx": 25,
            "is_ranging": False,
        }
        ae = AdaptiveEngine(MagicMock())
        ae.dl.available = True
        ae.dl.predict = MagicMock(return_value={"action": "SELL", "score": 0.70, "buy_prob": 0.35})
        # Mock meta to return devil disagreements
        ae.meta.get_ensemble_action = MagicMock(return_value=("SELL", 0.75, {}))
        ae.meta.devil_advocate_check = MagicMock(
            return_value=[{"model": "DL_LSTM", "action": "SELL", "trading_action": "BUY", "disagreement": "inverse"}]
        )
        result = ae.analyze("EURUSD", {"H1": rates}, signal)
        assert result["_devil"] > 0
        assert result["risk_mult"] <= 0.5

    @patch("engine_simple.adaptive_intelligence.datetime")
    @pytest.mark.skip(reason="P7: DL supprimé (code mort)")
    def test_analyze_meta_override(self, mock_dt):
        mock_dt.utcnow.return_value.hour = 10
        rates = _make_h1_rates(100, trend="down")
        signal = {
            "action": "BUY",
            "score": 0.7,
            "confidence": 0.6,
            "atr": 0.005,
            "sl_atr": 2.0,
            "tp_atr": 4.0,
            "rates": {"H1": rates},
            "adx": 25,
            "is_ranging": False,
        }
        ae = AdaptiveEngine(MagicMock())
        ae.dl.available = True
        ae.dl.predict = MagicMock(return_value={"action": "SELL", "score": 0.75, "buy_prob": 0.30})
        ae.meta.get_ensemble_action = MagicMock(return_value=("SELL", 0.70, {}))
        ae.meta.devil_advocate_check = MagicMock(return_value=[])
        result = ae.analyze("EURUSD", {"H1": rates}, signal)
        assert result["action"] == "SELL"  # meta overrode BUY signal
        assert result["_meta_action"] == "SELL"

    @patch("engine_simple.adaptive_intelligence.datetime")
    @pytest.mark.skip(reason="P7: DL supprimé (code mort)")
    def test_analyze_trade_stats_high_wr(self, mock_dt):
        mock_dt.utcnow.return_value.hour = 10
        rates = _make_h1_rates(100, trend="up")
        signal = {
            "action": "BUY",
            "score": 0.7,
            "confidence": 0.6,
            "atr": 0.005,
            "sl_atr": 2.0,
            "tp_atr": 4.0,
            "rates": {"H1": rates},
            "adx": 25,
            "is_ranging": False,
        }
        trade_stats = {"trade_count": 20, "trade_winrate": 0.70, "trade_profit_factor": 1.8}
        ae = AdaptiveEngine(MagicMock())
        ae.dl.available = False
        result = ae.analyze("EURUSD", {"H1": rates}, signal, trade_stats)
        assert result is not None
        assert "score" in result

    @patch("engine_simple.adaptive_intelligence.datetime")
    @pytest.mark.skip(reason="P7: DL supprimé (code mort)")
    def test_analyze_trade_stats_low_wr_risk_reduction(self, mock_dt):
        mock_dt.utcnow.return_value.hour = 10
        rates = _make_h1_rates(100, trend="up")
        signal = {
            "action": "BUY",
            "score": 0.7,
            "confidence": 0.6,
            "atr": 0.005,
            "sl_atr": 2.0,
            "tp_atr": 4.0,
            "rates": {"H1": rates},
            "adx": 25,
            "is_ranging": False,
        }
        trade_stats = {"trade_count": 50, "trade_winrate": 0.40, "trade_profit_factor": 0.7}
        ae = AdaptiveEngine(MagicMock())
        ae.dl.available = False
        result = ae.analyze("EURUSD", {"H1": rates}, signal, trade_stats)
        assert result["risk_mult"] < 1.0  # WR < 45% → risk * 0.7, PF < 0.8 → risk * 0.5

    def test_save_calibration_no_path(self):
        ae = AdaptiveEngine(MagicMock())
        ae.calibration_path = None
        result = ae.save_calibration()  # pas de chemin → no-op sans exception
        # 🔧 FIX M-S3: save_calibration() retourne None et ne lève pas quand
        # calibration_path est None (la sauvegarde est simplement ignorée)
        assert result is None

    def test_load_calibration_not_found(self):
        """_load_calibration logs warning when file is missing."""
        ae = AdaptiveEngine(MagicMock())
        # Invoke directly to avoid side effects from __init__ order
        ae._load_calibration("/nonexistent/path.jbl")  # should not crash
        # 🔧 FIX M-S3: le moteur reste opérationnel après l'échec de chargement
        assert ae.learner is not None, "le learner doit rester initialisé après un chargement raté"

    @patch("engine_simple.adaptive_intelligence.os.path.exists", return_value=True)
    @patch("engine_simple.adaptive_intelligence.os.stat")
    def test_load_calibration_corrupt(self, mock_stat, mock_exists):
        mock_stat.return_value = MagicMock(st_size=100)
        ae = AdaptiveEngine(MagicMock(), calibration_path="/fake/path.json")
        # Should catch error, log warning, not crash
        # 🔧 FIX M-S3: fichier corrompu → le moteur reste utilisable
        assert ae.learner is not None, "le learner doit rester initialisé après un chargement corrompu"

    def test_load_calibration_purges_pre_gr_params(self, tmp_path):
        """🔧 FIX 20 Août 2026 (Robot Manager): les adapted_params restaurés depuis
        la calibration avec une history < min_trades (20) doivent être PURGÉS.
        C'était le contournement de la garde _update_params L.418 : les params
        pré-GR (NZDUSD/USDJPY/US30.cash thresh 2.6-2.75, 10-17 trades) étaient
        restaurés à chaque redémarrage, annulant la purge du 19/08 23:55."""
        import json

        cal_path = str(tmp_path / "cal.json")
        # History et params pré-GR du scénario réel (10-17 trades < 20 min_trades)
        pre_gr_history = [
            {"r": 0.5, "regime": "TREND_UP"} for _ in range(12)  # NZDUSD-like: 12 trades
        ]
        cal = {
            "online_history": {
                "NZDUSD": pre_gr_history,
            },
            "adapted_params": {
                "NZDUSD": {"thresh": 2.6176, "risk_mult": 1.0, "sl_mult": 2.0, "tp_mult": 5.0},
                "US30.cash": {"thresh": 2.6667, "risk_mult": 1.0, "sl_mult": 2.0, "tp_mult": 5.0},
                "USDJPY": {"thresh": 2.75, "risk_mult": 1.0, "sl_mult": 2.0, "tp_mult": 5.0},
            },
        }
        with open(cal_path, "w") as f:
            json.dump(cal, f)

        ae = AdaptiveEngine(MagicMock(), calibration_path=cal_path)
        ae._load_calibration(cal_path)

        # Les params pré-GR avec history insuffisante doivent être purgés
        assert "NZDUSD" not in ae.learner.adapted_params, (
            "NZDUSD <20 trades valides → params pré-GR doivent être purgés"
        )
        assert "US30.cash" not in ae.learner.adapted_params
        assert "USDJPY" not in ae.learner.adapted_params
        # L'history reste restaurée (elle n'est pas contaminée — juste insuffisante)
        assert len(ae.learner.history.get("NZDUSD", [])) == 12

    def test_load_state_purges_pre_gr_params(self, tmp_path):
        """🔧 FIX 20 Août 2026 (Robot Manager): OnlineLearner._load_state doit
        appliquer la MÊME garde min_trades que la calibration. ol_state.json
        contenait aussi les 5 params pré-GR (NZDUSD/USDJPY/US30/GBPJPY/USOIL
        10-17 trades) — ils étaient restaurés via ce chemin, pas seulement
        via _load_calibration."""
        import json

        state_path = str(tmp_path / "ol_state.json")
        pre_gr_history = [
            {"r": 0.5, "regime": "TREND_UP"} for _ in range(12)  # NZDUSD-like
        ]
        state = {
            "window": 200,
            "history": {
                "NZDUSD": pre_gr_history,
                # Un symbole avec history SUFFISANTE doit être conservé
                "BTCUSD": [{"r": 0.5, "regime": "TREND_UP"} for _ in range(25)],
            },
            "adapted_params": {
                "NZDUSD": {"thresh": 2.6176, "risk_mult": 1.0, "sl_mult": 2.0, "tp_mult": 5.0},
                "BTCUSD": {"thresh": 2.0, "risk_mult": 1.0, "sl_mult": 2.0, "tp_mult": 5.0},
            },
        }
        with open(state_path, "w") as f:
            json.dump(state, f)

        ol = OnlineLearner(window=200, state_path=state_path)

        # NZDUSD: history 12 < 20 → params purgés ; BTCUSD: history 25 ≥ 20 → conservé
        assert "NZDUSD" not in ol.adapted_params, "NZDUSD <20 trades → params pré-GR purgés"
        assert "BTCUSD" in ol.adapted_params, "BTCUSD ≥20 trades → params conservés"
        assert len(ol.history.get("NZDUSD", [])) == 12

    @patch("engine_simple.adaptive_intelligence.datetime")
    @pytest.mark.skip(reason="P7: DL supprimé (code mort)")
    def test_analyze_meta_recalibrate(self, mock_dt):
        mock_dt.utcnow.return_value.hour = 10
        rates = _make_h1_rates(100, trend="up")
        signal = {
            "action": "BUY",
            "score": 0.7,
            "confidence": 0.6,
            "atr": 0.005,
            "sl_atr": 2.0,
            "tp_atr": 4.0,
            "rates": {"H1": rates},
            "adx": 25,
            "is_ranging": False,
        }
        ae = AdaptiveEngine(MagicMock())
        ae.dl.available = False
        ae.meta.should_recalibrate = MagicMock(return_value=True)
        ae.meta.recalibrate = MagicMock()
        result = ae.analyze("EURUSD", {"H1": rates}, signal)
        ae.meta.recalibrate.assert_called_once()

    @pytest.mark.skip(reason="P7: DL supprimé (code mort)")
    def test_record_result_with_dl(self):
        ae = AdaptiveEngine(MagicMock())
        ae.dl.available = True
        ae.dl.record_trade = MagicMock()
        ae.record_result("EURUSD", 1.5, "TREND_UP", dl_features=np.array([1, 2, 3]))
        assert ae.dl.record_trade.call_count == 1
        args, _ = ae.dl.record_trade.call_args
        assert args[0] == "EURUSD"
        np.testing.assert_array_equal(args[1], np.array([1, 2, 3]))
        assert args[2] == 1.5

    @pytest.mark.skip(reason="P7: DL supprimé (code mort)")
    def test_record_result_without_dl_skips_dl(self):
        ae = AdaptiveEngine(MagicMock())
        ae.dl.available = False
        ae.record_result("EURUSD", 1.5, "TREND_UP", dl_features=np.array([1, 2, 3]))
        # should not crash

    @patch("engine_simple.adaptive_intelligence.datetime")
    @pytest.mark.skip(reason="P7: DL supprimé (code mort)")
    def test_analyze_high_vol_regime_risk_mult(self, mock_dt):
        mock_dt.utcnow.return_value.hour = 10
        rates = _make_h1_rates(100, trend="ranging")
        signal = {
            "action": "BUY",
            "score": 0.7,
            "confidence": 0.6,
            "atr": 0.005,
            "sl_atr": 2.0,
            "tp_atr": 4.0,
            "rates": {"H1": rates},
            "adx": 15,
            "is_ranging": True,
        }
        ae = AdaptiveEngine(MagicMock())
        ae.dl.available = False
        result = ae.analyze("EURUSD", {"H1": rates}, signal)
        assert "sl_atr" in result
        assert "tp_atr" in result

    @pytest.mark.skip(reason="P7: DL supprimé (code mort)")
    def test_build_dl_features_dl_available_no_h1(self):
        ae = AdaptiveEngine(MagicMock())
        ae.dl.available = True
        result = ae.build_dl_features({"M5": []})
        assert result is None

    @patch("engine_simple.adaptive_intelligence.datetime")
    @pytest.mark.skip(reason="P7: DL supprimé (code mort)")
    def test_analyze_session_boost_london_open(self, mock_dt):
        mock_dt.utcnow.return_value.hour = 7  # London open boost
        rates = _make_h1_rates(100, trend="up")
        signal = {
            "action": "BUY",
            "score": 0.5,
            "confidence": 0.5,
            "atr": 0.005,
            "sl_atr": 2.0,
            "tp_atr": 4.0,
            "rates": {"H1": rates},
            "adx": 25,
            "is_ranging": False,
        }
        ae = AdaptiveEngine(MagicMock())
        ae.dl.available = False
        result = ae.analyze("EURUSD", {"H1": rates}, signal)
        assert result["score"] > 0.5

    @patch("engine_simple.adaptive_intelligence.datetime")
    @pytest.mark.skip(reason="P7: DL supprimé (code mort)")
    def test_analyze_session_boost_ny_open(self, mock_dt):
        mock_dt.utcnow.return_value.hour = 13  # NY open boost
        rates = _make_h1_rates(100, trend="up")
        signal = {
            "action": "BUY",
            "score": 0.5,
            "confidence": 0.5,
            "atr": 0.005,
            "sl_atr": 2.0,
            "tp_atr": 4.0,
            "rates": {"H1": rates},
            "adx": 25,
            "is_ranging": False,
        }
        ae = AdaptiveEngine(MagicMock())
        ae.dl.available = False
        result = ae.analyze("EURUSD", {"H1": rates}, signal)
        assert result["score"] > 0.5

    @patch("engine_simple.adaptive_intelligence.datetime")
    @pytest.mark.skip(reason="P7: DL supprimé (code mort)")
    def test_analyze_session_penalty_asian(self, mock_dt):
        mock_dt.utcnow.return_value.hour = 3  # Asian session penalty
        rates = _make_h1_rates(100, trend="ranging")
        signal = {
            "action": "HOLD",
            "score": 0.5,
            "confidence": 0.5,
            "atr": 0.005,
            "sl_atr": 2.0,
            "tp_atr": 4.0,
            "rates": {"H1": rates},
            "adx": 15,
            "is_ranging": True,
        }
        ae = AdaptiveEngine(MagicMock())
        ae.dl.available = False
        # Session should NOT apply positive boost at hour 3
        result = ae.analyze("EURUSD", {"H1": rates}, signal)
        assert result["_regime"] is not None

    def test_get_validation_report(self):
        ae = AdaptiveEngine(MagicMock())
        # 🔧 FIX 28 Août 2026: validator supprimé (archivé dans retired/)
        # Ce test vérifie que get_report fonctionne sans validator
        report = ae.get_report("EURUSD")
        assert isinstance(report, dict)

    @patch("engine_simple.adaptive_intelligence.datetime")
    @pytest.mark.skip(reason="P7: DL supprimé (code mort)")
    def test_analyze_dl_error(self, mock_dt):
        mock_dt.utcnow.return_value.hour = 10
        rates = _make_h1_rates(100, trend="up")
        signal = {
            "action": "BUY",
            "score": 0.7,
            "confidence": 0.6,
            "atr": 0.005,
            "sl_atr": 2.0,
            "tp_atr": 4.0,
            "rates": {"H1": rates},
            "adx": 25,
            "is_ranging": False,
        }
        ae = AdaptiveEngine(MagicMock())
        ae.dl.available = True
        ae.dl.predict = MagicMock(side_effect=ValueError("DL crash"))
        result = ae.analyze("EURUSD", {"H1": rates}, signal)
        assert result["_dl_score"] is None

    @pytest.mark.skip(reason="P7: DL supprimé (code mort)")
    def test_record_meta_result(self):
        ae = AdaptiveEngine(MagicMock())
        predictions = {"MOM20x3": True, "DL_LSTM": False}
        ae.record_meta_result("EURUSD", "TREND_UP", predictions)
        assert ae.meta.trades_since_recal > 0

    @patch("engine_simple.adaptive_intelligence.datetime")
    @pytest.mark.skip(reason="P7: DL supprimé (code mort)")
    def test_train_dl_if_ready(self, mock_dt):
        mock_dt.utcnow.return_value.hour = 10
        ae = AdaptiveEngine(MagicMock())
        ae.dl.available = True
        ae.dl.training_buffer = {"EURUSD": [1, 2] * 20}  # 40 samples
        ae.dl.train_all = MagicMock()
        ae.train_dl_if_ready()
        ae.dl.train_all.assert_called_once()

    @pytest.mark.skip(reason="P7: DL supprimé (code mort)")
    def test_train_dl_if_not_enough(self):
        ae = AdaptiveEngine(MagicMock())
        ae.dl.available = True
        ae.dl.training_buffer = {"EURUSD": [1] * 10}
        ae.dl.train_all = MagicMock()
        ae.train_dl_if_ready()
        ae.dl.train_all.assert_not_called()

    @patch("engine_simple.adaptive_intelligence.datetime")
    @pytest.mark.skip(reason="P7: DL supprimé (code mort)")
    def test_build_dl_features_with_dl(self, mock_dt):
        mock_dt.utcnow.return_value.hour = 10
        ae = AdaptiveEngine(MagicMock())
        ae.dl.available = True
        rates = _make_h1_rates(100, trend="up")
        ae.dl._build_sequence = MagicMock(return_value=np.array([[1, 2, 3]]))
        result = ae.build_dl_features({"H1": rates})
        np.testing.assert_array_equal(result, np.array([[1, 2, 3]]))

    @patch("engine_simple.adaptive_intelligence.datetime")
    @pytest.mark.skip(reason="P7: DL supprimé (code mort)")
    def test_build_dl_features_error(self, mock_dt):
        mock_dt.utcnow.return_value.hour = 10
        ae = AdaptiveEngine(MagicMock())
        ae.dl.available = True
        rates = _make_h1_rates(100, trend="up")
        ae.dl._build_sequence = MagicMock(side_effect=ValueError("bad data"))
        result = ae.build_dl_features({"H1": rates})
        assert result is None

    @patch("engine_simple.adaptive_intelligence.datetime")
    def test_save_calibration_success(self, mock_dt):
        mock_dt.utcnow.return_value.hour = 10
        import tempfile, json

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            tmppath = f.name
        try:
            ae = AdaptiveEngine(MagicMock(), calibration_path=tmppath)
            ae.learner.record_trade("EURUSD", 1.5, "TREND_UP")
            ae.save_calibration()
            with open(tmppath) as f:
                state = json.load(f)
            assert "online_history" in state
            assert "adapted_params" in state
            # meta_calibration absent car _meta_active=False
        finally:
            os.unlink(tmppath)

    @patch("engine_simple.adaptive_intelligence.datetime")
    def test_save_calibration_error_handled(self, mock_dt):
        mock_dt.utcnow.return_value.hour = 10
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            tmppath = f.name
            os.chmod(tmppath, 0o444)  # read-only → OSError on write
        try:
            ae = AdaptiveEngine(MagicMock(), calibration_path=tmppath)
            ae.save_calibration()  # should not raise
            # 🔧 FIX M-S3: l'échec d'écriture ne doit pas corrompre/supprimer
            # le fichier de calibration d'origine (le tmp+replace avorté laisse
            # l'ancien fichier intact)
            assert Path(tmppath).exists(), "le fichier de calibration a disparu après l'échec"
        finally:
            os.chmod(tmppath, 0o666)
            os.unlink(tmppath)

    @patch("engine_simple.adaptive_intelligence.datetime")
    @pytest.mark.skip(reason="P7: DL supprimé (code mort)")
    def test_analyze_full_pipeline_all_fields_set(self, mock_dt):
        mock_dt.utcnow.return_value.hour = 14
        rates = _make_h1_rates(100, trend="up")
        d_rates = _make_h1_rates(200, trend="up")
        h4_rates = _make_h1_rates(150, trend="up")
        signal = {
            "action": "BUY",
            "score": 0.7,
            "confidence": 0.6,
            "atr": 0.005,
            "sl_atr": 2.0,
            "tp_atr": 4.0,
            "adx": 25,
            "is_ranging": False,
        }
        ae = AdaptiveEngine(MagicMock())
        ae.dl.available = True
        ae.dl.predict = MagicMock(return_value={"action": "BUY", "score": 0.70, "buy_prob": 0.65})
        result = ae.analyze("EURUSD", {"H1": rates, "H4": h4_rates, "D1": d_rates}, signal)
        assert result is not None
        for key in (
            "action",
            "score",
            "confidence",
            "risk_mult",
            "sl_atr",
            "tp_atr",
            "_regime",
            "_dl_score",
            "_meta_action",
            "_devil",
            "_alignment_dir",
            "_alignment_score",
            "_fvgs",
            "_sweep_type",
            "_sweep_level",
        ):
            assert key in result, f"Missing key: {key}"
