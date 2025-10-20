#!/usr/bin/env python3
"""
Script de validation pour vérifier que les corrections des faiblesses
fonctionnent.
"""

import sys
from pathlib import Path


def validate_centralized_config():
    """Valide que la configuration centralisée fonctionne."""
    print("🔍 Validation de la configuration centralisée...")

    try:
        # Test d'import de la configuration
        if '__file__' in globals():
            current_file = Path(__file__)
        else:
            current_file = Path.cwd()
        sys.path.append(str(current_file.parent.parent))
        from config.trading_config import TradingConfig

        # Vérifier les attributs essentiels
        required_attrs = [
            'DEFAULT_CONFIDENCE_THRESHOLD',
            'TRADING_INTERVAL_SECONDS',
            'CLEANUP_CYCLE_INTERVAL',
            'LOG_SUMMARY_INTERVAL',
            'MIN_SLEEP_SECONDS',
            'MAX_HISTORY_TRADES',
            'BASE_POSITION_SIZE',
            'MAX_DRAWDOWN_THRESHOLD'
        ]

        missing_attrs = []
        for attr in required_attrs:
            if not hasattr(TradingConfig, attr):
                missing_attrs.append(attr)

        if missing_attrs:
            print(f"❌ Attributs manquants: {missing_attrs}")
            return False
        else:
            print("✅ Configuration centralisée OK")
            return True

    except ImportError as e:
        print(f"❌ Import config échoué: {e}")
        return False
    except Exception as e:
        print(f"❌ Erreur config: {e}")
        return False


def validate_robust_retry():
    """Valide que le système de retry robuste fonctionne."""
    print("\n🔍 Validation du système de retry robuste...")

    try:
        # Test d'import du système de retry
        from utils.robust_retry import (
            RobustRetry, CircuitBreaker,
            MT5ConnectionError
        )

        # Test de création d'un objet RobustRetry (validation import)
        RobustRetry(max_retries=3, base_delay=1.0)

        # Test du circuit breaker (validation import)
        CircuitBreaker(failure_threshold=3, recovery_timeout=60.0)

        # Test des exceptions personnalisées
        try:
            raise MT5ConnectionError("Test exception")
        except MT5ConnectionError:
            pass  # Attendu

        print("✅ Système de retry robuste OK")
        return True

    except ImportError as e:
        print(f"❌ Import retry échoué: {e}")
        return False
    except Exception as e:
        print(f"❌ Erreur retry: {e}")
        return False


def validate_trading_engine_integration():
    """Valide l'intégration dans le moteur de trading."""
    print("\n🔍 Validation de l'intégration moteur de trading...")

    try:
        # Vérifier que le fichier existe
        engine_path = Path("scripts/live_trading_engine.py")
        if not engine_path.exists():
            print("❌ Fichier moteur de trading non trouvé")
            return False

        # Lire le contenu et vérifier les imports
        with open(engine_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Vérifications critiques
        checks = [
            ("from config.trading_config import TradingConfig",
             "Import configuration centralisée"),
            ("from utils.robust_retry import",
             "Import système de retry"),
            ("self.confidence_threshold = TradingConfig."
             "DEFAULT_CONFIDENCE_THRESHOLD",
             "Utilisation configuration"),
            ("@validate_input",
             "Décorateur de validation"),
            ("@robust_mt5_retry",
             "Décorateur de retry MT5")
        ]

        results = []
        for check_text, description in checks:
            if check_text in content:
                results.append(f"✅ {description}")
            else:
                results.append(f"❌ {description} manquant")

        print("\n".join(results))

        # Compter les succès
        success_count = sum(1 for result in results if result.startswith("✅"))
        total_count = len(results)

        if success_count == total_count:
            print(f"\n✅ Intégration moteur de trading OK "
                  f"({success_count}/{total_count})")
            return True
        else:
            print(f"\n⚠️ Intégration partielle "
                  f"({success_count}/{total_count})")
            return False

    except Exception as e:
        print(f"❌ Erreur validation moteur: {e}")
        return False


def validate_hardcoded_values_replaced():
    """Valide que les valeurs codées en dur ont été remplacées."""
    print("\n🔍 Validation du remplacement des valeurs codées en dur...")

    try:
        engine_path = Path("scripts/live_trading_engine.py")
        with open(engine_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Rechercher des patterns de valeurs codées en dur communes
        hardcoded_patterns = [
            ("0.68", "Seuil de confiance"),
            ("930", "Intervalle de trading"),
            ("time.sleep(60)", "Délai de sommeil fixe"),
            ("count=20", "Nombre de cycles fixe"),
            ("time.sleep(5)", "Délai de log fixe")
        ]

        found_hardcoded = []
        for pattern, description in hardcoded_patterns:
            if pattern in content:
                found_hardcoded.append(
                    f"⚠️ {description}: {pattern} encore présent")

        if found_hardcoded:
            print("\n".join(found_hardcoded))
            return False
        else:
            print("✅ Valeurs codées en dur correctement remplacées")
            return True

    except Exception as e:
        print(f"❌ Erreur validation valeurs: {e}")
        return False


def main():
    """Fonction principale de validation."""
    print("🔧 VALIDATION DES CORRECTIONS DE FAIBLESSES\n")
    print("=" * 50)

    validations = [
        validate_centralized_config,
        validate_robust_retry,
        validate_trading_engine_integration,
        validate_hardcoded_values_replaced
    ]

    results = []
    for validation_func in validations:
        try:
            result = validation_func()
            results.append(result)
        except Exception as e:
            print(f"❌ Erreur lors de {validation_func.__name__}: {e}")
            results.append(False)

    # Résumé final
    print("\n" + "=" * 50)
    print("📊 RÉSUMÉ DES VALIDATIONS:")

    success_count = sum(results)
    total_count = len(results)

    if success_count == total_count:
        print(f"🎉 TOUTES LES VALIDATIONS RÉUSSIES "
              f"({success_count}/{total_count})")
        print("\n✅ Les faiblesses identifiées ont été corrigées avec succès!")
        return True
    else:
        print(f"⚠️ VALIDATIONS PARTIELLES ({success_count}/{total_count})")
        print(f"   {total_count - success_count} problème(s) "
              f"restant(s) à corriger.")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
