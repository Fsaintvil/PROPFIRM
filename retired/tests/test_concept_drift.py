"""Tests pour ConceptDriftDetector (Phase 7 ML Pipeline)"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["LOG_LEVEL"] = "CRITICAL"

import numpy as np

from engine_simple.concept_drift import ConceptDriftDetector, psi


class TestPSI:
    def test_identical_distributions(self):
        ref = np.array([1.0] * 50 + [2.0] * 50)
        cur = np.array([1.0] * 50 + [2.0] * 50)
        assert psi(ref, cur) < 0.01

    def test_different_distributions(self):
        ref = np.array([1.0] * 90 + [2.0] * 10)
        cur = np.array([1.0] * 10 + [2.0] * 90)
        assert psi(ref, cur) > 0.1

    def test_empty_arrays(self):
        assert psi(np.array([]), np.array([1, 2, 3])) == 0.0
        assert psi(np.array([1, 2, 3]), np.array([])) == 0.0

    def test_single_value(self):
        ref = np.array([1.0] * 100)
        cur = np.array([1.0] * 100)
        assert psi(ref, cur) < 0.01


class TestConceptDriftDetector:
    def test_init_defaults(self):
        d = ConceptDriftDetector()
        assert d.window_size == 100
        assert d.threshold_light == 0.10
        assert d.threshold_moderate == 0.20

    def test_set_reference(self):
        d = ConceptDriftDetector()
        d.set_reference({"feat1": np.array([0.1, 0.2, 0.3, 0.4, 0.5] * 20)})
        assert "feat1" in d._reference

    def test_add_sample(self):
        d = ConceptDriftDetector(window_size=50)
        for i in range(60):
            d.add_sample({"adx": float(i % 10), "score": 0.5})
        assert len(d._current["adx"]) == 50  # maxlen
        assert len(d._current["score"]) == 50

    def test_record_prediction(self):
        d = ConceptDriftDetector()
        for i in range(100):
            d.record_prediction("dl_lstm", correct=(i < 70), confidence=0.6)
        drift = d.model_drift("dl_lstm")
        assert 0.0 <= drift <= 1.0

    def test_drift_category(self):
        d = ConceptDriftDetector()
        assert d.drift_category(0.0) == "NONE"
        assert d.drift_category(0.15) == "LIGHT"
        assert d.drift_category(0.22) == "MODERATE"
        assert d.drift_category(0.30) == "SEVERE"

    def test_should_retrain_no_samples(self):
        d = ConceptDriftDetector()
        assert not d.should_retrain()

    def test_should_retrain_with_drift(self):
        d = ConceptDriftDetector(window_size=50, psi_threshold_moderate=0.05)
        d.set_reference({"feat": np.array([0.1] * 50 + [0.9] * 50)})
        for _ in range(30):
            d.add_sample({"feat": 0.9})
        for _ in range(30):
            d.record_prediction("test", correct=False, confidence=0.5)
        assert d.should_retrain(min_samples=10)

    def test_reset_current(self):
        d = ConceptDriftDetector()
        d.add_sample({"feat": 1.0})
        d.record_prediction("test", correct=True)
        d.reset_current()
        assert len(d._current) == 0
        assert len(d._model_accuracy) == 0

    def test_get_report(self):
        d = ConceptDriftDetector()
        d.set_reference({"feat1": np.array([0.1, 0.2, 0.3] * 30)})
        for _ in range(20):
            d.add_sample({"feat1": 0.15})
            d.record_prediction("m1", correct=True, confidence=0.7)
        report = d.get_report()
        assert "aggregate_drift" in report
        assert "drift_category" in report
        assert "per_feature_psi" in report
        assert "model_accuracy_drift" in report
        assert "should_retrain" in report

    def test_compute_per_feature_psi_no_ref(self):
        d = ConceptDriftDetector()
        d.add_sample({"feat": 1.0})
        assert d.compute_per_feature_psi() == {}

    def test_no_drift_identical_data(self):
        d = ConceptDriftDetector(window_size=100, psi_threshold_moderate=0.20)
        data = np.array([0.1, 0.2, 0.3, 0.4, 0.5] * 30)
        d.set_reference({"feat": data.copy()})
        for v in data[:50]:
            d.add_sample({"feat": v})
        assert d.aggregate_drift() < 0.10
