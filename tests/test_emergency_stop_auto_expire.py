from pathlib import Path
from datetime import datetime, timedelta


def test_emergency_stop_auto_expires(tmp_path: Path):
    # Import tardif pour éviter effets de bord globaux au chargement
    from scripts.live_trading_engine import LiveTradingEngine

    engine = LiveTradingEngine(symbols=["EURUSD"], lot_sizes={"EURUSD": 0.01})

    # Rediriger le chemin du fichier d'arrêt d'urgence vers un dossier temporaire
    control_dir = tmp_path / "control"
    control_dir.mkdir(parents=True, exist_ok=True)
    stop_file = control_dir / "emergency_stop"
    engine.emergency_stop_file = stop_file

    # Créer un fichier d'arrêt avec une date Until dans le passé
    past_until = (datetime.now() - timedelta(minutes=2)).isoformat()
    content = (
        "EMERGENCY_STOP_ACTIVE\n"
        f"Timestamp: {datetime.now().isoformat()}\n"
        "Duration: 1 minutes\n"
        f"Until: {past_until}\n"
        "Status: ACTIVE\n"
    )
    stop_file.write_text(content, encoding="utf-8")

    # L'appel doit constater l'expiration, supprimer le fichier et retourner False
    is_active = engine.check_emergency_stop()
    assert is_active is False
    assert stop_file.exists() is False
