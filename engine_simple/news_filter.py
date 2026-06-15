"""
News filter — bloque les trades avant/après les événements économiques à haut impact.

Règle FTMO : pas de nouveau trade 2 minutes avant et 2 minutes après
les événements économiques programmés à haut impact.

Sources de données (par ordre de priorité) :
1. Fichier manuel config/economic_events.json (précision maximale)
2. Calendrier offline généré par règle (NFP, CPI, FOMC, etc.)
3. Cache local runtime/news_cache.json
"""
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("news")

# ============================================================
# Configuration — depuis config_simple (lui-même issu de config/*.yaml)
# ============================================================
from config_simple import NEWS_MINUTES_BEFORE as BLOCK_MINUTES_BEFORE, NEWS_MINUTES_AFTER as BLOCK_MINUTES_AFTER

CACHE_FILE = "runtime/news_cache.json"
CACHE_TTL = 3600           # 1h de cache
MANUAL_EVENTS_FILE = "config/economic_events.json"

# H-02: Flag thread-safe — évite race condition sur les mutables globaux
_NEWS_DISABLED = False
_LAST_DISABLED_CHECK = 0.0
_NEWS_LOCK = threading.Lock()


# ============================================================
# Calendrier offline complet — événements à haut impact
# ============================================================
def _generate_events():
    """Génère les événements à haut impact prévisibles pour les 30 prochains jours.
    
    Couvre les 5 devises de nos paires (USD, GBP, EUR, CAD, CHF)
    avec leurs événements macroéconomique majeurs.
    """
    events = []
    now = datetime.utcnow()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = today + timedelta(days=45)

    for y, m in _months_ahead(now, 3):
        # ========== USD ==========
        # NFP: 1er vendredi du mois, 12:30 UTC
        _try_add(events, _nfp_event(y, m), now, end)

        # CPI US: entre 10-16 du mois, 12:30 UTC.
        # Jamais le week-end — si le 13 tombe un samedi, prendre vendredi 12 ;
        # si dimanche, prendre lundi 14.
        _try_add(events, _us_cpi_event(y, m), now, end)

        # Retail Sales: ~15, 12:30 UTC
        _try_add(events, _day_of_month_event(y, m, 15, 12, 30, "Retail Sales (US)"), now, end)

        # Industrial Production: ~15, 13:15 UTC
        _try_add(events, _day_of_month_event(y, m, 15, 13, 15, "Industrial Production (US)"), now, end)

        # Existing Home Sales: ~22, 14:00 UTC
        _try_add(events, _day_of_month_event(y, m, 22, 14, 0, "Existing Home Sales (US)"), now, end)

        # Durable Goods Orders: ~26, 12:30 UTC
        _try_add(events, _day_of_month_event(y, m, 26, 12, 30, "Durable Goods Orders (US)"), now, end)

        # Michigan Consumer Sentiment: 2nd Friday, 14:00 UTC
        _try_add(events, _nth_weekday_event(y, m, 2, 4, 14, 0, "Michigan Consumer Sentiment (US)"), now, end)

        # ISM Manufacturing PMI: 1er jour ouvré, 14:00 UTC
        _try_add(events, _first_business_day_event(y, m, 14, 0, "ISM Manufacturing PMI (US)"), now, end)

        # ISM Services PMI: 3ème jour ouvré, 14:00 UTC
        _try_add(events, _nth_business_day_event(y, m, 3, 14, 0, "ISM Services PMI (US)"), now, end)

        # PCE Price Index: ~30, 12:30 UTC
        _try_add(events, _day_of_month_event(y, m, 30, 12, 30, "PCE Price Index (US)"), now, end)

        # FOMC: 8 fois/an (jan, mar, mai, jun, jul, sep, nov, dec)
        # 3ème mercredi du mois, 18:00 UTC (14:00 ET)
        if m in [1, 3, 5, 6, 7, 9, 11, 12]:
            _try_add(events, _nth_weekday_event(y, m, 3, 2, 18, 0, "FOMC Rate Decision"), now, end)

        # FOMC Minutes: ~3 semaines après FOMC
        if m in [1, 3, 5, 6, 7, 9, 11, 12]:
            fomc_date = _get_nth_weekday(y, m, 3, 2)
            if fomc_date:
                minutes_date = fomc_date + timedelta(days=21)
                if now <= minutes_date.replace(hour=18, minute=0) <= end:
                    events.append({
                        "name": "FOMC Minutes", "impact": "high",
                        "time": minutes_date.replace(hour=18, minute=0),
                        "is_generated": True
                    })

        # ========== GBP ==========
        # UK CPI: ~20, 06:00 UTC
        _try_add(events, _day_of_month_event(y, m, 20, 6, 0, "CPI (UK)"), now, end)

        # UK Unemployment: ~15, 06:00 UTC
        _try_add(events, _day_of_month_event(y, m, 15, 6, 0, "Unemployment (UK)"), now, end)

        # UK Retail Sales: ~20, 06:00 UTC
        _try_add(events, _day_of_month_event(y, m, 20, 6, 0, "Retail Sales (UK)"), now, end)

        # UK GDP (monthly): ~15, 06:00 UTC
        _try_add(events, _day_of_month_event(y, m, 15, 6, 0, "GDP (UK)"), now, end)

        # ========== EUR ==========
        # EU CPI: ~17, 09:00 UTC
        _try_add(events, _day_of_month_event(y, m, 17, 9, 0, "CPI (EU)"), now, end)

        # German ZEW: ~15, 09:00 UTC
        _try_add(events, _day_of_month_event(y, m, 15, 9, 0, "ZEW (Germany)"), now, end)

        # German IFO: ~25, 08:00 UTC
        _try_add(events, _day_of_month_event(y, m, 25, 8, 0, "IFO (Germany)"), now, end)

        # ECB: 8 fois/an (variable — approximation 2ème jeudi)
        # 12:15 UTC
        _try_add(events, _nth_weekday_event(y, m, 2, 3, 12, 15, "ECB Rate Decision"), now, end)

        # ========== CAD ==========
        # Canada CPI: ~20, 12:30 UTC
        _try_add(events, _day_of_month_event(y, m, 20, 12, 30, "CPI (Canada)"), now, end)

        # Canada Employment: ~10, 12:30 UTC
        _try_add(events, _day_of_month_event(y, m, 10, 12, 30, "Employment (Canada)"), now, end)

        # Canada GDP: ~30, 12:30 UTC
        _try_add(events, _day_of_month_event(y, m, 30, 12, 30, "GDP (Canada)"), now, end)

        # Canada Retail Sales: ~22, 12:30 UTC
        _try_add(events, _day_of_month_event(y, m, 22, 12, 30, "Retail Sales (Canada)"), now, end)

        # BOC: 8 fois/an (variable — approximation 3ème mercredi)
        # 14:00 UTC
        _try_add(events, _nth_weekday_event(y, m, 3, 2, 14, 0, "BOC Rate Decision"), now, end)

        # ========== CHF ==========
        # SNB: trimestriel (mar, jun, sep, dec) ~15, 07:30 UTC
        if m in [3, 6, 9, 12]:
            _try_add(events, _day_of_month_event(y, m, 15, 7, 30, "SNB Rate Decision"), now, end)

        # Swiss CPI: ~3, 06:30 UTC
        _try_add(events, _day_of_month_event(y, m, 3, 6, 30, "CPI (Switzerland)"), now, end)

        # ========== TRANSVERSAL ==========
        # NFP est déjà ajouté plus haut

    return events


def _months_ahead(from_dt, count):
    """Génère les tuples (year, month) pour les `count` mois à venir."""
    y, m = from_dt.year, from_dt.month
    for i in range(count):
        yy = y + (m + i - 1) // 12
        mm = (m + i - 1) % 12 + 1
        yield yy, mm


def _try_add(events, event, now, end):
    """Ajoute l'événement s'il tombe dans la fenêtre [now, end]."""
    if event is not None:
        t = event.get("time")
        if isinstance(t, datetime) and now <= t <= end:
            events.append(event)


def _nfp_event(year, month):
    """NFP = 1er vendredi du mois, 12:30 UTC. Si le 1er vendredi est trop tôt (jour <= 2),
    prend le 2ème vendredi (cas où le 1er du mois est vendredi)."""
    first = datetime(year, month, 1)
    days_to_friday = (4 - first.weekday()) % 7
    first_friday = first + timedelta(days=days_to_friday)
    # Si le 1er vendredi est le jour 1 ou 2 du mois, c'est probablement le reporting
    # du mois précédent — prendre le 2ème vendredi.
    if first_friday.day <= 2:
        first_friday += timedelta(days=7)
    return {
        "name": "NFP (US)", "impact": "high",
        "time": first_friday.replace(hour=12, minute=30),
        "is_generated": True
    }


def _us_cpi_event(year, month):
    """CPI US — entre le 10 et le 16 du mois, 12:30 UTC.
    
    Le CPI n'est JAMAIS publié le week-end. Si le 13 tombe un :
    - Samedi (weekday=5) → prendre vendredi 12
    - Dimanche (weekday=6) → prendre lundi 14
    - Sinon → garder le 13
    """
    day = 13
    dt = datetime(year, month, min(day, 28), 12, 30)
    while dt.day < day and dt.month == month:
        dt += timedelta(days=1)
    # Ajustement week-end
    if dt.weekday() == 5:   # samedi → vendredi
        dt -= timedelta(days=1)
    elif dt.weekday() == 6:  # dimanche → lundi
        dt += timedelta(days=1)
    return {
        "name": "CPI (US)", "impact": "high",
        "time": dt, "is_generated": True
    }


def _day_of_month_event(year, month, day, hour, minute, name):
    """Événement à date fixe (approx)."""
    try:
        dt = datetime(year, month, min(day, 28), hour, minute)
        # Avance jusqu'au jour valide si le mois n'a pas assez de jours
        while dt.day < day and dt.month == month:
            dt += timedelta(days=1)
        return {"name": name, "impact": "high", "time": dt, "is_generated": True}
    except (ValueError, OverflowError):
        return None


def _get_nth_weekday(year, month, n, weekday):
    """Retourne le n-ème jour de la semaine (0=lun, 6=dim) du mois."""
    first = datetime(year, month, 1)
    days_to_target = (weekday - first.weekday()) % 7
    target = first + timedelta(days=days_to_target + (n - 1) * 7)
    if target.month != month:
        return None
    return target


def _nth_weekday_event(year, month, n, weekday, hour, minute, name):
    """Événement au n-ème jour de la semaine du mois."""
    dt = _get_nth_weekday(year, month, n, weekday)
    if dt is None:
        return None
    return {
        "name": name, "impact": "high",
        "time": dt.replace(hour=hour, minute=minute),
        "is_generated": True
    }


def _first_business_day_event(year, month, hour, minute, name):
    """1er jour ouvré du mois (pas samedi/dimanche)."""
    d = datetime(year, month, 1)
    while d.weekday() >= 5:  # samedi=5, dimanche=6
        d += timedelta(days=1)
    return {
        "name": name, "impact": "high",
        "time": d.replace(hour=hour, minute=minute),
        "is_generated": True
    }


def _nth_business_day_event(year, month, n, hour, minute, name):
    """n-ème jour ouvré du mois."""
    count = 0
    d = datetime(year, month, 1)
    while d.month == month:
        if d.weekday() < 5:
            count += 1
            if count == n:
                return {
                    "name": name, "impact": "high",
                    "time": d.replace(hour=hour, minute=minute),
                    "is_generated": True
                }
        d += timedelta(days=1)
    return None


# ============================================================
# Calendrier manuel (JSON) — pour les dates précises
# ============================================================
def _load_manual_events():
    """Charge les événements depuis le fichier JSON manuel (override)."""
    try:
        path = Path(MANUAL_EVENTS_FILE)
        if not path.exists():
            return []
        with open(path) as f:
            data = json.load(f)
        events = data.get("events", [])
        parsed = []
        for e in events:
            if e.get("impact", "").lower() in ("high", "h"):
                t = e.get("time", "")
                if t:
                    dt = datetime.fromisoformat(t)
                    parsed.append({
                        "name": e.get("name", "Unknown"),
                        "impact": "high",
                        "time": dt,
                        "is_generated": False,
                        "manual": True
                    })
        if parsed:
            logger.info(f"NEWS: {len(parsed)} evenements manuels charges depuis {MANUAL_EVENTS_FILE}")
        return parsed
    except Exception as e:
        logger.warning(f"Manual events load error: {e}")
        return []


# ============================================================
# Cache local
# ============================================================
def _save_cache(events):
    """Sauvegarde les événements dans le cache local."""
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
            json.dump({"events": serializable,
                       "cached_at": datetime.utcnow().isoformat(),
                       "count": len(serializable)}, f)
    except Exception as e:
        logger.warning(f"News cache write error: {e}")


def _load_cache():
    """Charge les événements depuis le cache local (si encore frais)."""
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
                    # Log le cache toutes les 60s max (évite bruit — 5-8x/cycle sinon)
                    _last_news_log = getattr(_load_cache, '_last_log', 0)
                    if time.time() - _last_news_log > 60:
                        logger.debug(f"News cache: {len(events)} evenements (age={age:.0f}s)")
                        _load_cache._last_log = time.time()
                    return events
    except Exception as e:
        logger.warning(f"News cache load error: {e}")
    return None


# ============================================================
# API de fetch (désactivée — 403 permanent)
# ============================================================
def _try_fetch_api():
    """Tente de récupérer le calendrier via API web.
    Les deux APIs connues (TradingView, ForexFactory) retournent 403 permanent.
    Cette fonction est conservée pour compatibilité mais ne sera pas utilisée."""
    return None


# ============================================================
# Orchestrateur des sources
# ============================================================
def fetch_calendar():
    """Récupère le calendrier des événements à haut-impact.
    
    Ordre de priorité:
    1. Cache local (si frais)
    2. Fichier manuel JSON (config/economic_events.json)
    3. Calendrier offline généré par règle
    4. APIs web (désactivé — 403 permanent)
    """
    global _NEWS_DISABLED, _LAST_DISABLED_CHECK

    with _NEWS_LOCK:  # H-02: protection thread-safe
        if _NEWS_DISABLED:
            now = time.time()
            if now - _LAST_DISABLED_CHECK < 86400:
                return []
            _NEWS_DISABLED = False  # retry après 24h

    # 1. Cache
    cached = _load_cache()
    if cached:
        return cached

    # 2. Fichier manuel
    manual = _load_manual_events()

    # 3. Calendrier offline
    generated = _generate_events()

    # Fusion: manuel d'abord + généré (le manuel override si doublon)
    seen_names = set()
    merged = []

    for e in manual + generated:
        key = (e["name"], e["time"].isoformat() if isinstance(e["time"], datetime) else str(e.get("time")))
        if key not in seen_names:
            seen_names.add(key)
            merged.append(e)

    merged.sort(key=lambda x: x["time"] if isinstance(x.get("time"), datetime) else datetime.max)

    if merged:
        logger.info(f"NEWS FILTER: {len(merged)} evenements (manuel={len(manual)}, genere={len(generated)})")
        _save_cache(merged)
        return merged

    logger.warning("NEWS FILTER: AUCUN evenement genere — filtre desactive temporairement.")
    with _NEWS_LOCK:
        _NEWS_DISABLED = True
        _LAST_DISABLED_CHECK = time.time()
    return []


# ============================================================
# Vérification du blocage
# ============================================================
def is_news_blocked(utc_now=None, symbol=None):
    """Vérifie si un trade est bloqué par le filtre news.
    
    Args:
        utc_now: datetime UTC (pour les tests)
        symbol: symbole à vérifier (utilise ses news_minutes_before/after si fourni)
    
    Returns:
        (bool, list) — (bloqué ou non, détails des événements bloquants)
    """
    if utc_now is None:
        utc_now = datetime.utcnow()

    events = fetch_calendar()
    if not events:
        return False, []

    # Per-symbol news block times
    block_before = BLOCK_MINUTES_BEFORE
    block_after = BLOCK_MINUTES_AFTER
    if symbol:
        from config_simple import SYMBOL_LIMITS
        sym_cfg = SYMBOL_LIMITS.get(symbol, {})
        sym_before = sym_cfg.get("news_minutes_before")
        sym_after = sym_cfg.get("news_minutes_after")
        if sym_before is not None:
            block_before = sym_before
        if sym_after is not None:
            block_after = sym_after

    blocked = []
    for e in events:
        dt = e.get("time")
        if dt is None:
            continue
        if isinstance(dt, str):
            try:
                dt = datetime.fromisoformat(dt)
            except ValueError:
                logger.debug(f"Bad event date format: {dt}")
                continue

        start_block = dt - timedelta(minutes=block_before)
        end_block = dt + timedelta(minutes=block_after)

        if start_block <= utc_now <= end_block:
            name = e.get("name", "Unknown")
            blocked.append({
                "name": name,
                "event_time": dt,
                "block_start": start_block,
                "block_until": end_block,
                "seconds_remaining": int((end_block - utc_now).total_seconds())
            })

    if blocked:
        reasons = " | ".join(
            f"{b['name']} (encore {b['seconds_remaining']}s)"
            for b in blocked
        )
        logger.info(f"  [NEWS] Trades bloques: {reasons}")

    return len(blocked) > 0, blocked
