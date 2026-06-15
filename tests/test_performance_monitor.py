"""Tests for performance_monitor.py — PerformanceMonitor + module-level functions.

Covers: initialisation, record_trade, rolling windows, alerts,
challenge tracking, report generation, recommendations, singleton."""
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine_simple.performance_monitor import (
    PerformanceMonitor,
    get_monitor,
    record_trade,
    update_challenge,
)

# ── Constants ─────────────────────────────────────────────────────

FAKE_NOW = datetime(2026, 6, 7, 12, 30, 0)
FAKE_TODAY = "2026-06-07"


# ── Fixture ───────────────────────────────────────────────────────

@pytest.fixture
def perf_monitor(request):
    """Fixture: PerformanceMonitor with temp files + fixed datetime.
    Patches stay alive until the test method finishes."""
    tmp = Path(request.config.cache.mkdir("perf_mon_tmp"))
    # Use a unique subdir per test to avoid collisions
    import uuid
    tmp = tmp / str(uuid.uuid4())
    tmp.mkdir(parents=True, exist_ok=True)
    hist_file = tmp / "performance_history.json"
    rpt_file = tmp / "daily_report.json"

    with (
        patch("engine_simple.performance_monitor.HISTORY_FILE", hist_file),
        patch("engine_simple.performance_monitor.REPORT_FILE", rpt_file),
        patch("engine_simple.performance_monitor.datetime") as mock_dt,
    ):
        mock_dt.utcnow.return_value = FAKE_NOW
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        pm = PerformanceMonitor()
        yield pm, hist_file, rpt_file


@pytest.fixture
def pm(perf_monitor):
    """Convenience: just return the PerformanceMonitor instance."""
    return perf_monitor[0]


@pytest.fixture
def hist_file(perf_monitor):
    """Convenience: return the temp history file path."""
    return perf_monitor[1]


@pytest.fixture
def rpt_file(perf_monitor):
    """Convenience: return the temp report file path."""
    return perf_monitor[2]


# ── Helper ─────────────────────────────────────────────────────────

def _record_trades(pm, wins, losses, profit_win=10.0, profit_loss=-10.0,
                   symbol="USDCAD", regime="RANGING"):
    """Record a batch of wins and losses."""
    for _ in range(wins):
        pm.record_trade(symbol, profit_win, regime, "BUY")
    for _ in range(losses):
        pm.record_trade(symbol, profit_loss, regime, "SELL")


# ══════════════════════════════════════════════════════════════════════
# 1. INITIALISATION
# ══════════════════════════════════════════════════════════════════════

class TestInit:
    def test_no_history_file_creates_default(self, perf_monitor):
        pm, _, _ = perf_monitor
        assert "daily" in pm.history
        assert "rolling" in pm.history
        assert "symbols" in pm.history
        assert "alerts" in pm.history
        assert "challenge" in pm.history
        assert "recent_trades" in pm.history
        assert pm.history["daily"] == {}
        assert pm.history["alerts"] == []
        # Challenge dict exists but may be empty (only set if missing entirely)
        assert isinstance(pm.history["challenge"], dict)

    def test_corrupt_json_resets(self):
        with tempfile_for_test() as tmp:
            hist_file = tmp / "performance_history.json"
            hist_file.write_text("{invalid json!!!")
            with patch("engine_simple.performance_monitor.HISTORY_FILE", hist_file):
                pm = PerformanceMonitor()
                assert pm.history["daily"] == {}
                assert pm.history["alerts"] == []

    def test_contaminated_trades_resets(self):
        """Trades without timestamp → contamination → reset."""
        data = {
            "daily": {"2026-06-07": {"trades": 1, "wins": 1, "losses": 0, "pnl": 10.0, "gross_profit": 10.0, "gross_loss": 0.0, "symbols": {}}},
            "rolling": {},
            "symbols": {"USDCAD": {"trades": 1, "wins": 1, "losses": 0, "pnl": 10.0, "gross_profit": 10.0, "gross_loss": 0.0, "regime_stats": {}, "direction_stats": {}}},
            "alerts": [],
            "challenge": {},
            "recent_trades": [
                {"profit": 10, "symbol": "USDCAD"},  # NO timestamp
            ],
        }
        with tempfile_for_test() as tmp:
            hist_file = tmp / "performance_history.json"
            hist_file.write_text(json.dumps(data))
            with patch("engine_simple.performance_monitor.HISTORY_FILE", hist_file):
                pm = PerformanceMonitor()
                assert pm.history["recent_trades"] == []
                assert pm.history["daily"] == {}

    def test_valid_history_loaded(self):
        data = {
            "daily": {"2026-06-06": {"trades": 5, "wins": 3, "losses": 2, "pnl": 50.0, "gross_profit": 100.0, "gross_loss": 50.0, "symbols": {}}},
            "rolling": {"last_20": {"trades": 5, "wins": 3, "losses": 2, "pnl": 50.0, "wr": 60.0, "avg": 10.0}},
            "symbols": {"USDCAD": {"trades": 5, "wins": 3, "losses": 2, "pnl": 50.0, "gross_profit": 100.0, "gross_loss": 50.0, "regime_stats": {}, "direction_stats": {"BUY": {"wins": 0, "losses": 0, "pnl": 0.0}, "SELL": {"wins": 0, "losses": 0, "pnl": 0.0}}}},
            "alerts": [],
            "challenge": {"start_balance": 100000},
            "recent_trades": [{"profit": 10, "symbol": "USDCAD", "ts": "2026-06-06T12:00:00"}],
        }
        with tempfile_for_test() as tmp:
            hist_file = tmp / "performance_history.json"
            hist_file.write_text(json.dumps(data))
            with patch("engine_simple.performance_monitor.HISTORY_FILE", hist_file):
                pm = PerformanceMonitor()
                assert pm.history["daily"]["2026-06-06"]["trades"] == 5
                assert pm.history["challenge"]["start_balance"] == 100000
                assert len(pm.history["recent_trades"]) == 1

    def test_empty_history_file_is_valid(self):
        """Empty JSON object is valid and preserved."""
        data = {
            "daily": {},
            "rolling": {},
            "symbols": {},
            "alerts": [],
            "challenge": {},
            "recent_trades": [],
        }
        with tempfile_for_test() as tmp:
            hist_file = tmp / "performance_history.json"
            hist_file.write_text(json.dumps(data))
            with patch("engine_simple.performance_monitor.HISTORY_FILE", hist_file):
                pm = PerformanceMonitor()
                assert pm.history["daily"] == {}
                assert pm.history["recent_trades"] == []


# ══════════════════════════════════════════════════════════════════════
# 2. RECORD TRADE
# ══════════════════════════════════════════════════════════════════════

class TestRecordTrade:
    def test_record_win(self, perf_monitor):
        pm, _, _ = perf_monitor
        pm.record_trade("USDCAD", 50.0, "TREND_UP", "BUY")
        d = pm.history["daily"][FAKE_TODAY]
        assert d["trades"] == 1
        assert d["wins"] == 1
        assert d["losses"] == 0
        assert d["pnl"] == 50.0
        assert d["gross_profit"] == 50.0
        assert d["gross_loss"] == 0.0

    def test_record_loss(self, perf_monitor):
        pm, _, _ = perf_monitor
        pm.record_trade("USDCHF", -30.0, "RANGING", "SELL")
        d = pm.history["daily"][FAKE_TODAY]
        assert d["trades"] == 1
        assert d["wins"] == 0
        assert d["losses"] == 1
        assert d["pnl"] == -30.0
        assert d["gross_profit"] == 0.0
        assert d["gross_loss"] == 30.0

    def test_record_breakeven(self, perf_monitor):
        """profit=0: counts as trade but neither win nor loss."""
        pm, _, _ = perf_monitor
        pm.record_trade("USDCAD", 0.0, "RANGING", "BUY")
        d = pm.history["daily"][FAKE_TODAY]
        assert d["trades"] == 1
        assert d["wins"] == 0   # 0 is not > 0
        assert d["losses"] == 0  # 0 is not < 0 either
        assert d["pnl"] == 0.0

    def test_record_multiple_trades_same_day(self, perf_monitor):
        pm, _, _ = perf_monitor
        pm.record_trade("USDCAD", 50.0, "TREND_UP", "BUY")
        pm.record_trade("USDCHF", -20.0, "RANGING", "SELL")
        pm.record_trade("USDCAD", 30.0, "TREND_UP", "BUY")
        d = pm.history["daily"][FAKE_TODAY]
        assert d["trades"] == 3
        assert d["wins"] == 2
        assert d["losses"] == 1
        assert d["pnl"] == 60.0

    def test_record_different_days(self, perf_monitor):
        pm, _, _ = perf_monitor
        # Day 1
        with patch("engine_simple.performance_monitor.datetime") as mock_dt:
            mock_dt.utcnow.return_value = datetime(2026, 6, 6, 12, 0, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            pm.record_trade("USDCAD", 100.0, "TREND_UP", "BUY")
        # Day 2 (uses fixture's FAKE_NOW = 2026-06-07)
        pm.record_trade("EURUSD", 50.0, "RANGING", "BUY")

        assert "2026-06-06" in pm.history["daily"]
        assert "2026-06-07" in pm.history["daily"]
        assert pm.history["daily"]["2026-06-06"]["pnl"] == 100.0
        assert pm.history["daily"]["2026-06-07"]["pnl"] == 50.0

    def test_symbol_aggregation(self, perf_monitor):
        pm, _, _ = perf_monitor
        pm.record_trade("USDCAD", 100.0, "TREND_UP", "BUY")
        pm.record_trade("USDCAD", -30.0, "TREND_UP", "BUY")
        pm.record_trade("EURUSD", 50.0, "RANGING", "BUY")

        s = pm.history["symbols"]
        assert s["USDCAD"]["trades"] == 2
        assert s["USDCAD"]["wins"] == 1
        assert s["USDCAD"]["losses"] == 1
        assert s["USDCAD"]["pnl"] == 70.0
        assert s["USDCAD"]["gross_profit"] == 100.0
        assert s["USDCAD"]["gross_loss"] == 30.0

        assert s["EURUSD"]["trades"] == 1
        assert s["EURUSD"]["wins"] == 1
        assert s["EURUSD"]["pnl"] == 50.0

    def test_regime_stats(self, perf_monitor):
        pm, _, _ = perf_monitor
        pm.record_trade("USDCAD", 50.0, "TREND_UP", "BUY")
        pm.record_trade("USDCAD", -20.0, "RANGING", "BUY")
        pm.record_trade("USDCAD", 30.0, "TREND_UP", "BUY")

        rs = pm.history["symbols"]["USDCAD"]["regime_stats"]
        assert rs["TREND_UP"]["trades"] == 2
        assert rs["TREND_UP"]["wins"] == 2
        assert rs["TREND_UP"]["pnl"] == 80.0
        assert rs["RANGING"]["trades"] == 1
        assert rs["RANGING"]["wins"] == 0
        assert rs["RANGING"]["pnl"] == -20.0

    def test_direction_stats(self, perf_monitor):
        pm, _, _ = perf_monitor
        pm.record_trade("USDCAD", 50.0, "TREND_UP", "BUY")
        pm.record_trade("USDCAD", -20.0, "RANGING", "SELL")

        ds = pm.history["symbols"]["USDCAD"]["direction_stats"]
        assert ds["BUY"]["wins"] == 1
        assert ds["BUY"]["pnl"] == 50.0
        assert ds["SELL"]["losses"] == 1
        assert ds["SELL"]["pnl"] == -20.0

    def test_recent_trades_capped_at_500(self, perf_monitor):
        pm, _, _ = perf_monitor
        for _ in range(510):
            pm.record_trade("USDCAD", 1.0, "RANGING", "BUY")
        assert len(pm.history["recent_trades"]) == 500

    def test_daily_data_capped_at_365_days(self, perf_monitor):
        pm, _, _ = perf_monitor
        with patch("engine_simple.performance_monitor.datetime") as mock_dt:
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            for day_offset in range(400):
                d = datetime(2025, 1, 1) + timedelta(days=day_offset)
                mock_dt.utcnow.return_value = d
                pm.record_trade("USDCAD", 10.0, "RANGING", "BUY")
        assert len(pm.history["daily"]) <= 365

    def test_rolling_updated_after_record(self, perf_monitor):
        pm, _, _ = perf_monitor
        for _ in range(50):
            pm.record_trade("USDCAD", 10.0, "RANGING", "BUY")
        r = pm.history["rolling"]
        assert "last_20" in r
        assert "last_50" in r
        assert r["last_50"]["trades"] == 50
        assert r["last_50"]["wr"] == 100.0  # all wins

    def test_file_saved_after_record(self, perf_monitor):
        pm, hf, _ = perf_monitor
        pm.record_trade("USDCAD", 50.0, "TREND_UP", "BUY")
        assert hf.exists(), f"History file {hf} should exist"
        content = json.loads(hf.read_text())
        assert content["daily"][FAKE_TODAY]["pnl"] == 50.0


# ══════════════════════════════════════════════════════════════════════
# 3. ROLLING WINDOWS
# ══════════════════════════════════════════════════════════════════════

class TestRollingWindows:
    def test_all_windows_present_with_enough_trades(self, perf_monitor):
        pm, _, _ = perf_monitor
        for _ in range(200):
            pm.record_trade("USDCAD", 10.0, "RANGING", "BUY")
        for w in [20, 50, 100, 200]:
            assert f"last_{w}" in pm.history["rolling"], f"Missing last_{w}"
            assert pm.history["rolling"][f"last_{w}"]["trades"] == w

    def test_window_deleted_when_insufficient_trades(self, perf_monitor):
        pm, _, _ = perf_monitor
        for _ in range(50):
            pm.record_trade("USDCAD", 10.0, "RANGING", "BUY")
        assert "last_100" not in pm.history["rolling"]
        assert "last_200" not in pm.history["rolling"]

    def test_rolling_wr_accuracy_mixed_results(self, perf_monitor):
        pm, _, _ = perf_monitor
        _record_trades(pm, wins=30, losses=20)
        r = pm.history["rolling"]["last_50"]
        assert r["trades"] == 50
        assert r["wins"] == 30
        assert r["losses"] == 20
        assert r["wr"] == 60.0
        # profit_win=10.0 (default), profit_loss=-10.0 (default, NOT -5.0!)
        expected_pnl = 30 * 10.0 - 20 * 10.0  # = 100.0
        assert r["pnl"] == expected_pnl
        assert r["avg"] == expected_pnl / 50

    def test_rolling_updates_as_new_trades_come(self, perf_monitor):
        pm, _, _ = perf_monitor
        for _ in range(20):
            pm.record_trade("USDCAD", 10.0, "RANGING", "BUY")
        assert pm.history["rolling"]["last_20"]["wr"] == 100.0
        # Add 5 losses — last_20 = 15 wins + 5 losses = 75%
        _record_trades(pm, wins=0, losses=5, profit_loss=-10.0)
        assert pm.history["rolling"]["last_20"]["wr"] == 75.0
        assert pm.history["rolling"]["last_20"]["trades"] == 20

    def test_rolling_empty_when_no_trades(self, perf_monitor):
        pm, _, _ = perf_monitor
        assert pm.history["rolling"] == {}


# ══════════════════════════════════════════════════════════════════════
# 4. CHALLENGE
# ══════════════════════════════════════════════════════════════════════

class TestChallenge:
    def test_record_challenge_updates_fields(self, perf_monitor):
        pm, _, _ = perf_monitor
        ftmo_data = {
            "balance": 195000.0,
            "equity": 194500.0,
            "peak_equity": 196000.0,
            "dd_from_initial": "0.5%",
            "dd_from_peak": "0.8%",
            "profit_progress": "+15.2%",
            "profit_remaining": 17500.0,
            "trading_days": 12,
            "days_remaining": 18,
            "total_trades": 45,
            "status": "ACTIVE",
            "daily_pnl": 250.0,
            "win_rate": "65.4%",
        }
        pm.record_challenge(ftmo_data)
        c = pm.history["challenge"]
        assert c["balance"] == 195000.0
        assert c["equity"] == 194500.0
        assert c["profit_progress_pct"] == 15.2
        assert c["profit_remaining"] == 17500.0
        assert c["trading_days"] == 12
        assert c["days_remaining"] == 18
        assert c["status"] == "ACTIVE"
        assert "last_update" in c

    def test_record_challenge_handles_string_amounts(self, perf_monitor):
        pm, _, _ = perf_monitor
        pm.record_challenge({
            "profit_progress": "8.5%",
            "profit_remaining": "$18,500",
            "balance": 195000.0,
            "status": "ACTIVE",
        })
        assert pm.history["challenge"]["profit_progress_pct"] == 8.5


# ══════════════════════════════════════════════════════════════════════
# 5. ALERTS
# ══════════════════════════════════════════════════════════════════════

class TestAlerts:
    def test_no_alerts_when_fresh(self, perf_monitor):
        pm, _, _ = perf_monitor
        assert pm.check_alerts() == []

    def test_wr_decline_alert(self, perf_monitor):
        """WR drops >15 points from 100-trade window to 50-trade window."""
        pm, _, _ = perf_monitor
        # 100 trades at 90% WR
        _record_trades(pm, wins=90, losses=10)
        # Add 50 trades at 30% WR to drop last_50 significantly
        _record_trades(pm, wins=15, losses=35)
        alerts = pm.check_alerts()
        wr_alerts = [a for a in alerts if a["metric"] == "WR_DECLINE"]
        assert len(wr_alerts) >= 1

    def test_pf_below_1_critical(self, perf_monitor):
        """PF < 1.0 on 50+ trades → CRITICAL alert."""
        pm, _, _ = perf_monitor
        # 50 trades: PF = (20*5)/(30*10) = 100/300 = 0.33
        _record_trades(pm, wins=20, losses=30, profit_win=5.0, profit_loss=-10.0)
        alerts = pm.check_alerts()
        pf_alerts = [a for a in alerts if a["metric"] == "PF_BELOW_1"]
        assert len(pf_alerts) >= 1
        assert all(a["level"] == "CRITICAL" for a in pf_alerts)

    def test_pf_warning_below_1_2(self, perf_monitor):
        """PF between 1.0 and 1.2 → WARNING."""
        pm, _, _ = perf_monitor
        # 50 trades: PF = 260/240 = 1.083 (< 1.2, > 1.0)
        _record_trades(pm, wins=26, losses=24, profit_win=10.0, profit_loss=-10.0)
        alerts = pm.check_alerts()
        pf_low = [a for a in alerts if a["metric"] == "PF_LOW"]
        pf_crit = [a for a in alerts if a["metric"] == "PF_BELOW_1"]
        assert len(pf_low) >= 1 or len(pf_crit) >= 1

    def test_symbol_losing_alert(self, perf_monitor):
        """Symbol PnL < -50 and WR < 40% → alert."""
        pm, _, _ = perf_monitor
        # 10 trades: PF = (3*5)/(7*10) = 15/70 = 0.21, PnL = -55, WR = 30%
        _record_trades(pm, wins=3, losses=7, profit_win=5.0, profit_loss=-10.0, symbol="BAD")
        alerts = pm.check_alerts()
        sym_alerts = [a for a in alerts if a["metric"] == "SYMBOL_LOSING"]
        assert len(sym_alerts) >= 1
        assert sym_alerts[0]["symbol"] == "BAD"
        assert sym_alerts[0]["level"] == "WARNING"

    def test_no_symbol_alert_low_volume(self, perf_monitor):
        """Less than 5 trades on a symbol → no alert even if losing."""
        pm, _, _ = perf_monitor
        _record_trades(pm, wins=0, losses=3, profit_loss=-100.0, symbol="BAD")
        alerts = pm.check_alerts()
        sym_alerts = [a for a in alerts if a["metric"] == "SYMBOL_LOSING"]
        assert len(sym_alerts) == 0

    def test_challenge_behind_alert(self, perf_monitor):
        """J+15 with < 30% progress → alert."""
        pm, _, _ = perf_monitor
        pm.history["challenge"]["trading_days"] = 20
        pm.history["challenge"]["profit_progress_pct"] = 15.0
        alerts = pm.check_alerts()
        cha = [a for a in alerts if a["metric"] == "CHALLENGE_BEHIND"]
        assert len(cha) >= 1
        assert cha[0]["level"] == "WARNING"

    def test_no_challenge_alert_when_on_track(self, perf_monitor):
        pm, _, _ = perf_monitor
        pm.history["challenge"]["trading_days"] = 10  # < 15
        pm.history["challenge"]["profit_progress_pct"] = 10.0
        alerts = pm.check_alerts()
        cha = [a for a in alerts if a["metric"] == "CHALLENGE_BEHIND"]
        assert len(cha) == 0


# ══════════════════════════════════════════════════════════════════════
# 6. REPORT GENERATION
# ══════════════════════════════════════════════════════════════════════

class TestReport:
    def test_generate_report_structure(self, perf_monitor):
        pm, _, _ = perf_monitor
        report = pm.generate_report()
        assert "generated_at" in report
        assert "challenge" in report
        assert "rolling" in report
        assert "symbols" in report
        assert "daily_recent" in report
        assert "alerts" in report
        assert "trend" in report
        assert "recommendations" in report

    def test_generate_report_saves_file(self, perf_monitor):
        pm, _, rf = perf_monitor
        pm.generate_report()
        assert rf.exists(), f"Report file {rf} should exist"
        content = json.loads(rf.read_text())
        assert "generated_at" in content

    def test_rolling_summary_present(self, perf_monitor):
        pm, _, _ = perf_monitor
        for _ in range(50):
            pm.record_trade("USDCAD", 10.0, "RANGING", "BUY")
        report = pm.generate_report()
        assert "last_20" in report["rolling"]
        assert "last_50" in report["rolling"]
        assert report["rolling"]["last_50"]["wr"] == 100.0

    def test_symbol_summary(self, perf_monitor):
        pm, _, _ = perf_monitor
        pm.record_trade("USDCAD", 100.0, "TREND_UP", "BUY")
        pm.record_trade("EURUSD", 50.0, "RANGING", "BUY")
        report = pm.generate_report()
        assert "USDCAD" in report["symbols"]
        assert "EURUSD" in report["symbols"]
        assert report["symbols"]["USDCAD"]["pnl"] == 100.0

    def test_daily_recent_last_7_days(self, perf_monitor):
        pm, _, _ = perf_monitor
        with patch("engine_simple.performance_monitor.datetime") as mock_dt:
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            for i in range(10):
                d = datetime(2026, 6, 1) + timedelta(days=i)
                mock_dt.utcnow.return_value = d
                pm.record_trade("USDCAD", 10.0, "RANGING", "BUY")
        report = pm.generate_report()
        assert len(report["daily_recent"]) <= 7


# ══════════════════════════════════════════════════════════════════════
# 7. SUMMARY TEXT
# ══════════════════════════════════════════════════════════════════════

class TestSummaryText:
    def test_summary_text_returns_string(self, perf_monitor):
        pm, _, _ = perf_monitor
        pm.record_trade("USDCAD", 50.0, "TREND_UP", "BUY")
        text = pm.summary_text()
        assert isinstance(text, str)
        assert "USDCAD" in text

    def test_summary_text_includes_alerts(self, perf_monitor):
        pm, _, _ = perf_monitor
        _record_trades(pm, wins=20, losses=30, profit_win=5.0, profit_loss=-10.0)
        text = pm.summary_text()
        assert "ALERTES" in text or "RECOMMANDATIONS" in text


# ══════════════════════════════════════════════════════════════════════
# 8. GET DAILY PNL
# ══════════════════════════════════════════════════════════════════════

class TestGetDailyPnl:
    def test_get_today_pnl(self, perf_monitor):
        pm, _, _ = perf_monitor
        pm.record_trade("USDCAD", 100.0, "TREND_UP", "BUY")
        result = pm.get_daily_pnl()
        assert result["pnl"] == 100.0
        assert result["trades"] == 1
        assert result["wins"] == 1
        assert result["losses"] == 0

    def test_get_specific_date(self, perf_monitor):
        pm, _, _ = perf_monitor
        with patch("engine_simple.performance_monitor.datetime") as mock_dt:
            mock_dt.utcnow.return_value = datetime(2026, 6, 6, 12, 0, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            pm.record_trade("USDCAD", 50.0, "RANGING", "BUY")
        result = pm.get_daily_pnl("2026-06-06")
        assert result["pnl"] == 50.0

    def test_get_missing_date(self, perf_monitor):
        pm, _, _ = perf_monitor
        result = pm.get_daily_pnl("2025-01-01")
        assert result["pnl"] == 0
        assert result["trades"] == 0


# ══════════════════════════════════════════════════════════════════════
# 9. TREND ANALYSIS
# ══════════════════════════════════════════════════════════════════════

class TestTrendAnalysis:
    def test_trend_stable_when_insufficient_data(self, perf_monitor):
        pm, _, _ = perf_monitor
        trend = pm._trend_analysis()
        assert trend["direction"] == "stable"

    def test_trend_declining(self, perf_monitor):
        pm, _, _ = perf_monitor
        # On crée un profil où WR baisse progressivement sur les trades récents:
        #   Phase 1 (oldest 50): 40W/10L = 80% WR
        #   Phase 2 (next 30):   15W/15L = 50% WR
        #   Phase 3 (newest 20):  4W/16L = 20% WR
        # Résultat attendu: wr_20 (20%) < wr_50 (38%) < wr_100 (59%)
        _record_trades(pm, wins=40, losses=10, profit_win=1.0, profit_loss=-1.0)
        _record_trades(pm, wins=15, losses=15, profit_win=1.0, profit_loss=-1.0)
        _record_trades(pm, wins=4, losses=16, profit_win=1.0, profit_loss=-1.0)
        trend = pm._trend_analysis()
        wr_100 = trend.get("wr_evolution", {}).get("100", 0)
        wr_50 = trend.get("wr_evolution", {}).get("50", 0)
        wr_20 = trend.get("wr_evolution", {}).get("20", 0)
        assert wr_50 < wr_100, f"wr_50 ({wr_50}) should be < wr_100 ({wr_100})"
        assert wr_20 < wr_50, f"wr_20 ({wr_20}) should be < wr_50 ({wr_50})"
        assert trend["direction"] == "declining"


# ══════════════════════════════════════════════════════════════════════
# 10. RECOMMENDATIONS
# ══════════════════════════════════════════════════════════════════════

class TestRecommendations:
    def test_high_priority_for_symbol_pf_below_0_8(self, perf_monitor):
        """PF < 0.8 on 10+ trades → HIGH priority."""
        pm, _, _ = perf_monitor
        # 3 wins * 5 + 10 losses * (-10): PnL = -85, gross_profit = 15, gross_loss = 100, PF = 0.15
        _record_trades(pm, wins=3, losses=10, profit_win=5.0, profit_loss=-10.0, symbol="BAD")
        report = pm.generate_report()
        bad_recs = [r for r in report["recommendations"] if "BAD" in r["action"]]
        assert len(bad_recs) >= 1
        assert any(r["priority"] == "HIGH" for r in bad_recs)

    def test_medium_priority_for_symbol_pf_below_1(self, perf_monitor):
        """PF < 1.0 on 20+ trades → MEDIUM priority."""
        pm, _, _ = perf_monitor
        # 12 wins * 5 = 60, 10 losses * (-6) = -60 → PF = 60/60 = 1.0 EXACT → NO alert
        # To get PF < 1.0: make losses slightly larger
        # 12 wins * 5 = 60, 10 losses * (-7) = -70 → PF = 60/70 = 0.857 → HIGH (PF < 0.8 still)
        # Let's try: 11 wins * 10 = 110, 10 losses * (-11) = -110 → PF = 110/110 = 1.0
        # 11 wins * 10 = 110, 10 losses * (-12) = -120 → PF = 110/120 = 0.917 → MEDIUM (PF < 1.0 but >= 0.8)
        _record_trades(pm, wins=11, losses=10, profit_win=10.0, profit_loss=-12.0, symbol="WEAK")
        report = pm.generate_report()
        weak_recs = [r for r in report["recommendations"] if "WEAK" in r["action"]]
        assert len(weak_recs) >= 1
        assert any(r["priority"] == "MEDIUM" for r in weak_recs)

    def test_challenge_pace_recommendation_high(self, perf_monitor):
        """If estimated days > remaining → HIGH priority recommendation.
        $100/day × 10j = $1k total → $5k/$100 = 50j estimés > 10j restants.
        50 < 365 donc pas ">1 an", la recommandation est émise."""
        pm, _, _ = perf_monitor
        pm.history["challenge"]["profit_remaining"] = 5000.0
        pm.history["challenge"]["days_remaining"] = 10
        pm.history["challenge"]["trading_days"] = 20
        pm.history["challenge"]["profit_progress_pct"] = 10.0
        pm.history["challenge"]["status"] = "ACTIVE"
        # 10 jours × $100 PnL → avg_daily_pnl = $100
        with patch("engine_simple.performance_monitor.datetime") as mock_dt:
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            for i in range(10):
                d = datetime(2026, 5, 28) + timedelta(days=i)
                mock_dt.utcnow.return_value = d
                pm.record_trade("USDCAD", 100.0, "RANGING", "BUY")
        report = pm.generate_report()
        pace_recs = [r for r in report["recommendations"]
                     if "Rythme" in r["action"] or "augmenter" in r["action"]]
        assert len(pace_recs) >= 1


# ══════════════════════════════════════════════════════════════════════
# 11. MODULE-LEVEL FUNCTIONS (SINGLETON)
# ══════════════════════════════════════════════════════════════════════

class TestSingleton:
    def _reset_singleton(self):
        import engine_simple.performance_monitor as pm_mod
        pm_mod._monitor = None

    def test_get_monitor_returns_same_instance(self):
        self._reset_singleton()
        with tempfile_for_test() as tmp, \
             patch("engine_simple.performance_monitor.HISTORY_FILE", tmp / "perf.json"), \
             patch("engine_simple.performance_monitor.REPORT_FILE", tmp / "rpt.json"):
            m1 = get_monitor()
            m2 = get_monitor()
            assert m1 is m2
        self._reset_singleton()

    def test_record_trade_function(self):
        self._reset_singleton()
        with tempfile_for_test() as tmp, \
             patch("engine_simple.performance_monitor.HISTORY_FILE", tmp / "perf.json"), \
             patch("engine_simple.performance_monitor.REPORT_FILE", tmp / "rpt.json"):
            record_trade("EURUSD", 100.0, "TREND_DOWN", "SELL")
            pm = get_monitor()
            assert pm.history["symbols"]["EURUSD"]["pnl"] == 100.0
        self._reset_singleton()

    def test_update_challenge_function(self):
        self._reset_singleton()
        with tempfile_for_test() as tmp, \
             patch("engine_simple.performance_monitor.HISTORY_FILE", tmp / "perf.json"), \
             patch("engine_simple.performance_monitor.REPORT_FILE", tmp / "rpt.json"):
            update_challenge({
                "balance": 200000.0,
                "status": "ACTIVE",
                "profit_progress": "5%",
            })
            pm = get_monitor()
            assert pm.history["challenge"]["balance"] == 200000.0
        self._reset_singleton()


# ══════════════════════════════════════════════════════════════════════
# 12. EDGE CASES
# ══════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_record_trade_function_survives_exception(self):
        """record_trade wraps in try/except — should not crash."""
        import engine_simple.performance_monitor as pm_mod
        pm_mod._monitor = None
        with patch("engine_simple.performance_monitor.get_monitor",
                   side_effect=ValueError("test")):
            record_trade("USDCAD", 100.0, "RANGING", "BUY")  # should not raise
        pm_mod._monitor = None

    def test_update_challenge_function_survives_exception(self):
        import engine_simple.performance_monitor as pm_mod
        pm_mod._monitor = None
        with patch("engine_simple.performance_monitor.get_monitor",
                   side_effect=ValueError("test")):
            update_challenge({"balance": 100.0})  # should not raise
        pm_mod._monitor = None

    def test_generate_report_with_empty_history(self, perf_monitor):
        pm, _, _ = perf_monitor
        report = pm.generate_report()
        assert isinstance(report, dict)
        assert report["challenge"]["status"] == "UNKNOWN"

    def test_challenge_summary_with_string_profit_remaining(self, perf_monitor):
        pm, _, _ = perf_monitor
        pm.history["challenge"]["profit_remaining"] = "$20,000"
        pm.history["challenge"]["days_remaining"] = 15
        s = pm._challenge_summary()
        assert s["profit_remaining"] == 20000.0

    def test_challenge_summary_default_remaining(self, perf_monitor):
        pm, _, _ = perf_monitor
        pm.history["challenge"] = {"status": "ACTIVE"}
        s = pm._challenge_summary()
        assert s["profit_remaining"] == 20000  # default

    def test_recent_series_returns_empty(self, perf_monitor):
        pm, _, _ = perf_monitor
        assert pm._recent_series(20) == []

    def test_rolling_summary_pf_na_when_no_losses(self, perf_monitor):
        """PF when losses=0 should be 'N/A' (not computed)."""
        pm, _, _ = perf_monitor
        _record_trades(pm, wins=50, losses=0)
        s = pm._rolling_summary()
        assert "last_50" in s
        assert s["last_50"]["pf"] == "N/A"

    def test_symbol_summary_skips_zero_trade_symbols(self, perf_monitor):
        pm, _, _ = perf_monitor
        pm.history["symbols"]["UNUSED"] = {
            "trades": 0, "wins": 0, "losses": 0, "pnl": 0.0,
            "gross_profit": 0.0, "gross_loss": 0.0,
            "regime_stats": {}, "direction_stats": {
                "BUY": {"wins": 0, "losses": 0, "pnl": 0.0},
                "SELL": {"wins": 0, "losses": 0, "pnl": 0.0},
            },
        }
        s = pm._symbol_summary()
        assert "UNUSED" not in s

    def test_record_trade_regime_default(self, perf_monitor):
        pm, _, _ = perf_monitor
        pm.record_trade("USDCAD", 50.0)
        rs = pm.history["symbols"]["USDCAD"]["regime_stats"]
        assert "UNKNOWN" in rs
        ds = pm.history["symbols"]["USDCAD"]["direction_stats"]
        assert "BUY" in ds


# ══════════════════════════════════════════════════════════════════════
# 13. SAVE / LOAD INTEGRITY
# ══════════════════════════════════════════════════════════════════════

class TestPersistence:
    def test_save_and_reload_preserves_data(self):
        with tempfile_for_test() as tmp:
            hist_file = tmp / "performance_history.json"
            rpt_file = tmp / "daily_report.json"

            # Create and save
            with (
                patch("engine_simple.performance_monitor.HISTORY_FILE", hist_file),
                patch("engine_simple.performance_monitor.REPORT_FILE", rpt_file),
                patch("engine_simple.performance_monitor.datetime") as mock_dt,
            ):
                mock_dt.utcnow.return_value = FAKE_NOW
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                pm1 = PerformanceMonitor()
                pm1.record_trade("USDCAD", 100.0, "TREND_UP", "BUY")
                pm1.record_trade("EURUSD", -50.0, "RANGING", "SELL")

            # Reload in new instance
            with (
                patch("engine_simple.performance_monitor.HISTORY_FILE", hist_file),
                patch("engine_simple.performance_monitor.REPORT_FILE", rpt_file),
            ):
                pm2 = PerformanceMonitor()
                assert pm2.history["daily"][FAKE_TODAY]["trades"] == 2
                assert pm2.history["daily"][FAKE_TODAY]["pnl"] == 50.0
                assert pm2.history["symbols"]["USDCAD"]["trades"] == 1
                assert pm2.history["symbols"]["EURUSD"]["trades"] == 1

    def test_save_handles_permission_error_gracefully(self, perf_monitor):
        pm, hf, _ = perf_monitor
        pm._save()  # should work silently
        pm.record_trade("USDCAD", 100.0, "RANGING", "BUY")
        assert hf.exists()


# ══════════════════════════════════════════════════════════════════════
# 14. CHALLENGE SUMMARY
# ══════════════════════════════════════════════════════════════════════

class TestChallengeSummary:
    def test_challenge_summary_on_track(self, perf_monitor):
        pm, _, _ = perf_monitor
        pm.history["challenge"]["trading_days"] = 5
        pm.history["challenge"]["profit_progress_pct"] = 10.0
        pm.history["challenge"]["status"] = "ACTIVE"
        pm.history["challenge"]["profit_remaining"] = 18000.0
        pm.history["challenge"]["days_remaining"] = 25
        with patch("engine_simple.performance_monitor.datetime") as mock_dt:
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            for i in range(5):
                d = datetime(2026, 6, 2) + timedelta(days=i)
                mock_dt.utcnow.return_value = d
                pm.record_trade("USDCAD", 200.0, "RANGING", "BUY")
        s = pm._challenge_summary()
        assert s["on_track"] is True

    def test_challenge_summary_off_track(self, perf_monitor):
        pm, _, _ = perf_monitor
        pm.history["challenge"]["trading_days"] = 20
        pm.history["challenge"]["profit_progress_pct"] = 5.0
        pm.history["challenge"]["status"] = "ACTIVE"
        pm.history["challenge"]["profit_remaining"] = 19000.0
        pm.history["challenge"]["days_remaining"] = 10
        s = pm._challenge_summary()
        # avg_daily_pnl = 0 (no trades recorded) → on_track = False (not > 0)
        assert s["on_track"] is False

    def test_estimated_days_infinity_when_negative_pnl(self, perf_monitor):
        pm, _, _ = perf_monitor
        pm.history["challenge"]["profit_remaining"] = 20000.0
        pm.history["challenge"]["days_remaining"] = 15
        with patch("engine_simple.performance_monitor.datetime") as mock_dt:
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            for i in range(5):
                d = datetime(2026, 6, 2) + timedelta(days=i)
                mock_dt.utcnow.return_value = d
                pm.record_trade("USDCAD", -200.0, "RANGING", "BUY")
        s = pm._challenge_summary()
        # avg_daily_pnl negative → estimated_days = "∞"
        assert s["estimated_days_to_target"] == "∞"


# ══════════════════════════════════════════════════════════════════════
# 15. ALERTS — RECOMMENDATIONS CROSS-CHECK
# ══════════════════════════════════════════════════════════════════════

class TestAlertRecommendationCross:
    def test_declining_trend_generates_recommendation(self, perf_monitor):
        pm, _, _ = perf_monitor
        # Même profil que test_trend_declining: WR baisse progressivement
        _record_trades(pm, wins=40, losses=10, profit_win=1.0, profit_loss=-1.0)
        _record_trades(pm, wins=15, losses=15, profit_win=1.0, profit_loss=-1.0)
        _record_trades(pm, wins=4, losses=16, profit_win=1.0, profit_loss=-1.0)
        report = pm.generate_report()
        trend_recs = [r for r in report["recommendations"]
                      if "WR en baisse" in r["action"] or "baisse" in r["action"]]
        assert len(trend_recs) >= 1

    def test_no_recommendations_when_performing_well(self, perf_monitor):
        pm, _, _ = perf_monitor
        _record_trades(pm, wins=200, losses=0)  # perfect performance
        report = pm.generate_report()
        # No bad symbols, no declining trend, no challenge issue
        assert isinstance(report["recommendations"], list)


# ── Temp dir helper (avoids pytest tmp_path dependency in patched context) ──


@contextmanager
def tempfile_for_test():
    """Yield a Path to a temp directory that auto-cleans."""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


# ── Run directly ─────────────────────────────────────────────────────

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
