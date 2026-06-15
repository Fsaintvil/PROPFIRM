import logging
from datetime import datetime, timedelta

import numpy as np

logger = logging.getLogger("sessions")

SESSIONS = {
    "asia": (0, 9),
    "london_open": (7, 10),
    "london": (7, 16),
    "ny_open": (12, 15),
    "new_york": (12, 21),
    "london_ny": (12, 16),
    "all": (0, 24),
}

KILLZONES = {
    "london_killzone": (7, 9),
    "ny_killzone": (13, 15),
    "london_close": (15, 17),
    "asia_killzone": (1, 4),
}


def get_current_session():
    now = datetime.utcnow()
    hour = now.hour
    for name, (start, end) in SESSIONS.items():
        if start <= hour < end:
            return name
    return "off_hours"


def get_active_killzone():
    now = datetime.utcnow()
    hour = now.hour
    for name, (start, end) in KILLZONES.items():
        if start <= hour < end:
            return name
    return None


def is_session_active(session_name, current_hour=None):
    if current_hour is None:
        current_hour = datetime.utcnow().hour
    start, end = SESSIONS.get(session_name, (0, 0))
    return start <= current_hour < end


def session_proximity_weight(current_hour=None):
    if current_hour is None:
        current_hour = datetime.utcnow().hour
    london_ny_start, london_ny_end = SESSIONS["london_ny"]
    if london_ny_start <= current_hour < london_ny_end:
        return 1.0
    dist = min(abs(current_hour - london_ny_start), abs(current_hour - london_ny_end),
               abs(current_hour - (london_ny_start - 24)), abs(current_hour - (london_ny_end + 24)))
    return max(0, 1.0 - dist * 0.15)


def session_highs_lows(high, low, timestamps, lookback_hours=24):
    h = np.asarray(high, dtype=float)
    lo = np.asarray(low, dtype=float)
    t = np.asarray(timestamps)
    now = datetime.utcnow()
    cutoff = now - timedelta(hours=lookback_hours)
    cutoff_ts = cutoff.timestamp()
    mask = t >= cutoff_ts
    if not np.any(mask):
        return {}
    recent_h = h[mask]
    recent_l = lo[mask]
    return {
        "session_high": np.max(recent_h),
        "session_low": np.min(recent_l),
        "session_range": np.max(recent_h) - np.min(recent_l),
        "is_near_high": recent_h[-1] >= np.max(recent_h) * 0.98 if len(recent_h) > 0 else False,
        "is_near_low": recent_l[-1] <= np.min(recent_l) * 1.02 if len(recent_l) > 0 else False,
    }


def killzone_highs_lows(high, low, timestamps, killzone_name, days_back=3):
    h = np.asarray(high, dtype=float)
    lo = np.asarray(low, dtype=float)
    t = np.asarray(timestamps)
    kz_start, kz_end = KILLZONES.get(killzone_name, (0, 0))
    now = datetime.utcnow()
    mask = np.zeros(len(t), dtype=bool)
    for d in range(days_back + 1):
        day_start = now - timedelta(days=d)
        day_start = day_start.replace(hour=kz_start, minute=0, second=0, microsecond=0)
        day_end = day_start.replace(hour=kz_end, minute=0, second=0, microsecond=0)
        ts_start = day_start.timestamp()
        ts_end = day_end.timestamp()
        mask |= (t >= ts_start) & (t < ts_end)
    if not np.any(mask):
        return {}
    kz_h = h[mask]
    kz_l = lo[mask]
    return {
        "killzone_high": np.max(kz_h) if len(kz_h) > 0 else None,
        "killzone_low": np.min(kz_l) if len(kz_l) > 0 else None,
    }


def analyze_sessions(high, low, close, timestamps):
    current = get_current_session()
    weight = session_proximity_weight()
    sl = session_highs_lows(high, low, timestamps)
    active_killzone = get_active_killzone()
    kz_hl = {}
    if active_killzone:
        kz_hl = killzone_highs_lows(high, low, timestamps, active_killzone)

    c = np.asarray(close, dtype=float)
    result = {
        "current_session": current,
        "active_killzone": active_killzone,
        "session_weight": weight,
    }
    result.update(sl)
    result.update(kz_hl)

    if sl.get("session_high") and sl.get("session_low") and sl["session_high"] > sl["session_low"]:
        position = (c[-1] - sl["session_low"]) / (sl["session_high"] - sl["session_low"])
        result["session_position"] = float(position)
        if position > 0.8:
            result["session_bias"] = "premium"
        elif position < 0.2:
            result["session_bias"] = "discount"
        else:
            result["session_bias"] = "neutral"
    else:
        result["session_position"] = 0.5
        result["session_bias"] = "neutral"

    return result
