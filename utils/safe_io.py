#!/usr/bin/env python3
"""
Utilitaires d'entrée/sortie sécurisées avec gestion d'erreurs robuste.

Ce module centralise toutes les opérations de fichiers avec:
- Gestion d'erreurs robuste
- Validation des chemins
- Création automatique des dossiers
- Fallback pour fichiers manquants
"""

import json
import os
import pandas as pd
from pathlib import Path
from typing import Optional, Dict, Any


def safe_read_csv(
    filepath: str,
    fallback_data: Optional[pd.DataFrame] = None,
    **kwargs
) -> pd.DataFrame:
    """Lecture sécurisée de fichiers CSV avec fallback."""
    try:
        if not os.path.exists(filepath):
            print(f"⚠️  Fichier CSV manquant: {filepath}")
            if fallback_data is not None:
                print("🔄 Utilisation données fallback")
                return fallback_data
            else:
                print("🔄 Création DataFrame vide")
                return pd.DataFrame()

        return pd.read_csv(filepath, **kwargs)

    except FileNotFoundError:
        print(f"🔴 Fichier non trouvé: {filepath}")
        return fallback_data if fallback_data is not None else pd.DataFrame()
    except pd.errors.EmptyDataError:
        print(f"⚠️  Fichier CSV vide: {filepath}")
        return fallback_data if fallback_data is not None else pd.DataFrame()
    except Exception as e:
        print(f"🔴 Erreur lecture CSV {filepath}: {e}")
        return fallback_data if fallback_data is not None else pd.DataFrame()


def safe_read_json(
    filepath: str,
    fallback_data: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Lecture sécurisée de fichiers JSON avec fallback."""
    try:
        if not os.path.exists(filepath):
            print(f"⚠️  Fichier JSON manquant: {filepath}")
            if fallback_data is not None:
                print("🔄 Utilisation données fallback")
                return fallback_data
            else:
                print("🔄 Retour dictionnaire vide")
                return {}

        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)

    except FileNotFoundError:
        print(f"🔴 Fichier non trouvé: {filepath}")
        return fallback_data if fallback_data is not None else {}
    except json.JSONDecodeError as e:
        print(f"🔴 JSON invalide {filepath}: {e}")
        return fallback_data if fallback_data is not None else {}
    except Exception as e:
        print(f"🔴 Erreur lecture JSON {filepath}: {e}")
        return fallback_data if fallback_data is not None else {}


def safe_write_json(
    data: Dict[str, Any],
    filepath: str,
    create_dirs: bool = True
) -> bool:
    """Écriture sécurisée de fichiers JSON."""
    try:
        # Créer les dossiers si nécessaire
        if create_dirs:
            os.makedirs(os.path.dirname(filepath), exist_ok=True)

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        print(f"✅ JSON sauvegardé: {filepath}")
        return True

    except PermissionError:
        print(f"🔴 Permission refusée: {filepath}")
        return False
    except Exception as e:
        print(f"🔴 Erreur écriture JSON {filepath}: {e}")
        return False


def safe_write_csv(
    df: pd.DataFrame,
    filepath: str,
    create_dirs: bool = True,
    **kwargs
) -> bool:
    """Écriture sécurisée de fichiers CSV."""
    try:
        # Créer les dossiers si nécessaire
        if create_dirs:
            os.makedirs(os.path.dirname(filepath), exist_ok=True)

        df.to_csv(filepath, **kwargs)
        print(f"✅ CSV sauvegardé: {filepath}")
        return True

    except PermissionError:
        print(f"🔴 Permission refusée: {filepath}")
        return False
    except Exception as e:
        print(f"🔴 Erreur écriture CSV {filepath}: {e}")
        return False


def ensure_directory(dirpath: str) -> bool:
    """Création sécurisée de dossiers."""
    try:
        os.makedirs(dirpath, exist_ok=True)
        return True
    except PermissionError:
        print(f"🔴 Permission refusée pour créer: {dirpath}")
        return False
    except Exception as e:
        print(f"🔴 Erreur création dossier {dirpath}: {e}")
        return False


def file_exists_and_readable(filepath: str) -> bool:
    """Vérification d'existence et de lisibilité d'un fichier."""
    try:
        return os.path.exists(filepath) and os.access(filepath, os.R_OK)
    except Exception:
        return False


def get_safe_path(base_path: str, relative_path: str) -> str:
    """Construction sécurisée de chemins de fichiers."""
    try:
        # Nettoyer et normaliser les chemins
        base = Path(base_path).resolve()
        full_path = (base / relative_path).resolve()

        # Vérifier que le chemin reste dans le répertoire de base
        if not str(full_path).startswith(str(base)):
            raise ValueError("Chemin en dehors du répertoire autorisé")

        return str(full_path)

    except Exception as e:
        print(f"🔴 Erreur construction chemin: {e}")
        return os.path.join(base_path, relative_path)


# Données de fallback pour les tests
FALLBACK_SAMPLE_DATA = pd.DataFrame({
    'timestamp': pd.date_range('2024-01-01', periods=100, freq='h'),
    'open': 1.1000 + 0.001 * pd.Series(range(100)).cumsum(),
    'high': 1.1000 + 0.001 * pd.Series(range(100)).cumsum() + 0.0005,
    'low': 1.1000 + 0.001 * pd.Series(range(100)).cumsum() - 0.0005,
    'close': 1.1000 + 0.001 * pd.Series(range(100)).cumsum(),
    'volume': 1000 + 100 * pd.Series(range(100))
})

FALLBACK_CONFIG = {
    "model_type": "lightgbm",
    "features": ["rsi", "macd", "bollinger"],
    "lookback": 20,
    "risk_pct": 0.02
}


if __name__ == "__main__":
    # Tests des fonctions
    print("🧪 Test des utilitaires I/O sécurisées")

    # Test lecture fichier manquant
    df = safe_read_csv("fichier_inexistant.csv", FALLBACK_SAMPLE_DATA)
    print(f"DataFrame fallback: {len(df)} lignes")

    # Test écriture et lecture JSON
    test_data = {"test": "data", "value": 42}
    if safe_write_json(test_data, "test_output.json"):
        read_data = safe_read_json("test_output.json")
        print(f"JSON lu: {read_data}")

    print("✅ Tests terminés")
