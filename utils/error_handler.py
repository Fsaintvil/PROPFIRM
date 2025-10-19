#!/usr/bin/env python3
"""
Gestionnaire d'erreurs centralisé pour une meilleure robustesse.

Ce module fournit:
- Gestion d'erreurs standardisée
- Logging robuste avec fallback
- Notification d'erreurs critiques
- Recovery automatique pour erreurs communes
"""

import logging
import os
import sys
import traceback
from datetime import datetime
from typing import Optional, Callable, Any
from functools import wraps


# Configuration du logging avec fallback
def setup_logging(log_dir: str = "logs") -> logging.Logger:
    """Configuration robuste du système de logging."""
    try:
        # Créer le dossier de logs si nécessaire
        os.makedirs(log_dir, exist_ok=True)

        # Configuration du logger principal
        logger = logging.getLogger("PROPFIRM")
        logger.setLevel(logging.INFO)

        # Éviter les doublons de handlers
        if logger.handlers:
            return logger

        # Handler pour fichier
        log_file = os.path.join(log_dir, f"app_{datetime.now():%Y%m%d}.log")
        try:
            file_handler = logging.FileHandler(log_file, encoding='utf-8')
            file_formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            file_handler.setFormatter(file_formatter)
            logger.addHandler(file_handler)
        except PermissionError:
            print("⚠️  Impossible d'écrire dans le fichier de log")

        # Handler pour console (toujours présent)
        console_handler = logging.StreamHandler(sys.stdout)
        console_formatter = logging.Formatter(
            '%(levelname)s: %(message)s'
        )
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)

        return logger

    except Exception as e:
        # Fallback vers logging basique
        print(f"⚠️  Erreur configuration logging: {e}")
        logging.basicConfig(level=logging.INFO)
        return logging.getLogger("PROPFIRM")


# Logger global
logger = setup_logging()


def handle_error(
    error: Exception,
    context: str = "",
    critical: bool = False,
    retry_func: Optional[Callable] = None
) -> bool:
    """Gestion centralisée des erreurs avec options de recovery."""
    error_type = type(error).__name__
    error_msg = str(error)

    # Message d'erreur formaté
    if context:
        full_msg = f"{context}: {error_type} - {error_msg}"
    else:
        full_msg = f"{error_type} - {error_msg}"

    if critical:
        logger.error(f"🔴 ERREUR CRITIQUE: {full_msg}")
        logger.error(f"Traceback:\n{traceback.format_exc()}")
    else:
        logger.warning(f"⚠️  {full_msg}")

    # Tentative de recovery automatique
    if retry_func:
        try:
            logger.info("🔄 Tentative de recovery automatique...")
            retry_func()
            logger.info("✅ Recovery réussie")
            return True
        except Exception as recovery_error:
            logger.error(f"🔴 Échec recovery: {recovery_error}")

    return False


def safe_execute(
    func: Callable,
    *args,
    context: str = "",
    fallback_value: Any = None,
    silent: bool = False,
    **kwargs
) -> Any:
    """Exécution sécurisée d'une fonction avec gestion d'erreurs."""
    try:
        return func(*args, **kwargs)
    except Exception as e:
        if not silent:
            handle_error(e, context=context or f"Exécution de {func.__name__}")
        return fallback_value


def retry_on_failure(max_retries: int = 3, delay: float = 1.0):
    """Décorateur pour retry automatique en cas d'échec."""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None

            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        logger.warning(
                            f"⚠️  Tentative {attempt + 1}/{max_retries} "
                            f"échouée pour {func.__name__}: {e}"
                        )
                        if delay > 0:
                            import time
                            time.sleep(delay)
                    else:
                        logger.error(
                            f"🔴 Toutes les tentatives échouées pour "
                            f"{func.__name__}: {e}"
                        )

            if last_exception:
                raise last_exception

        return wrapper
    return decorator


def validate_config(config: dict, required_keys: list) -> bool:
    """Validation robuste de configuration."""
    missing_keys = []

    for key in required_keys:
        if key not in config:
            missing_keys.append(key)
        elif config[key] is None or config[key] == "":
            missing_keys.append(f"{key} (vide)")

    if missing_keys:
        logger.error(
            f"🔴 Configuration invalide - Clés manquantes: {missing_keys}"
        )
        return False

    logger.info("✅ Configuration validée")
    return True


def create_error_report(error: Exception, context: dict = None) -> dict:
    """Création d'un rapport d'erreur détaillé."""
    import platform

    report = {
        "timestamp": datetime.now().isoformat(),
        "error_type": type(error).__name__,
        "error_message": str(error),
        "traceback": traceback.format_exc(),
        "python_version": sys.version,
        "platform": platform.platform(),
        "working_directory": os.getcwd()
    }

    if context:
        report["context"] = context

    return report


class ErrorHandler:
    """Gestionnaire d'erreurs contextuel pour les classes."""

    def __init__(self, class_name: str):
        self.class_name = class_name
        self.error_count = 0
        self.last_error = None

    def handle(
        self, error: Exception, method: str = "", critical: bool = False
    ):
        """Gestion d'erreur pour une méthode de classe."""
        self.error_count += 1
        self.last_error = error

        context = f"{self.class_name}.{method}" if method else self.class_name
        handle_error(error, context=context, critical=critical)

    def reset_count(self):
        """Reset du compteur d'erreurs."""
        self.error_count = 0
        self.last_error = None

    def is_healthy(self, max_errors: int = 5) -> bool:
        """Vérification de l'état de santé basé sur le nombre d'erreurs."""
        return self.error_count <= max_errors


# Gestionnaires d'erreurs prédéfinis pour types d'erreurs communes
def handle_import_error(
    error: ImportError, package_name: str, fallback_msg: str = ""
):
    """Gestion spécialisée des erreurs d'import."""
    logger.warning(f"⚠️  Package manquant: {package_name}")
    logger.info(f"💡 Installation: pip install {package_name}")
    if fallback_msg:
        logger.info(f"🔄 {fallback_msg}")


def handle_file_error(
    error: Exception, filepath: str, operation: str = "accès"
):
    """Gestion spécialisée des erreurs de fichiers."""
    if isinstance(error, FileNotFoundError):
        logger.warning(f"⚠️  Fichier manquant: {filepath}")
        logger.info("💡 Vérifiez le chemin ou créez le fichier")
    elif isinstance(error, PermissionError):
        logger.error(f"🔴 Permission refusée: {filepath}")
        logger.info("💡 Vérifiez les permissions du fichier/dossier")
    else:
        logger.error(f"🔴 Erreur {operation} fichier {filepath}: {error}")


def handle_data_error(error: Exception, data_source: str):
    """Gestion spécialisée des erreurs de données."""
    logger.warning(f"⚠️  Problème données {data_source}: {error}")
    logger.info("💡 Vérifiez format et intégrité des données")


if __name__ == "__main__":
    # Tests du gestionnaire d'erreurs
    print("🧪 Test du gestionnaire d'erreurs")

    # Test gestion d'erreur basique
    try:
        1 / 0
    except Exception as e:
        handle_error(e, "Test division par zéro")

    # Test exécution sécurisée
    result = safe_execute(
        lambda x: x / 0,
        5,
        context="Test division sécurisée",
        fallback_value="Fallback utilisé"
    )
    print(f"Résultat sécurisé: {result}")

    # Test gestionnaire de classe
    error_handler = ErrorHandler("TestClass")
    try:
        raise ValueError("Test error")
    except Exception as e:
        error_handler.handle(e, "test_method")

    print(f"Santé de la classe: {error_handler.is_healthy()}")
    print("✅ Tests terminés")
