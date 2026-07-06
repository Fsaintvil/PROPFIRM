"""State Manager — accès thread-safe à robot_state.json.

Évite la race condition entre _save_state() (main.py) et
_persist_partial_closed() (trailer.py) qui écrivent toutes deux
dans runtime/robot_state.json depuis des threads différents.

Utilisation:
    from engine_simple.state_manager import save_full_state, update_state_field
    save_full_state(path, data_dict)
    update_state_field(path, field_name, field_value)
"""

import json
import logging
import threading
from pathlib import Path

logger = logging.getLogger("state_manager")

# Lock partagé entre main.py et trailer.py via ce module
_STATE_LOCK = threading.Lock()


def _atomic_write(path: Path, data: dict) -> None:
    """Écriture atomique JSON : temp → rename. Évite la corruption si crash."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, default=str))
    tmp.replace(path)  # atomique sur NTFS


def save_full_state(path: str, data: dict) -> bool:
    """Écrit l'état complet avec lock thread-safe.

    Args:
        path: Chemin vers robot_state.json
        data: Dict complet à persister

    Returns:
        True si succès, False si erreur
    """
    with _STATE_LOCK:
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(p, data)
            return True
        except Exception as e:
            logger.warning(f"save_full_state échoué: {e}")
            return False


def update_state_field(path: str, field: str, value) -> bool:
    """Lit robot_state.json, met à jour UN champ, réécrit avec lock.

    Alternative légère à save_full_state — utilisée par trailer.py
    pour persister partial_closed sans risquer d'écraser les données
    écrites par save_full_state() depuis l'autre thread.

    Args:
        path: Chemin vers robot_state.json
        field: Nom du champ à mettre à jour
        value: Valeur à écrire

    Returns:
        True si succès, False si erreur
    """
    with _STATE_LOCK:
        try:
            p = Path(path)
            if p.exists():
                data = json.loads(p.read_text())
            else:
                data = {}
            data[field] = value
            p.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(p, data)
            return True
        except Exception as e:
            logger.debug(f"update_state_field({field}) échoué: {e}")
            return False


def read_state(path: str) -> dict:
    """Lit robot_state.json avec lock (lecture seule, lock pour cohérence).

    Args:
        path: Chemin vers robot_state.json

    Returns:
        Dict de l'état, ou dict vide si erreur
    """
    with _STATE_LOCK:
        try:
            p = Path(path)
            if p.exists():
                return json.loads(p.read_text())
        except Exception as e:
            logger.debug(f"read_state échoué: {e}")
        return {}
