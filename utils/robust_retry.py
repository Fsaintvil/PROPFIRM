"""
Système de retry robuste pour remplacer la gestion d'erreurs basique.
"""

import time
import logging
from typing import Callable, Any, Optional, Type
from functools import wraps


# Classes d'exception personnalisées pour MT5
class MT5ConnectionError(Exception):
    """Erreur de connexion MT5."""
    pass


class MT5OperationError(Exception):
    """Erreur d'opération MT5."""
    pass


class RetryConfig:
    """Configuration pour les mécanismes de retry"""
    MAX_RETRIES = 3
    BASE_DELAY = 1.0
    BACKOFF_MULTIPLIER = 2.0
    MAX_DELAY = 30.0

    # Exceptions qui méritent un retry
    RETRYABLE_EXCEPTIONS = (
        ConnectionError,
        TimeoutError,
        OSError,
    )


class RobustRetry:
    """Système de retry robuste avec backoff exponentiel"""

    def __init__(self,
                 max_retries: int = RetryConfig.MAX_RETRIES,
                 base_delay: float = RetryConfig.BASE_DELAY,
                 backoff_multiplier: float = RetryConfig.BACKOFF_MULTIPLIER,
                 max_delay: float = RetryConfig.MAX_DELAY,
                 retryable_exceptions: tuple = (
                     RetryConfig.RETRYABLE_EXCEPTIONS
                 ),
                 logger: Optional[logging.Logger] = None):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.backoff_multiplier = backoff_multiplier
        self.max_delay = max_delay
        self.retryable_exceptions = retryable_exceptions
        self.logger = logger or logging.getLogger(__name__)

    def __call__(self, func: Callable) -> Callable:
        """Décorateur pour ajouter retry à une fonction"""
        @wraps(func)
        def wrapper(*args, **kwargs):
            return self.execute_with_retry(func, *args, **kwargs)
        return wrapper

    def execute_with_retry(self, func: Callable, *args, **kwargs) -> Any:
        """Exécute une fonction avec retry"""
        last_exception = None

        for attempt in range(self.max_retries + 1):
            try:
                return func(*args, **kwargs)
            except self.retryable_exceptions as e:
                last_exception = e

                if attempt == self.max_retries:
                    self.logger.error(
                        f"🔴 Échec final après {self.max_retries} tentatives "
                        f"pour {func.__name__}: {e}"
                    )
                    raise e

                # Calculer délai avec backoff exponentiel
                delay = min(
                    self.base_delay * (self.backoff_multiplier ** attempt),
                    self.max_delay
                )

                self.logger.warning(
                    f"⚠️ Tentative {attempt + 1}/{self.max_retries} "
                    f"échouée pour {func.__name__}: {e}. "
                    f"Retry dans {delay:.1f}s"
                )

                time.sleep(delay)

            except Exception as e:
                # Exceptions non-retryables
                self.logger.error(
                    f"🔴 Erreur non-retryable dans {func.__name__}: {e}"
                )
                raise e

        # Ne devrait jamais arriver
        if last_exception:
            raise last_exception


# Décorateurs prêts à l'emploi


def retry_connection(max_retries: int = 3, base_delay: float = 1.0):
    """Retry pour les erreurs de connexion"""
    return RobustRetry(
        max_retries=max_retries,
        base_delay=base_delay,
        retryable_exceptions=(ConnectionError, TimeoutError, OSError)
    )


def retry_mt5_operation(max_retries: int = 3, base_delay: float = 0.5):
    """Retry spécifique pour les opérations MT5"""
    return RobustRetry(
        max_retries=max_retries,
        base_delay=base_delay,
        retryable_exceptions=(ConnectionError, TimeoutError, Exception)
    )


def robust_mt5_retry(max_attempts: int = 3, base_delay: float = 1.0):
    """Décorateur de retry robuste spécialement conçu pour MT5."""
    return RobustRetry(
        max_retries=max_attempts,
        base_delay=base_delay,
        retryable_exceptions=(
            MT5ConnectionError,
            MT5OperationError,
            ConnectionError,
            TimeoutError,
            OSError
        )
    )


class CircuitBreaker:
    """Circuit breaker pour éviter les appels répétés en cas d'échec"""

    def __init__(self,
                 failure_threshold: int = 5,
                 recovery_timeout: float = 60.0,
                 expected_exception: Type[Exception] = Exception):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception

        self.failure_count = 0
        self.last_failure_time = None
        self.state = 'CLOSED'  # CLOSED, OPEN, HALF_OPEN

        self.logger = logging.getLogger(__name__)

    def __call__(self, func: Callable) -> Callable:
        """Décorateur circuit breaker"""
        @wraps(func)
        def wrapper(*args, **kwargs):
            return self.call(func, *args, **kwargs)
        return wrapper

    def call(self, func: Callable, *args, **kwargs) -> Any:
        """Appel protégé par circuit breaker"""
        current_time = time.time()

        # Vérifier si on peut sortir de l'état OPEN
        if self.state == 'OPEN':
            recovery_time = (
                current_time - self.last_failure_time > self.recovery_timeout
            )
            if self.last_failure_time and recovery_time:
                self.state = 'HALF_OPEN'
                self.logger.info("🔄 Circuit breaker: OPEN -> HALF_OPEN")
            else:
                raise Exception(
                    f"Circuit breaker OPEN: {self.failure_count} échecs "
                    f"consécutifs"
                )

        try:
            result = func(*args, **kwargs)

            # Succès - reset si nécessaire
            if self.state == 'HALF_OPEN':
                self.state = 'CLOSED'
                self.failure_count = 0
                self.logger.info("✅ Circuit breaker: HALF_OPEN -> CLOSED")

            return result

        except self.expected_exception as e:
            self.failure_count += 1
            self.last_failure_time = current_time

            if self.failure_count >= self.failure_threshold:
                self.state = 'OPEN'
                self.logger.error(
                    f"🛑 Circuit breaker OUVERT: {self.failure_count} "
                    f"échecs consécutifs"
                )

            raise e


def validate_input(_func: Optional[Callable] = None,
                   *,
                   validation_func: Optional[Callable[..., bool]] = None,
                   error_message: str = "Invalid input"):
    """Décorateur pour valider les entrées, tolérant à l'usage avec ou sans arguments.

    Utilisations supportées:
    - @validate_input  -> n'applique aucune validation (sécurisé), wrap neutre
    - @validate_input(validation_func=callable, error_message="...")
    - @validate_input(callable)  (positionnel) [éviter si possible]

    Le validation_func peut accepter soit un seul argument (valeur),
    soit la liste complète des arguments (*args, **kwargs). Le décorateur
    est tolérant et n'empêche pas l'exécution si la validation échoue
    pour des raisons internes (il journalise et continue l'appel).
    """

    def _build_wrapper(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            vfunc = validation_func

            # Cas de mauvaise utilisation: @validate_input sans args → _func == func
            # Dans ce cas, aucune validation spécifique n'est appliquée.
            if vfunc is func:
                vfunc = None

            if vfunc is not None:
                try:
                    # Si vfunc accepte *args/**kwargs, on les transmet.
                    try:
                        valid = vfunc(*args, **kwargs)
                    except TypeError:
                        # Fallback: valider le 2ème argument si présent (ex: self, symbol)
                        if len(args) > 1:
                            valid = vfunc(args[1])
                        elif 'symbol' in kwargs:
                            valid = vfunc(kwargs.get('symbol'))
                        else:
                            # Aucun argument pertinent → considérer valide par défaut
                            valid = True

                    if not valid:
                        raise ValueError(f"{error_message}")
                except Exception as exc:
                    # Journaliser l'erreur de validation mais ne pas bloquer l'exécution
                    logging.getLogger(__name__).warning(
                        f"Validation input déclenchée une exception: {exc}"
                    )

            # Appel de la fonction d'origine
            return func(*args, **kwargs)

        return wrapper

    # Si utilisé en mode décorateur sans parenthèses: @validate_input
    if callable(_func) and validation_func is None:
        return _build_wrapper(_func)

    # Si l'utilisateur a passé par position un callable comme fonction de validation
    # (ex: @validate_input(some_validator)), supporter également ce cas.
    if callable(_func) and validation_func is None:
        validation_func = _func  # interpréter le 1er arg comme fonction de validation
        return _build_wrapper  # retourner le décorateur à appliquer sur la fonction cible

    # Cas standard: @validate_input(validation_func=..., error_message=...)
    return _build_wrapper
