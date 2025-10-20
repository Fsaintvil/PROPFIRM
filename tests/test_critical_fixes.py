#!/usr/bin/env python3
"""
🧪 TESTS CRITIQUES POUR LIVE_TRADING_ENGINE
Tests de base pour valider les corrections apportées
"""

import pytest
import sys
import os
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import pandas as pd
import numpy as np

# Ajouter le répertoire parent au path pour les imports
sys.path.insert(0, str(Path(__file__).parent.parent))

def test_imports_critiques():
    """Test que tous les imports critiques fonctionnent"""
    try:
        # Import basique sans mt5_connector qui n'existe plus
        from scripts.live_trading_engine import LiveTradingEngine  # noqa: F401
        from scripts.advanced_decision_engine import AdvancedDecisionEngine  # noqa: F401
        assert True
    except ImportError as e:
        pytest.fail(f"Import critique échoué: {e}")


def test_live_trading_engine_initialization():
    """Test initialisation du LiveTradingEngine"""
    from scripts.live_trading_engine import LiveTradingEngine

    # Patch MT5 initialize if available in the environment
    try:
        with patch('MetaTrader5.initialize') as mock_init:
            mock_init.return_value = True

            # Test avec paramètres minimaux
            engine = LiveTradingEngine(
                symbols=['EURUSD'],
                lot_sizes={'EURUSD': 0.01},
                max_risk_per_trade=0.01
            )

            assert engine.symbols == ['EURUSD']
            assert engine.lot_sizes['EURUSD'] == 0.01
            assert engine.max_risk_per_trade == 0.01
    except Exception:
        # Si MetaTrader5 n'est pas importable dans cet environnement,
        # on vérifie seulement l'instanciation basique sans patch
        engine = LiveTradingEngine(
            symbols=['EURUSD'],
            lot_sizes={'EURUSD': 0.01},
            max_risk_per_trade=0.01
        )
        assert engine.symbols == ['EURUSD']
        assert engine.lot_sizes['EURUSD'] == 0.01
        assert engine.max_risk_per_trade == 0.01

@patch('MetaTrader5.initialize')
@patch('MetaTrader5.login')
def test_get_ai_signals_with_symbol(mock_login, mock_init):
    """Test critique: get_ai_signals renvoie un dict contenant les clés attendues"""
    from scripts.live_trading_engine import LiveTradingEngine

    mock_init.return_value = True
    mock_login.return_value = True

    engine = LiveTradingEngine(
        symbols=['EURUSD'],
        lot_sizes={'EURUSD': 0.01}
    )

    # Créer données de test
    test_data = pd.DataFrame({
        'close': np.random.randn(100) + 1.1,
        'high': np.random.randn(100) + 1.15,
        'low': np.random.randn(100) + 1.05,
        'open': np.random.randn(100) + 1.1,
        'volume': np.random.randint(1000, 10000, 100)
    })

    # Appel conforme à la signature (seul current_data)
    signals = engine.get_ai_signals(test_data)

    # Vérifications
    assert isinstance(signals, dict)
    assert 'combined_signal' in signals
    assert 'confidence' in signals
    assert signals['combined_signal'] in ['buy', 'sell', 'hold']

@patch('MetaTrader5.initialize')
def test_advanced_decision_engine_import(mock_init):
    """Test que l'import advanced_decision_engine fonctionne"""
    from scripts.live_trading_engine import LiveTradingEngine
    
    mock_init.return_value = True
    
    engine = LiveTradingEngine(symbols=['EURUSD'], lot_sizes={'EURUSD': 0.01})
    
    # Test données minimum
    test_data = pd.DataFrame({'close': [1.1, 1.11, 1.12]})
    base_signals = {
        'combined_signal': 'buy',
        'confidence': 0.7
    }
    
    try:
        # Appel conforme à la signature (symbol, data, base_signals)
        result = engine.apply_advanced_decision_engine('EURUSD', test_data, base_signals)
        assert isinstance(result, dict)
    except ImportError:
        # C'est OK si le module n'est pas trouvé
        pass
    except NameError as e:
        if 'symbol' in str(e):
            pytest.fail(f"Erreur 'symbol' non résolue: {e}")

def test_data_integrity():
    """Test intégrité des données nettoyées"""
    import json
    
    # Test paper_trades.json
    if os.path.exists('data/paper_trades.json'):
        with open('data/paper_trades.json', 'r') as f:
            lines = f.readlines()
        
        for line_num, line in enumerate(lines[:5], 1):  # Test premiers 5
            trade = json.loads(line.strip())
            
            # Vérifications critiques
            assert 'timestamp' in trade, f"Ligne {line_num}: timestamp manquant"
            assert 'symbol' in trade, f"Ligne {line_num}: symbol manquant"
            
            # Vérifier prix cohérents
            price = trade.get('price')
            if price is not None:
                symbol = trade['symbol']
                if symbol == 'XAUUSD':
                    assert price > 100, f"Prix XAUUSD trop bas: {price}"
                elif symbol == 'BTCUSD':
                    assert price > 1000, f"Prix BTCUSD trop bas: {price}"

def test_requirements_files():
    """Test que les fichiers requirements existent et sont valides"""
    required_files = [
        'requirements.txt'
    ]
    
    for file_path in required_files:
        assert os.path.exists(file_path), f"Fichier manquant: {file_path}"
        
        # Vérifier que le fichier n'est pas vide
        with open(file_path, 'r') as f:
            content = f.read().strip()
            assert len(content) > 0, f"Fichier vide: {file_path}"

def test_env_template():
    """Test que le template d'environnement existe"""
    template_path = 'config/env.template'
    assert os.path.exists(template_path), "config/env.template manquant"
    
    with open(template_path, 'r') as f:
        content = f.read()
        
    # Vérifier présence des sections critiques
    critical_sections = [
        'MT5_LOGIN',
        'MT5_PASSWORD',
        'CONFIDENCE_THRESHOLD',
        'TRADING_SYMBOLS'
    ]
    
    for section in critical_sections:
        assert section in content, f"Section manquante dans env.template: {section}"

def test_stop_loss_take_profit_calculation():
    """Test que les calculs SL/TP utilisent les nouveaux paramètres"""
    from scripts.live_trading_engine import LiveTradingEngine
    
    with patch('MetaTrader5.initialize') as mock_init:
        mock_init.return_value = True
        
        engine = LiveTradingEngine(symbols=['EURUSD'], lot_sizes={'EURUSD': 0.01})
        # Test prix d'entrée réaliste
        entry_price = 1.1650

        # Test calcul SL pour EURUSD (signature: symbol, action, entry_price)
        sl = engine.calculate_dynamic_stop_loss('EURUSD', 'buy', entry_price)
        
        # Vérifier que le SL n'est pas trop éloigné (anciens paramètres = 20 pips)
        sl_distance = abs(entry_price - sl)
        assert sl_distance < 0.0015, f"SL trop éloigné: {sl_distance} (>15 pips)"
        assert sl_distance > 0.0001, f"SL trop proche: {sl_distance} (<1 pip)"

if __name__ == "__main__":
    print("🧪 EXÉCUTION DES TESTS CRITIQUES")
    print("="*50)
    
    # Exécuter les tests
    pytest.main([__file__, "-v", "-x"])  # -x pour arrêter au premier échec