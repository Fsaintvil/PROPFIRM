#!/usr/bin/env python3
"""
Test complet des améliorations appliquées
"""

import sys
import os
import pytest
from pathlib import Path
import traceback


def test_import_live_engine():
    """Tester l'import du moteur live"""
    try:
        sys.path.insert(0, str(Path.cwd()))
        from scripts.live_trading_engine import LiveTradingEngine
        print("✅ Import LiveTradingEngine réussi")
    except Exception as e:
        print(f"❌ Erreur import LiveTradingEngine: {e}")
        traceback.print_exc()
        pytest.fail("Import LiveTradingEngine a échoué")


def test_enhanced_xauusd_signal():
    """Tester le signal XAUUSD amélioré"""
    try:
        from scripts.live_trading_engine import LiveTradingEngine
        import pandas as pd
        import numpy as np
        
        # Créer instance
        engine = LiveTradingEngine(symbols=["XAUUSD"])
        
        # Données de test pour XAUUSD
        dates = pd.date_range(start='2025-01-01', periods=50, freq='h')
        prices = np.random.normal(2300, 50, 50)  # Prix autour de 2300
        data = pd.DataFrame({
            'close': prices,
            'open': prices * 0.999,
            'high': prices * 1.001,
            'low': prices * 0.998,
            'volume': np.random.randint(100, 1000, 50)
        }, index=dates)
        
        # Tester le signal amélioré
        signal = engine.generate_enhanced_xauusd_signal(data)
        
        print(f"✅ Signal XAUUSD généré: {signal}")
        
        # Vérifier structure
        required_keys = ['action', 'confidence']
        for key in required_keys:
            if key not in signal:
                print(f"❌ Clé manquante: {key}")
                pytest.fail(f"Clé manquante dans le signal: {key}")
        
        # Vérifier valeurs
        if signal['action'] not in ['buy', 'sell', 'hold']:
            print(f"❌ Action invalide: {signal['action']}")
            pytest.fail("Action invalide dans le signal")
            
        if not 0 <= signal['confidence'] <= 1:
            print(f"❌ Confiance invalide: {signal['confidence']}")
            pytest.fail("Confiance invalide dans le signal")

        print(
            f"✅ Signal XAUUSD valide: action={signal['action']}, "
            f"conf={signal['confidence']:.3f}"
        )
        assert True
        
    except Exception as e:
        print(f"❌ Erreur test signal XAUUSD: {e}")
        traceback.print_exc()
        pytest.fail("Génération signal XAUUSD a échoué")


def test_mt5_connector_improvements():
    """Tester les améliorations MT5"""
    try:
        from src.utils.mt5_connector import mt5_health_check, safe_mt5_import
        
        # Test import sécurisé
        result = safe_mt5_import()
        print(f"✅ Import MT5 sécurisé: {result}")
        
        # Test health check
        health = mt5_health_check()
        print(f"✅ Health check MT5: {health}")
        assert True
        
    except Exception as e:
        print(f"❌ Erreur test MT5: {e}")
        traceback.print_exc()
        pytest.fail("MT5 connector tests échoués")


def test_optimized_logging():
    """Tester le logging optimisé"""
    try:
        from src.utils.optimized_logging import setup_optimized_logging
        
        logger = setup_optimized_logging("test_logger")
        logger.info("Test message logging optimisé")
        
        print("✅ Logging optimisé fonctionnel")
        assert True
        
    except Exception as e:
        print(f"❌ Erreur test logging: {e}")
        traceback.print_exc()
        pytest.fail("Optimized logging test échoué")


def test_position_sizing():
    """Tester le position sizing adaptatif"""
    try:
        from src.utils.adaptive_position_sizing import AdaptivePositionSizing
        import pandas as pd
        import numpy as np
        
        # Créer instance
        sizer = AdaptivePositionSizing()
        
        # Données de test
        dates = pd.date_range(start='2025-01-01', periods=100, freq='h')
        prices = np.random.normal(1.1000, 0.005, 100)
        data = pd.DataFrame({
            'close': prices
        }, index=dates)
        
        # Test calcul
        result = sizer.calculate_optimal_size(
            "EURUSD", 1.1000, data, ["BTCUSD"]
        )
        
        print(f"✅ Position sizing calculé: {result}")
        
        # Vérifier structure
        if 'recommended_size' not in result:
            print("❌ recommended_size manquant")
            pytest.fail("recommended_size manquant")
            
        print(f"✅ Position sizing: {result['recommended_size']:.3f}")
        assert True
        
    except Exception as e:
        print(f"❌ Erreur test position sizing: {e}")
        traceback.print_exc()
        pytest.fail("Position sizing test échoué")


def test_error_corrections():
    """Tester que les erreurs de lint sont corrigées"""
    try:
        # Import principal sans erreurs
        from scripts.live_trading_engine import LiveTradingEngine
        
        # Créer instance avec paramètres par défaut
        engine = LiveTradingEngine()
        
        print("✅ Aucune erreur critique détectée")
        assert True
        
    except Exception as e:
        print(f"❌ Erreurs encore présentes: {e}")
        pytest.fail("Erreurs critiques détectées")


def main():
    """Test complet de toutes les améliorations"""
    print("🧪 TEST COMPLET DES AMÉLIORATIONS")
    print("=" * 50)
    
    tests = [
        ("Import Live Engine", test_import_live_engine),
        ("Signal XAUUSD Amélioré", test_enhanced_xauusd_signal),
        ("MT5 Connector", test_mt5_connector_improvements),
        ("Logging Optimisé", test_optimized_logging),
        ("Position Sizing", test_position_sizing),
        ("Corrections Erreurs", test_error_corrections),
    ]
    
    results = []
    
    for test_name, test_func in tests:
        print(f"\n🧪 {test_name}:")
        print("-" * 30)
        try:
            success = test_func()
            results.append((test_name, success))
        except Exception as e:
            print(f"❌ Échec test {test_name}: {e}")
            results.append((test_name, False))
    
    print(f"\n📊 RÉSULTATS FINAUX")
    print("=" * 50)
    
    passed = sum(1 for _, success in results if success)
    total = len(results)
    
    for test_name, success in results:
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"{status} {test_name}")
    
    print(f"\n🎯 Score: {passed}/{total} ({passed/total*100:.1f}%)")
    
    if passed == total:
        print("🎉 TOUS LES TESTS PASSENT - AMÉLIORATIONS VALIDÉES")
    elif passed >= total * 0.8:
        print("✅ MAJORITÉ DES TESTS PASSENT - CORRECTIONS RÉUSSIES")
    else:
        print("⚠️ PLUSIEURS ÉCHECS - CORRECTIONS ADDITIONNELLES NÉCESSAIRES")


if __name__ == "__main__":
    main()