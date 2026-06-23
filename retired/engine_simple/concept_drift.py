"""Concept drift detection — PSI-based gradual scoring + auto-reweight"""
import logging
from collections import defaultdict, deque

import numpy as np

logger = logging.getLogger("robot.concept_drift")

EPSILON = 1e-7


def psi(reference: np.ndarray, current: np.ndarray, bins: int = 10) -> float:
    """Population Stability Index entre deux distributions.
    0.0 = identique, <0.1 = faible drift, 0.1-0.25 = modéré, >0.25 = sévère.
    """
    if len(reference) == 0 or len(current) == 0:
        return 0.0
    all_vals = np.concatenate([reference, current])
    if np.all(all_vals == all_vals[0]):
        return 0.0
    edges = np.percentile(reference, np.linspace(0, 100, bins + 1))
    edges_arr = np.asarray(edges, dtype=float)
    edges_arr[-1] += EPSILON
    ref_counts, _ = np.histogram(reference, bins=edges_arr)
    cur_counts, _ = np.histogram(current, bins=edges_arr)
    ref_pct = ref_counts / max(len(reference), 1)
    cur_pct = cur_counts / max(len(current), 1)
    ref_pct = np.clip(ref_pct, EPSILON, 1.0)
    cur_pct = np.clip(cur_pct, EPSILON, 1.0)
    return float(np.sum((cur_pct - ref_pct) * np.log(cur_pct / ref_pct)))


class ConceptDriftDetector:
    """Détection de dérive conceptuelle graduelle par PSI.

    Maintient une distribution de référence (training) et une distribution
    courante (fenêtre glissante des prédictions récentes).
    """
    def __init__(self, window_size: int = 100, psi_threshold_light: float = 0.10,
                 psi_threshold_moderate: float = 0.20, psi_threshold_severe: float = 0.25,
                 feature_names: list[str] | None = None):
        self.window_size = window_size
        self.threshold_light = psi_threshold_light
        self.threshold_moderate = psi_threshold_moderate
        self.threshold_severe = psi_threshold_severe
        self.feature_names = feature_names or []

        # Référence: distribution apprise pendant le training
        self._reference: dict[str, np.ndarray] = {}

        # Courant: fenêtre glissante par feature
        self._current: dict[str, deque] = defaultdict(lambda: deque(maxlen=window_size))

        # Métriques modèles (accuracy, confidence) par modèle
        self._model_accuracy: dict[str, deque] = defaultdict(lambda: deque(maxlen=window_size))
        self._model_confidence: dict[str, deque] = defaultdict(lambda: deque(maxlen=window_size))

    def set_reference(self, feature_samples: dict[str, np.ndarray]):
        """Définit la distribution de référence (typiquement depuis le training set)."""
        for name, arr in feature_samples.items():
            if len(arr) > 0:
                self._reference[name] = np.asarray(arr, dtype=float)

    def add_sample(self, features: dict[str, float]):
        """Ajoute un échantillon courant pour chaque feature."""
        for name, val in features.items():
            self._current[name].append(float(val))

    def record_prediction(self, model_name: str, correct: bool, confidence: float = 0.0):
        """Enregistre le résultat d'une prédiction pour le drift tracking."""
        self._model_accuracy[model_name].append(1.0 if correct else 0.0)
        self._model_confidence[model_name].append(confidence)

    def compute_per_feature_psi(self) -> dict[str, float]:
        """Calcule PSI par feature. Retourne {feature_name: psi_value}."""
        result = {}
        for name, ref_arr in self._reference.items():
            cur_arr = list(self._current.get(name, []))
            if len(cur_arr) < 10 or len(ref_arr) < 10:
                continue
            result[name] = psi(ref_arr, np.array(cur_arr, dtype=float))
        return result

    def aggregate_drift(self) -> float:
        """Score de drift global: moyenne des PSI > threshold_light."""
        per_feature = self.compute_per_feature_psi()
        if not per_feature:
            return 0.0
        drifting = [v for v in per_feature.values() if v > self.threshold_light]
        return float(np.mean(drifting)) if drifting else 0.0

    def model_drift(self, model_name: str) -> float:
        """Score de drift basé sur la baisse d'accuracy d'un modèle."""
        acc = list(self._model_accuracy.get(model_name, []))
        if len(acc) < 20:
            return 0.0
        recent = np.mean(acc[-20:])
        historic = np.mean(acc)
        if historic < EPSILON:
            return 0.0
        return max(0.0, float((historic - recent) / historic))

    def drift_category(self, drift_score: float) -> str:
        if drift_score >= self.threshold_severe:
            return "SEVERE"
        elif drift_score >= self.threshold_moderate:
            return "MODERATE"
        elif drift_score >= self.threshold_light:
            return "LIGHT"
        return "NONE"

    def should_retrain(self, min_samples: int = 30) -> bool:
        """True si drift modéré+ ET assez d'échantillons."""
        if sum(len(q) for q in self._current.values()) < min_samples:
            return False
        agg = self.aggregate_drift()
        return agg >= self.threshold_moderate or any(
            self.model_drift(m) > 0.15 for m in list(self._model_accuracy.keys())
        )

    def get_report(self) -> dict:
        return {
            "aggregate_drift": self.aggregate_drift(),
            "drift_category": self.drift_category(self.aggregate_drift()),
            "per_feature_psi": self.compute_per_feature_psi(),
            "feature_count": len(self._current),
            "reference_features": list(self._reference.keys()),
            "model_accuracy_drift": {m: self.model_drift(m) for m in list(self._model_accuracy.keys())},
            "should_retrain": self.should_retrain(),
            "samples_collected": sum(len(q) for q in self._current.values()),
        }

    def reset_current(self):
        """Reset la fenêtre courante (après retraining)."""
        self._current.clear()
        self._model_accuracy.clear()
        self._model_confidence.clear()
