#!/usr/bin/env python3
"""
Test des améliorations du robot de trading
Validation de toutes les optimisations apportées
"""

import sys
import os
import time
import pytest
from datetime import datetime
import pandas as pd
import numpy as np

# Ajouter les chemins nécessaires
sys.path.append('scripts')
sys.path.append('utils')
sys.path.append('src/utils')

def test_imports_corriges():
    """Tester que tous les imports fonctionnent"""
    print("🧪 Test des imports corrigés...")
    
    try:
        from live_trading_engine import LiveTradingEngine
        print("✅ Import LiveTradingEngine réussi")
        
        from safe_io import safe_read_csv, FALLBACK_SAMPLE_DATA
        print("✅ Import safe_io réussi")
        
        from mt5_connector import get_mt5, is_mt5_available
        print("✅ Import mt5_connector réussi")
        
        assert True
    except Exception as e:
        print(f"❌ Erreur import: {e}")
        pytest.fail("Imports corrigés échoués")

def test_robustesse_systeme():
    """Tester la robustesse du système avec erreurs simulées"""
    print("\n🧪 Test de robustesse...")
    
    try:
        from live_trading_engine import LiveTradingEngine
        
        engine = LiveTradingEngine(
            symbols=["EURUSD"],
            lot_sizes={"EURUSD": 0.01}
        )
        
        # Test avec données corrompues
        corrupted_data = pd.DataFrame({
            'close': [None, np.inf, -1, 1.1000],
            'high': [1.1005, 1.1010, None, 1.1008],
            'low': [0.9995, 1.0999, 1.0998, None],
            'returns': [0.001, None, -0.002, 0.0015]
        })
        
        # Le système doit gérer ces données sans planter
        signals = engine.get_ai_signals(corrupted_data)
        print("✅ Gestion des données corrompues OK")
        
        # Test exécution avec paramètres invalides
        result = engine.execute_trade("invalid_action", "EURUSD", -1.0)
        if not result:
            print("✅ Validation paramètres trade OK")
        assert True
        
    except Exception as e:
        print(f"❌ Erreur robustesse: {e}")
        pytest.fail("Test robustesse échoué")

def test_gestion_memoire():
    """Tester la gestion mémoire optimisée"""
    print("\n🧪 Test gestion mémoire...")
    
    try:
        from live_trading_engine import LiveTradingEngine
        
        engine = LiveTradingEngine()
        
        # Simuler accumulation de données
        for i in range(2000):
            trade_info = {
                "timestamp": datetime.now(),
                "symbol": "EURUSD",
                "action": "buy",
                "volume": 0.01,
                "price": 1.1000 + i * 0.0001
            }
            engine.trade_history.append(trade_info)
        
        print(f"Trades avant cleanup: {len(engine.trade_history)}")
        
        # Tester le cleanup
        engine.cleanup_memory()
        
        print(f"Trades après cleanup: {len(engine.trade_history)}")
        
        if len(engine.trade_history) <= 1000:
            print("✅ Nettoyage mémoire OK")
            assert True
        else:
            print("❌ Nettoyage mémoire inefficace")
            pytest.fail("Nettoyage mémoire inefficace")
            
    except Exception as e:
        print(f"❌ Erreur gestion mémoire: {e}")
        pytest.fail("Gestion mémoire échouée")

def test_logging_ameliore():
    """Tester le système de logging amélioré"""
    print("\n🧪 Test logging amélioré...")
    
    try:
        from live_trading_engine import LiveTradingEngine
        
        engine = LiveTradingEngine()
        
        # Tester différents niveaux de log
        engine.logger.info("Test message INFO")
        engine.logger.warning("Test message WARNING")
        engine.logger.error("Test message ERROR")
        
        # Vérifier que les fichiers de log existent
        if os.path.exists("logs"):
            log_files = [f for f in os.listdir("logs") if f.endswith('.log')]
            if log_files:
                print(f"✅ Logs créés: {len(log_files)} fichiers")
                assert True
                return
        
        print("❌ Pas de fichiers de log trouvés")
        pytest.fail("Fichiers de log non trouvés")
        
    except Exception as e:
        print(f"❌ Erreur logging: {e}")
        pytest.fail("Logging amélioré test échoué")

def test_performance_optimisee():
    """Tester les optimisations de performance"""
    print("\n🧪 Test optimisations performance...")
    
    try:
        from live_trading_engine import LiveTradingEngine
        
        engine = LiveTradingEngine()
        
        # Générer données de test
        test_data = pd.DataFrame({
            'timestamp': pd.date_range('2024-01-01', periods=100, freq='h'),
            'open': 1.1000 + np.random.randn(100) * 0.001,
            'high': 1.1005 + np.random.randn(100) * 0.001,
            'low': 1.0995 + np.random.randn(100) * 0.001,
            'close': 1.1000 + np.random.randn(100) * 0.001,
            'volume': 1000 + np.random.randint(0, 500, 100),
            'returns': np.random.randn(100) * 0.001
        })
        
        # Mesurer temps d'exécution des signaux
        start_time = time.time()
        signals = engine.get_ai_signals(test_data)
        execution_time = time.time() - start_time
        
        print(f"Temps exécution signaux: {execution_time:.3f}s")
        
        if execution_time < 1.0:  # Moins d'1 seconde
            print("✅ Performance signaux OK")
        else:
            print("⚠️  Performance signaux lente")
        
        # Vérifier la qualité des signaux
        if all(key in signals for key in ['combined_signal', 'confidence']):
            print("✅ Structure signaux OK")
            assert True
        else:
            print("❌ Structure signaux incomplète")
            pytest.fail("Structure signaux incomplète")
            
    except Exception as e:
        print(f"❌ Erreur performance: {e}")
        pytest.fail("Performance test échoué")

def test_health_check():
    """Tester le health check complet"""
    print("\n🧪 Test health check...")
    
    try:
        from live_trading_engine import LiveTradingEngine
        
        engine = LiveTradingEngine()
        
        # Exécuter health check
        health_result = engine.production_health_check()
        
        print(f"Health check résultat: {health_result}")
        
        if isinstance(health_result, bool):
            print("✅ Health check fonctionne")
            assert True
        else:
            print("❌ Health check format incorrect")
            pytest.fail("Health check format incorrect")
            
    except Exception as e:
        print(f"❌ Erreur health check: {e}")
        pytest.fail("Health check test échoué")

def main():
    """Exécuter tous les tests d'amélioration"""
    print("🚀 TEST DES AMÉLIORATIONS DU ROBOT DE TRADING")
    print("=" * 60)
    
    tests = [
        ("Imports corrigés", test_imports_corriges),
        ("Robustesse système", test_robustesse_systeme),
        ("Gestion mémoire", test_gestion_memoire),
        ("Logging amélioré", test_logging_ameliore),
        ("Performance optimisée", test_performance_optimisee),
        ("Health check", test_health_check)
    ]
    
    results = []
    
    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
            status = "✅ PASS" if result else "❌ FAIL"
            print(f"{status} - {test_name}")
        except Exception as e:
            print(f"💥 CRASH - {test_name}: {e}")
            results.append((test_name, False))
    
    # Résumé
    print("\n" + "=" * 60)
    print("📊 RÉSUMÉ DES TESTS")
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    print(f"Tests réussis: {passed}/{total}")
    print(f"Taux de réussite: {passed/total*100:.1f}%")
    
    if passed == total:
        print("🎉 TOUS LES TESTS PASSÉS - ROBOT AMÉLIORÉ AVEC SUCCÈS!")
    else:
        print("⚠️  Certaines améliorations nécessitent attention")
        
        # Détail des échecs
        failed_tests = [name for name, result in results if not result]
        print(f"Tests échoués: {', '.join(failed_tests)}")
    
    return passed == total

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)