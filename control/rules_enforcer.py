"""
Contrôleur de règles opérationnelles pour le lancement production.

Objectif: empêcher des démarrages dans des conditions défavorables et
augmenter la probabilité de bons trades en imposant quelques garde-fous
simples sans changer l'architecture.

Règles implémentées (minimales, extensibles):
- Cap de risque par trade: 3% (0.03). Si supérieur, on bloque.
- Chargement des contraintes symboles si disponibles pour les logs.

API
----
enforce_operational_rules(max_risk_per_trade: float) -> dict
Retourne un rapport: {
  'status': 'ok'|'fail',
  'violations': [str, ...],
  'details': { ... meta ... }
}
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List


RISK_CAP = float(os.getenv("MAX_RISK_PER_TRADE_CAP", "0.03"))  # 3%
CONSTRAINTS_PATH = Path("artifacts/live_trading/symbol_constraints.json")


def _load_symbol_constraints(path: Path = CONSTRAINTS_PATH) -> Dict[str, Any]:
	if not path.exists():
		return {"constraints": []}
	try:
		data = json.loads(path.read_text(encoding="utf-8"))
		# Normaliser structure attendue
		if isinstance(data, dict) and "constraints" in data:
			return data
		if isinstance(data, list):
			return {"constraints": data}
		return {"constraints": []}
	except Exception:
		return {"constraints": []}


def enforce_operational_rules(max_risk_per_trade: float) -> Dict[str, Any]:
	violations: List[str] = []

	# Règle 1: cap de risque par trade
	try:
		risk_val = float(max_risk_per_trade)
	except Exception:
		risk_val = 1.0  # forcer une violation en cas d'entrée non numérique
	if risk_val > RISK_CAP:
		violations.append(
			f"max_risk_per_trade_cap: {risk_val:.4f} > {RISK_CAP:.4f}"
		)

	# Règle 2 (douce): existence des contraintes symboles (pour traçabilité)
	constraints = _load_symbol_constraints()
	constraints_count = len(constraints.get("constraints", []))
	if constraints_count == 0 and CONSTRAINTS_PATH.exists():
		# Fichier présent mais vide/invalide
		violations.append("symbol_constraints_invalid_or_empty")

	status = "ok" if not violations else "fail"
	report = {
		"status": status,
		"violations": violations,
		"details": {
			"risk_cap": RISK_CAP,
			"input_risk": risk_val,
			"constraints_path": str(CONSTRAINTS_PATH),
			"constraints_count": constraints_count,
		},
	}
	return report

