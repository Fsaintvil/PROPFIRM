"""Smoke tests — vérifie la structure des scripts de pipeline ML par analyse AST

Ces fichiers sont des scripts standalone avec des effets de bord en top-level
(lecture de fichiers, opérations MT5). On utilise l'AST pour vérifier que les
symboles principaux existent sans exécuter le code.
"""
import ast
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "engine_simple"
RECALIB_DIR = Path(__file__).resolve().parent.parent / "scripts" / "recalibration"

EXPECTED = {
    "step1_parse_reports": {"parse_report"},
    "step2_validate_ml": {"get_rates_before", "MLEnsemble"},
    "step3_train_dl_calibrate": {"FULL_FEATURE_NAMES", "DLEnsemble", "SYMBOLS"},
    "step4_retrain_dl_56feat": {"LSTMNet", "train_model"},
    "step5_retrain_dl_attention": {"train_model"},
    "step5_retrain_v2": {"train_model", "parse_trade_time", "get_bar_idx_for_trade"},
    "download_h1_2026": {"SYMBOLS"},
    "merge_all_reports": {"parse_report", "ACCOUNTS"},
    "validate_temporal": {"build_sequences", "parse_time"},
}


def _get_top_level_names(tree):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


@pytest.mark.parametrize("module", sorted(EXPECTED))
def test_module_has_expected_symbols(module):
    filepath = SCRIPTS_DIR / f"{module}.py"
    if not filepath.exists():
        filepath = RECALIB_DIR / f"{module}.py"
    assert filepath.exists(), f"{filepath} not found (tried engine_simple/ and scripts/recalibration/)"
    tree = ast.parse(filepath.read_text(encoding="utf-8"))
    names = _get_top_level_names(tree)
    for symbol in EXPECTED[module]:
        assert symbol in names, f"{module}.py missing top-level symbol: {symbol}"
