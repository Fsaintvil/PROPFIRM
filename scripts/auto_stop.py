#!/usr/bin/env python3
"""
Auto-Stop/Resume automatique pour le robot MT5.

Décision basée sur l'ADX moyen des symboles actifs :
  - ADX moyen < 22 sur >50% des symboles pendant >30 min → STOP
  - ADX moyen >= 22 sur >=3 symboles → RESUME

Stockage dans runtime/auto_state.json :
  - auto_paused : bool
  - auto_paused_at : timestamp ISO
  - auto_paused_until : timestamp ISO (30 min mini)
  - adx_check : dernier snapshot ADX

Appelé par le robot à chaque cycle (15s) via ftmo_protector.py.
Peut aussi être appelé standalone : python scripts/auto_stop.py
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

# Ajouter la racine au PYTHONPATH
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import config_simple as cfg
    from engine_simple.mt5_connector import get_rates
except ImportError:
    cfg = None
    get_rates = None

logger = logging.getLogger("auto_stop")

RUNTIME = Path(__file__).parent.parent / "runtime"
STATE_FILE = RUNTIME / "auto_state.json"

# Seuils
ADX_LOW_THRESHOLD = 22        # ADX < 22 = RANGING
ADX_HIGH_THRESHOLD = 22       # ADX >= 22 = TRENDING (pour reprise)
RATIO_STOP = 0.50             # >50% des symboles en ranging → STOP
SYMBOLS_MIN_RESUME = 3        # >=3 symboles avec ADX >= 22 → RESUME
PAUSE_MIN_DURATION = 1800     # 30 min de pause minimum
ADX_SNAPSHOT_TTL = 300        # snapshot ADX valide 5 min
STATE_TTL = 86400             # state max 24h (reset auto)


def compute_adx(high_arr, low_arr, close_arr, period=14):
    """Calcule ADX simplifié sur un array de prix."""
    if len(close_arr) < period + 2:
        return 0.0
    high = np.array(high_arr, dtype=np.float64)
    low = np.array(low_arr, dtype=np.float64)
    close = np.array(close_arr, dtype=np.float64)

    up_move = np.diff(high)
    down_move = -np.diff(low)
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr = np.maximum(
        high[1:] - low[1:],
        np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1]))
    )
    if len(tr) < period:
        return 0.0
    atr = np.mean(tr[-period:])
    if atr <= 0:
        return 0.0
    avg_plus = np.mean(plus_dm[-period:])
    avg_minus = np.mean(minus_dm[-period:])
    di_plus = 100.0 * avg_plus / atr
    di_minus = 100.0 * avg_minus / atr
    dx = 100.0 * abs(di_plus - di_minus) / (di_plus + di_minus) if (di_plus + di_minus) > 0 else 0
    return dx


def load_state():
    """Charge l'état auto depuis STATE_FILE."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "auto_paused": False,
        "auto_paused_at": None,
        "auto_paused_until": None,
        "adx_snapshot": {},
        "adx_snapshot_ts": 0.0,
    }


def save_state(state):
    """Sauvegarde l'état auto."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2, default=str))
    except OSError as e:
        logger.error(f"Erreur sauvegarde auto_state: {e}")


def check_adx():
    """Check ADX sur tous les symboles actifs. Retourne (ratio_low, total, details)."""
    if cfg is None:
        return 0.0, 0, {}

    symbols = cfg.SYMBOLS
    low_count = 0
    total = 0
    details = {}

    for sym in symbols:
        try:
            rates = get_rates(sym, "H1", 30)
            if rates is None or len(rates) < 26:
                continue
            high = [r[2] for r in rates[-26:]]
            low = [r[3] for r in rates[-26:]]
            close = [r[4] for r in rates[-26:]]
            adx_val = compute_adx(high, low, close)
            total += 1
            is_low = adx_val < ADX_LOW_THRESHOLD
            if is_low:
                low_count += 1
            details[sym] = {"adx": round(adx_val, 1), "low": is_low}
        except Exception:
            continue

    ratio = low_count / max(total, 1)
    return ratio, total, details


def decision(force_check=False):
    """
    Retourne le verdict auto_stop :
      "STOP"  → arrêter le trading
      "RESUME" → reprendre le trading
      "NOOP"  → pas de changement
      "WAIT"  → en pause, attendre la fin

    Stocke la décision dans STATE_FILE.
    """
    state = load_state()
    now = time.time()

    # Nettoyage state si trop vieux
    paused_at = state.get("auto_paused_at")
    if paused_at:
        try:
            paused_ts = datetime.fromisoformat(paused_at).timestamp()
            if now - paused_ts > STATE_TTL:
                state["auto_paused"] = False
                state["auto_paused_until"] = None
                state["auto_paused_at"] = None
        except (ValueError, TypeError):
            state["auto_paused"] = False

    # Si déjà en pause, vérifier si la pause est finie
    if state.get("auto_paused"):
        pause_until = state.get("auto_paused_until")
        if pause_until:
            try:
                until_ts = datetime.fromisoformat(pause_until).timestamp()
                if now < until_ts:
                    save_state(state)
                    return "WAIT", state
            except (ValueError, TypeError):
                pass

        # Pause finie → vérifier si on peut reprendre
        ratio, total, details = check_adx()
        state["adx_snapshot"] = details
        state["adx_snapshot_ts"] = now

        if total == 0:
            return "NOOP", state

        # Compter les symboles avec ADX >= 22
        ok_symbols = sum(1 for d in details.values() if not d["low"])
        if ok_symbols >= SYMBOLS_MIN_RESUME:
            state["auto_paused"] = False
            state["auto_paused_until"] = None
            state["auto_paused_at"] = None
            save_state(state)
            logger.info(f"✅ RESUME: {ok_symbols} symboles ADX>=22 → reprise trading")
            return "RESUME", state
        else:
            # Pas assez de symboles OK → prolonger la pause de 15 min
            new_until = datetime.utcnow() + timedelta(minutes=15)
            state["auto_paused_until"] = new_until.isoformat()
            save_state(state)
            return "WAIT", state

    # Pas en pause → vérifier si on doit stopper
    # Vérifier le cache ADX
    snapshot_ts = state.get("adx_snapshot_ts", 0)
    if force_check or (now - snapshot_ts > ADX_SNAPSHOT_TTL):
        ratio, total, details = check_adx()
        state["adx_snapshot"] = details
        state["adx_snapshot_ts"] = now
        save_state(state)
    else:
        ratio = sum(1 for d in state.get("adx_snapshot", {}).values() if d.get("low")) / max(len(state.get("adx_snapshot", {})), 1)
        total = len(state.get("adx_snapshot", {}))
        details = state.get("adx_snapshot", {})

    if total == 0:
        return "NOOP", state

    if ratio >= RATIO_STOP:
        # STOP le trading
        pause_until = datetime.utcnow() + timedelta(seconds=PAUSE_MIN_DURATION)
        state["auto_paused"] = True
        state["auto_paused_at"] = datetime.utcnow().isoformat()
        state["auto_paused_until"] = pause_until.isoformat()
        save_state(state)
        low_str = ", ".join(f"{s}={d['adx']}" for s, d in details.items() if d["low"])
        logger.warning(f"🛑 STOP: {ratio:.0%} symboles ADX<22 → pause 30min. Bas: {low_str}")
        return "STOP", state

    return "NOOP", state


def main():
    """Mode standalone : diagnostic et décision."""
    print("=== AUTO-STOP DIAGNOSTIC ===")
    print()

    verdict, state = decision(force_check=True)
    print(f"Verdict: {verdict}")
    print(f"Auto-paused: {state.get('auto_paused')}")

    if state.get("auto_paused_until"):
        print(f"Pause until: {state.get('auto_paused_until')}")

    print()
    print("=== ADX SNAPSHOT ===")
    details = state.get("adx_snapshot", {})
    for sym, d in sorted(details.items()):
        status = "🔴 LOW" if d["low"] else "✅ OK"
        print(f"  {sym}: ADX={d['adx']:6.1f}  {status}")

    if details:
        low_count = sum(1 for d in details.values() if d["low"])
        total = len(details)
        print(f"\n  {low_count}/{total} symboles avec ADX < {ADX_LOW_THRESHOLD}")
        print(f"  Ratio: {low_count/max(total,1):.0%}")
        print(f"  Seuil stop: >{RATIO_STOP:.0%}")

    print()
    print(f"Fichier état: {STATE_FILE}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    main()
