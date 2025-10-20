"""Diagnostic non invasif des imports et modèles nécessaires au moteur.
Écrit un petit JSON `artifacts/imports_diagnose.json` avec les résultats.
"""
import json
import importlib
from pathlib import Path

checks = [
    'meta_learning_system',
    'advanced_decision_engine',
    'lightgbm',
    'xgboost',
    'catboost',
    'skopt',
    'sklearn'
]

results = {}

for name in checks:
    try:
        mod = importlib.import_module(name)
        results[name] = {'importable': True, 'version': getattr(mod, '__version__', None)}
    except Exception as e:
        results[name] = {'importable': False, 'error': str(e)}

# Vérifier la présence des modèles
models = list(Path('artifacts/auto_improve').glob('best_lightgbm*.txt'))
model_paths = [str(p) for p in models]

report = {
    'timestamp': __import__('datetime').datetime.now().isoformat(),
    'imports': results,
    'model_paths': model_paths
}

Path('artifacts').mkdir(exist_ok=True)
with open('artifacts/imports_diagnose.json', 'w', encoding='utf-8') as f:
    json.dump(report, f, indent=2)

print('Diagnostic exports: artifacts/imports_diagnose.json')
print(report)
