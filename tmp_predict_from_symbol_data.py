#!/usr/bin/env python3
"""Construire l'input de prédiction à partir des dernières valeurs réelles
de `symbol_data` (EURUSD) et tester :
 - meta_learning.ensemble_predict(mapped_input)
 - AdvancedDecisionEngine.make_enhanced_decision(...) sur le même sample
"""
from scripts.live_trading_engine import LiveTradingEngine
from scripts.advanced_decision_engine import AdvancedDecisionEngine
import pandas as pd


def build_mapped_input(engine, data):
    """Reprend la logique de mapping utilisée en production pour construire
    un DataFrame (1, n_features) correspondant à model.feature_name()."""
    mapped_input = None

    if not hasattr(engine, 'meta_learning') or engine.meta_learning is None:
        print('MetaLearning non initialisé dans l\'engine; initialiser.')
        return None

    try:
        if (hasattr(engine.meta_learning, 'model_ensemble') and
                engine.meta_learning.model_ensemble):
            primary = engine.meta_learning.model_ensemble[0].get('model')
            if primary is None:
                primary = engine.meta_learning.model_ensemble[0]

            if hasattr(primary, 'feature_name'):
                fn = primary.feature_name() or []
            else:
                fn = []

            # Préparer les valeurs à partir des colonnes numériques disponibles
            last_features = data.select_dtypes(include=['number']).iloc[-1:]
            preferred_order = ['close', 'volume', 'sma_1T', 'ema_15T', 'rsi_60T']

            vals = []
            n_feat = max(len(fn), 1)
            for i in range(n_feat):
                source_col = None
                if i < len(preferred_order) and preferred_order[i] in last_features.columns:
                    source_col = preferred_order[i]
                elif i < len(last_features.columns):
                    source_col = last_features.columns[i]

                if source_col is not None and source_col in last_features.columns:
                    try:
                        v = float(last_features[source_col].iloc[-1])
                    except Exception:
                        v = 0.0
                else:
                    v = 0.0

                vals.append(v)

            mapped_input = pd.DataFrame([vals], columns=fn[:len(vals)])
    except Exception as e:
        print('Erreur construction mapped_input:', e)
        mapped_input = None

    return mapped_input


def main():
    engine = LiveTradingEngine(symbols=['EURUSD'])
    engine.initialize_ai_systems()

    data = engine.get_live_data('EURUSD', count=300)
    if data is None or len(data) == 0:
        print('Aucune donnée EURUSD reçue depuis MT5, utilisation de simulation/fallback')
        try:
            data = engine.generate_simulation_data(300)
        except Exception:
            print('Échec génération simulation - abort')
            return

    mapped = build_mapped_input(engine, data)
    print('\n--- MAPPED INPUT ---')
    print(mapped)

    # Prédiction meta-learning
    if mapped is not None and hasattr(engine.meta_learning, 'ensemble_predict'):
        pred = engine.meta_learning.ensemble_predict(mapped)
        print('\nmeta_learning.ensemble_predict ->', pred)
        try:
            pred_val = float(pred[0]) if pred is not None and len(pred) > 0 else 0.0
        except Exception:
            pred_val = 0.0
    else:
        pred_val = 0.0

    # Construire base_signals similaire à production
    base_signals = {
        'combined_signal': 'hold',
        'confidence': 0.0,
        'meta_learning': None
    }

    if pred_val is not None:
        action = 'hold'
        if pred_val > 0.6:
            action = 'buy'
        elif pred_val < 0.4:
            action = 'sell'

        # Appliquer le même clamp que dans le moteur (faible risque)
        raw_meta_conf = abs(pred_val - 0.5) * 2
        META_CONF_CLAMP = 0.3
        meta_conf = min(raw_meta_conf, META_CONF_CLAMP)

        base_signals['meta_learning'] = {
            'prediction': pred_val,
            'action': action,
            'confidence': meta_conf
        }
        base_signals['combined_signal'] = base_signals['meta_learning']['action']
        base_signals['confidence'] = base_signals['meta_learning']['confidence']

    print('\n--- BASE SIGNALS ---')
    print(base_signals)

    # Tester AdvancedDecisionEngine.make_enhanced_decision
    ade = AdvancedDecisionEngine()
    final = ade.make_enhanced_decision('EURUSD', data, base_signals)

    print('\n--- ADVANCED DECISION (full) ---')
    print(final)
    dm = final.get('decision_metrics')
    if dm:
        print('\ndecision_metrics:')
        print('  confidence:', getattr(dm, 'confidence', None))
        print('  uncertainty:', getattr(dm, 'uncertainty', None))
        print('  pattern_strength:', getattr(dm, 'pattern_strength', None))
        print('execution_urgency:', final.get('execution_urgency'))


if __name__ == '__main__':
    main()
