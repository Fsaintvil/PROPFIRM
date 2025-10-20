#!/usr/bin/env python3
"""
🧪 TESTS CRITIQUES POUR LIVE_TRADING_ENGINE
Tests de base pour valider les corrections apportées
"""

import pytest
import sys
import os
from pathlib import Path
from unittest.mock import patch
import pandas as pd
import numpy as np

# Ajouter le répertoire parent au path pour les imports
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_imports_critiques():
    """Test que tous les imports critiques fonctionnent"""
    try:
        from scripts.live_trading_engine import LiveTradingEngine  # noqa: F401
        from scripts.advanced_decision_engine import AdvancedDecisionEngine  # noqa: F401
        assert True
    except ImportError as e:
        pytest.fail(f"Import critique échoué: {e}")


def test_live_trading_engine_initialization():
    """Test initialisation du LiveTradingEngine"""
    from scripts.live_trading_engine import LiveTradingEngine
    
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


@patch('MetaTrader5.initialize')
@patch('MetaTrader5.login')
def test_get_ai_signals_with_symbol(mock_login, mock_init):
    """Test critique: get_ai_signals reçoit maintenant le symbole"""
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
    
    # Test avec symbole passé - CORRECTION CRITIQUE
    signals = engine.get_ai_signals(test_data, 'EURUSD')
    
    # Vérifications
    assert isinstance(signals, dict)
    assert 'combined_signal' in signals
    assert 'confidence' in signals
    assert signals['combined_signal'] in ['buy', 'sell', 'hold']


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
        'requirements.txt',
        'requirements-dev.txt',
        'requirements-freeze.txt'
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


if __name__ == "__main__":
    print("🧪 EXÉCUTION DES TESTS CRITIQUES")
    print("="*50)
    
    # Exécuter les tests
    pytest.main([__file__, "-v"])