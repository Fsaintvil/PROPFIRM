import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("news")

# Les deux APIs (TradingView + ForexFactory) retournent 403 permanent.
# Le filtre est désactivé au premier échec — aucune requête HTTP répétée.
# Fallback: calendrier locale des événements prévisibles (NFP, FOMC, CPI).
_NEWS_DISABLED = False
_LAST_DISABLED_CHECK = 0
CACHE_FILE = "runtime/news_cache.json"
CACHE_TTL = 3600
BLOCK_MINUTES_BEFORE = 10
BLOCK_MINUTES_AFTER = 10


def _generate_events():
    """Calendrier offline des événements à haut-impact prévisibles.
    Retourne une liste d'événements générés pour les 30 prochains jours."""
    events = []
    now = datetime.utcnow()
    year, month = now.year, now.month

    for m_offset in range(3):  # 3 mois
        y = year + (month + m_offset - 1) // 12
        m = (month + m_offset - 1) % 12 + 1
        # NFP: 1er vendredi du mois, 12:30 UTC
        first_day = datetime(y, m, 1, tzinfo=None)
        days_to_friday = (4 - first_day.weekday()) % 7
        nfp_date = first_day + timedelta(days=days_to_friday)
        if nfp_date.day > 7:
            nfp_date -= timedelta(days=7)  # parfois 2eme vendredi
        nfp_event = nfp_date.replace(hour=12, minute=30)
        if now <= nfp_event <= now + timedelta(days=30):
            events.append({"name": "NFP", "time": nfp_event, "impact": "high", "is_generated": True})

        # CPI: entre le 10 et 16 du mois (varie, on prend le milieu)
        cpi_base = datetime(y, m, 13, hour=12, minute=30, tzinfo=None)
        if now <= cpi_base <= now + timedelta(days=30):
            events.append({"name": "CPI", "time": cpi_base, "impact": "high", "is_generated": True})

        # FOMC approximatif: 8 fois/an, semaines avec 3eme mercredi du mois
        # Mois typiques: jan, mar, mai, jun, jul, sep, nov, dec
        fomc_months = [1, 3, 5, 6, 7, 9, 11, 12]
        if m in fomc_months:
            first_day = datetime(y, m, 1, tzinfo=None)
            days_to_wed = (2 - first_day.weekday()) % 7
            third_wed = first_day + timedelta(days=days_to_wed + 14)  # 3eme mercredi
            fomc_event = third_wed.replace(hour=18, minute=0)  # 14:00 ET = 18:00 UTC
            if now <= fomc_event <= now + timedelta(days=30):
                events.append({"name": "FOMC", "time": fomc_event, "impact": "high", "is_generated": True})

    return events






def fetch_calendar():
    global _NEWS_DISABLED, _LAST_DISABLED_CHECK
    if _NEWS_DISABLED:
        now = time.time()
        if now - _LAST_DISABLED_CHECK < 86400:
            return []
        _NEWS_DISABLED = False  # retry after 24h

    events = []
    try:
        cache_path = Path(CACHE_FILE)
        if cache_path.exists():
            age = time.time() - cache_path.stat().st_mtime
            if age < CACHE_TTL:
                with open(cache_path) as f:
                    cached = json.load(f)
                events = cached.get("events", [])
                for e in events:
                    if isinstance(e.get("time"), str):
                        e["time"] = datetime.fromisoformat(e["time"])
                if events:
                    return events
    except Exception as e:
        logger.debug(f"News cache read error: {e}")

    # Try web API
    try:
        import requests
        today = datetime.utcnow().strftime("%Y-%m-%d")
        url = (f"https://economic-calendar.tradingview.com/events"
               f"?dateFrom={today}&dateTo={today}")
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            for item in data:
                impact = item.get("impact", "").lower()
                if impact in ("high", "h"):
                    t = item.get("date", "")
                    name = item.get("name", "Unknown")
                    if t:
                        dt = datetime.fromisoformat(t.replace("Z", "+00:00")).replace(tzinfo=None)
                        events.append({"name": name, "time": dt, "impact": "high", "is_generated": False})
            if events:
                _save_cache(events)
                return events
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"TradingView fetch failed (403 permanent): {e}")

    try:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        url = f"https://calendar.forexfactory.com/calendar?date={today}"
        resp = requests.get(url, timeout=5,
                            headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code == 200:
            text = resp.text
            import re
            pattern = (r'<tr[^>]*data-event-id[^>]*>.*?'
                       r'<td[^>]*class="[^"]*importance--high[^"]*".*?</tr>')
            matches = re.findall(pattern, text, re.DOTALL)
            for match in matches:
                time_match = re.search(r'data-time="(\d+)"', match)
                name_match = re.search(r'<span class="calendar__event-title">(.*?)</span>', match)
                if time_match and name_match:
                    ts = int(time_match.group(1))
                    name = name_match.group(1).strip()
                    dt = datetime.fromtimestamp(ts)
                    events.append({"name": name, "time": dt, "impact": "high", "is_generated": False})
            if events:
                _save_cache(events)
                return events
    except (ImportError, Exception) as e:
        logger.warning(f"Forexfactory fetch failed (403 permanent): {e}")

    # Les deux APIs retournent 403 permanent — fallback sur calendrier local.
    fallback = _generate_events()
    if fallback:
        logger.info(f"NEWS FILTER: Fallback calendrier local ({len(fallback)} evenements generes)")
        _save_cache(fallback)
        return fallback
    logger.warning("NEWS FILTER COMPLETELY DISABLED: aucun evenement genere.")
    _NEWS_DISABLED = True
    _LAST_DISABLED_CHECK = time.time()
    return []


def _save_cache(events):
    try:
        cache_dir = os.path.dirname(CACHE_FILE)
        if cache_dir and not os.path.exists(cache_dir):
            os.makedirs(cache_dir, exist_ok=True)
        serializable = []
        for e in events:
            ev = dict(e)
            if isinstance(ev.get("time"), datetime):
                ev["time"] = ev["time"].isoformat()
            serializable.append(ev)
        with open(CACHE_FILE, "w") as f:
            json.dump({"events": serializable, "cached_at": datetime.utcnow().isoformat()}, f)
    except Exception as e:
        logger.debug(f"News cache write error: {e}")


def is_news_blocked(utc_now=None):
    if utc_now is None:
        utc_now = datetime.utcnow()
    events = fetch_calendar()
    if not events:
        return False, []
    # Filtre les événements expirés (block window déjà passée)
    now = utc_now or datetime.utcnow()
    events = [e for e in events if e.get("time") is None or
              e["time"] + timedelta(minutes=BLOCK_MINUTES_AFTER) >= now]
    if not events:
        return False, []
    blocked = []
    for e in events:
        dt = e.get("time")
        if dt is None:
            continue
        if isinstance(dt, str):
            try:
                dt = datetime.fromisoformat(dt)
            except ValueError:
                continue
        start_block = dt - timedelta(minutes=BLOCK_MINUTES_BEFORE)
        end_block = dt + timedelta(minutes=BLOCK_MINUTES_AFTER)
        if start_block <= utc_now <= end_block:
            name = e.get("name", "Unknown")
            blocked.append({"name": name, "event_time": dt, "block_until": end_block})
    if blocked:
        reasons = " | ".join(f"{b['name']} (blocked until {b['block_until'].strftime('%H:%M')} UTC)" for b in blocked)
        logger.info(f"  [NEWS] Blocked: {reasons}")
    return len(blocked) > 0, blocked
