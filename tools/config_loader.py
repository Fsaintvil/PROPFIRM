"""Loader non-invasive pour `config/ai_advanced_config.json`.

Expose des helpers légers : get_config() et get(key, default).
Ne modifie pas les valeurs d'environnement ni n'écrase la configuration runtime.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional


DEFAULT_CONFIG_PATH = Path("config") / "ai_advanced_config.json"


def get_config(path: Optional[str] = None) -> Dict[str, Any]:
    """Charge le fichier JSON de configuration si présent.

    Retourne un dictionnaire vide si le fichier est absent ou invalide.
    Cette fonction est volontairement légère et sans effets de bord.
    """
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    try:
        if not cfg_path.exists():
            return {}
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
            return {}
    except Exception:
        # En cas d'erreur de lecture/parse, retourner un dict vide (non bloquant)
        return {}


def get(key: str, default: Any = None, path: Optional[str] = None) -> Any:
    """Renvoie la valeur de configuration pour la clé donnée, ou default.

    Priorité (lecture seule) : environment variables (PREFIXED) non prises en compte ici —
    l'appelant doit décider de prioriser `os.environ` si besoin.
    """
    cfg = get_config(path=path)
    return cfg.get(key, default)


if __name__ == "__main__":
    # Exécution rapide pour debug local
    import pprint

    print("Chargement config depuis:", str(DEFAULT_CONFIG_PATH))
    pprint.pprint(get_config())
