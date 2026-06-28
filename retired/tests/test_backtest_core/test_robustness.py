"""Tests for robustness modules: WalkForward, MonteCarlo, StressTester."""

import numpy as np
import pandas as pd
import pytest

from engine_simple.backtest_core.robustness import (
    WalkForwardAnalyzer,
    MonteCarloSimulator,
    StressTester,
)
from engine_simple.backtest_core.robustness.walk_forward import WFResult, WFFoldResult
from engine_simple.backtest_core.robustness.monte_carlo import MCResult
from engine_simple.backtest_core.robustness.stress_tests import (
    StressTestReport,
    StressResult,
    StressScenario,
)


class TestWalkForwardAnalyzer:
    def test_walk_forward_init_defaults(self):
        wf = WalkForwardAnalyzer()
        assert wf.n_splits == 5
        assert wf.train_pct == 0.60
        assert wf.purging_bars == 50
        assert wf.embargo_bars == 20
        assert wf.verbose is False

    def test_walk_forward_init_custom(self):
        wf = WalkForwardAnalyzer(n_splits=3, train_pct=0.7, purging_bars=30, embargo_bars=10)
        assert wf.n_splits == 3
        assert wf.train_pct == 0.7
        assert wf.purging_bars == 30
        assert wf.embargo_bars == 10

    def test_walk_forward_invalid_n_splits(self):
        with pytest.raises(ValueError):
            WalkForwardAnalyzer(n_splits=1)

    def test_walk_forward_invalid_train_pct(self):
        with pytest.raises(ValueError):
            WalkForwardAnalyzer(train_pct=0.9)

    def test_walk_forward_invalid_purging(self):
        with pytest.raises(ValueError):
            WalkForwardAnalyzer(purging_bars=-1)

    def test_compute_fold_borders(self):
        wf = WalkForwardAnalyzer(n_splits=5, train_pct=0.6)
        borders = wf._compute_fold_borders(1000)
        assert len(borders) == 5
        assert all(b > 0 for b in borders)
        assert borders[-1] <= 600

    def test_compute_fold_borders_single(self):
        wf = WalkForwardAnalyzer(n_splits=5, train_pct=0.6)
        borders = wf._compute_fold_borders(200)
        assert len(borders) == 5

    def test_wfresult_fields(self):
        r = WFResult(n_splits=5)
        assert r.n_splits == 5
        assert r.folds == []
        assert r.is_robust is False
        assert r.overfitting_detected is False

    def test_wffoldresult_fields(self):
        f = WFFoldResult(fold=1, train_start=None, train_end=None, test_start=None, test_end=None)
        assert f.fold == 1
        assert f.train_trades == 0


class TestMonteCarloSimulator:
    def test_monte_carlo_init_defaults(self):
        mc = MonteCarloSimulator()
        assert mc.n_simulations == 1000
        assert mc.method == "bootstrap"
        assert mc.verbose is False

    def test_monte_carlo_init_custom(self):
        mc = MonteCarloSimulator(n_simulations=500, method="reshuffle", seed=42)
        assert mc.n_simulations == 500
        assert mc.method == "reshuffle"

    def test_monte_carlo_invalid_n_simulations(self):
        with pytest.raises(ValueError):
            MonteCarloSimulator(n_simulations=50)

    def test_monte_carlo_invalid_method(self):
        with pytest.raises(ValueError):
            MonteCarloSimulator(method="invalid")

    def test_monte_carlo_init_verbose(self):
        mc = MonteCarloSimulator(verbose=True)
        assert mc.verbose is True

    def test_mcresult_fields(self):
        r = MCResult(n_simulations=100, method="bootstrap", n_trades_input=50)
        assert r.n_simulations == 100
        assert r.method == "bootstrap"
        assert r.n_trades_input == 50
        assert r.mean_pnl == 0.0

    def test_compute_pf_positive(self):
        pnls = np.array([10.0, -5.0, 15.0, -3.0])
        pf = MonteCarloSimulator._compute_pf(pnls)
        assert pf == pytest.approx(25.0 / 8.0, abs=0.01)

    def test_compute_pf_all_positive(self):
        pnls = np.array([10.0, 5.0])
        pf = MonteCarloSimulator._compute_pf(pnls)
        assert pf == float("inf")

    def test_compute_pf_all_negative(self):
        pnls = np.array([-10.0, -5.0])
        pf = MonteCarloSimulator._compute_pf(pnls)
        assert pf == 0.0

    def test_simulate_equity(self):
        trades = np.array([10.0, -5.0, 15.0])
        equity = MonteCarloSimulator._simulate_equity(trades, 100.0)
        expected = np.array([100.0, 110.0, 105.0, 120.0])
        assert np.allclose(equity, expected)

    def test_compute_max_dd(self):
        equity = np.array([100.0, 110.0, 105.0, 95.0, 100.0])
        dd = MonteCarloSimulator._compute_max_dd(equity)
        # peak = 110, max dd at 95: (110-95)/110*100 = 13.636
        assert dd == pytest.approx(13.636, abs=0.01)

    def test_compute_max_dd_no_drawdown(self):
        equity = np.array([100.0, 110.0, 120.0])
        dd = MonteCarloSimulator._compute_max_dd(equity)
        assert dd == 0.0

    def test_compute_skewness(self):
        data = np.random.randn(100) * 10 + 50
        sk = MonteCarloSimulator._compute_skewness(data)
        assert isinstance(sk, float)

    def test_compute_kurtosis(self):
        data = np.random.randn(100) * 10 + 50
        kt = MonteCarloSimulator._compute_kurtosis(data)
        assert isinstance(kt, float)


class TestStressTester:
    def test_stress_tester_init_defaults(self):
        st = StressTester()
        assert len(st.scenarios) == 4
        assert "CRASH-2008" in st.scenarios
        assert "COVID-2020" in st.scenarios

    def test_stress_tester_init_subset(self):
        st = StressTester(scenarios=["CRASH-2008", "SNB-2015"])
        assert len(st.scenarios) == 2
        assert "COVID-2020" not in st.scenarios

    def test_stress_tester_init_verbose(self):
        st = StressTester(verbose=True)
        assert st.verbose is True

    def test_stress_scenario_defaults(self):
        sc = StressScenario(name="TEST", description="Test scenario")
        assert sc.spread_mult == 1.0
        assert sc.requote_prob == 0.02
        assert sc.vol_mult == 1.0

    def test_stress_result_defaults(self):
        sr = StressResult(scenario="TEST", description="test")
        assert sr.passed is True
        assert sr.severity == "none"

    def test_stress_test_report_defaults(self):
        r = StressTestReport()
        assert r.symbol == ""
        assert r.scenarios == []
        assert r.overall_verdict == ""

    def test_compare_scenario_detects_pnl_loss(self):
        class MockResult:
            def __init__(self, metrics, total_trades):
                self.metrics = metrics
                self.total_trades = total_trades

        normal = MockResult(
            {"win_rate": 60, "net_profit": 1000, "profit_factor": 1.5, "max_dd_pct": 5, "sharpe_ratio": 1.0}, 50
        )
        stress = MockResult(
            {"win_rate": 40, "net_profit": -200, "profit_factor": 0.8, "max_dd_pct": 18, "sharpe_ratio": -0.5}, 30
        )

        scenario = StressScenario(name="TEST", description="test")
        result = StressTester._compare_scenario("TEST", scenario, normal, stress)
        assert result.passed is False
        assert result.severity in ("high", "critical")
        assert result.wr_change < 0
        assert result.pnl_change < 0

    def test_build_stress_config(self):
        from engine_simple.backtest_core.engine import BacktestConfig

        base = BacktestConfig(latency_ms=100, requote_prob=0.02)
        scenario = StressScenario(
            name="TEST",
            description="",
            vol_mult=3.0,
            requote_prob=0.3,
            spread_mult=5.0,
            slippage_mean_mult=2.0,
            slippage_std_mult=2.0,
        )
        st = StressTester()
        stress_cfg = st._build_stress_config(base, scenario)
        assert stress_cfg.latency_ms == min(100 * 3.0, 5000)
        assert stress_cfg.requote_prob == 0.3

    def test_compute_verdict_all_pass(self):
        report = StressTestReport(n_passed=4, n_failed=0)
        verdict = StressTester._compute_verdict(report)
        assert "ROBUSTE" in verdict

    def test_compute_verdict_all_fail(self):
        report = StressTestReport(n_passed=0, n_failed=4)
        verdict = StressTester._compute_verdict(report)
        assert "FRAGILE" in verdict
