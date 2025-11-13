"""Advanced Decision Engine (light)

Lecture du seuil depuis l'environnement (BASE_CONFIDENCE_THRESHOLD),
calibration simple à partir de artifacts/live_trading/performance_summary.json,
et logs d'initialisation pour traçabilité.

Ce module n'introduit pas de dépendances externes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict


class AdvancedDecisionEngine:
    """Engine minimal avec lecture ENV et calibration basique.

    - base_confidence_threshold: valeur de base depuis ENV ou défaut.
    - adaptive_range: borne basse/haute pour ajustement.
    - performance_summary: lecture best-effort d'un JSON si présent.
    """

    def __init__(
        self,
        base_confidence_threshold: float | None = None,
        adaptive_low: float = 0.55,
        adaptive_high: float = 0.85,
        perf_summary_path: Path | None = None,
    ) -> None:
        # 1) Lire ENV
        if base_confidence_threshold is None:
            try:
                env_val = os.environ.get("BASE_CONFIDENCE_THRESHOLD")
                base_confidence_threshold = (
                    float(env_val) if env_val is not None else 0.55
                )
            except Exception:
                base_confidence_threshold = 0.55

        self.base_confidence_threshold = max(0.0, min(1.0, base_confidence_threshold))
        self.adaptive_range = [float(adaptive_low), float(adaptive_high)]
        if self.adaptive_range[0] > self.adaptive_range[1]:
            self.adaptive_range.sort()

        # 2) Lire performance_summary
        if perf_summary_path is None:
            perf_summary_path = Path("artifacts") / "live_trading" / "performance_summary.json"
        self.perf_summary_path = perf_summary_path
        self.performance_summary: Dict[str, Any] = {}
        if self.perf_summary_path.exists():
            try:
                self.performance_summary = json.loads(
                    self.perf_summary_path.read_text(encoding="utf-8")
                )
            except Exception:
                self.performance_summary = {}

        # 3) Calculer seuil effectif (calibration simple)
        self.effective_confidence_threshold = self._compute_effective_threshold()

        # 4) Logs init (stdout)
        print("[INIT] AdvancedDecisionEngine from", str(Path(__file__).resolve()))
        ar_low = self.adaptive_range[0]
        ar_high = self.adaptive_range[1]
        print(
            f"[INIT] BASE_CONFIDENCE_THRESHOLD={self.base_confidence_threshold:.2f}, "
            f"adaptive_range=[{ar_low:.2f}, {ar_high:.2f}]"
        )
        print(
            f"[INIT] Effective base_confidence_threshold={self.effective_confidence_threshold:.2f}"
        )

    def _compute_effective_threshold(self) -> float:
        """Applique un ajustement simple basé sur des métriques de perf.

        Heuristique: si win_rate >= 0.55 et sharpe >= 0.8, on relève la base
        de +0.10 bornée à adaptive_high. Si win_rate < 0.45 ou sharpe < 0.2,
        on maintient la base (pas de baisse ici pour rester conservateur).
        """
        base = self.base_confidence_threshold
        low, high = self.adaptive_range

        try:
            wr = float(self.performance_summary.get("win_rate", 0.0))
        except Exception:
            wr = 0.0
        try:
            sh = float(self.performance_summary.get("sharpe", 0.0))
        except Exception:
            sh = 0.0

        # Ajustement conservateur
        if wr >= 0.55 and sh >= 0.8:
            adj = min(base + 0.10, high)
            return max(low, adj)
        return max(low, min(high, base))


def main() -> None:
    print("\N{BRAIN} TEST AdvancedDecisionEngine (light)")
    _ = AdvancedDecisionEngine()
    print("\u2705 OK")


if __name__ == "__main__":
    main()
import os
import json
from pathlib import Path


class AdvancedDecisionEngine:
    def __init__(self):
        # === CONFIGURATION INITIALE ===
        # Seuil global depuis environnement, sinon fallback propre (0.55)
        self.base_confidence_threshold = float(
            os.getenv("BASE_CONFIDENCE_THRESHOLD", 0.55)
        )
        self.adaptive_threshold_range = [0.55, 0.85]

        # Log d'initialisation complet pour debug
        try:
            print(f"[INIT] AdvancedDecisionEngine from {__file__}")
        except Exception:
            print("[INIT] AdvancedDecisionEngine (file unknown)")
        print(
            f"[INIT] BASE_CONFIDENCE_THRESHOLD={self.base_confidence_threshold}, adaptive_range={self.adaptive_threshold_range}"
        )

        # === AUTO-ADAPTATION SELON PERFORMANCE ===
        perf_path = Path("artifacts/live_trading/performance_summary.json")
        if perf_path.exists():
            try:
                data = json.loads(perf_path.read_text())
                winrate = float(data.get("winrate", 0.5))
                avg_rr = float(data.get("avg_rr", 1.0))

                print(f"[PERF] winrate={winrate:.2f}, avg_rr={avg_rr:.2f}")

                # Si très bon winrate → rend le moteur plus sélectif
                if winrate > 0.6 and avg_rr > 1.2:
                    self.base_confidence_threshold = min(
                        self.base_confidence_threshold + 0.05,
                        self.adaptive_threshold_range[1],
                    )
                    print(
                        f"[ADAPT] Performance >60% → hausse seuil à {self.base_confidence_threshold:.2f}"
                    )

                # Si mauvais winrate → assouplit légèrement le seuil
                elif winrate < 0.4:
                    self.base_confidence_threshold = max(
                        self.base_confidence_threshold - 0.05,
                        self.adaptive_threshold_range[0],
                    )
                    print(
                        f"[ADAPT] Performance <40% → baisse seuil à {self.base_confidence_threshold:.2f}"
                    )

            except Exception as e:
                print(f"[WARN] Adaptive performance tuning skipped: {e}")

        print(f"[INIT] Effective base_confidence_threshold={self.base_confidence_threshold}")

        # === MODE AUTO-THRESHOLD (optionnel) ===
        self.enable_auto_threshold = os.getenv("AUTO_THRESHOLD_MODE", "0") == "1"
        if self.enable_auto_threshold:
            print("[MODE] AUTO_THRESHOLD_MODE activé")


def main():
    print("🧠 TEST AdvancedDecisionEngine (light)")
    eng = AdvancedDecisionEngine()
    print("✅ OK")


if __name__ == "__main__":
    main()
