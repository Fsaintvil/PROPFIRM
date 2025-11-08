# migration: try import safe sender (fail-open)
try:
    from src.utils.mt5_safe import send_order as _mt5_send_safe
except Exception:
    _mt5_send_safe = None

#!/usr/bin/env python3
"""Executeur sûr de trades live (dry-run par défaut).

Usage:
  python tools/execute_live_trades_safe.py [--file PATH] [--apply]

Comportement:
 - Par défaut: dry-run, aucun ordre envoyé. Écrit un log détaillé dans logs/
 - Pour envoyer réellement: utiliser --apply et confirmer le prompt interactif.
 - Avant envoi réel, le script sauvegarde les fichiers cibles dans
     artifacts/backup_apply_YYYYMMDDTHHMMSSZ

Ce script ne tente pas d'ouvrir une session réelle MT5 automatiquement sans vos identifiants.
Il cherche une configuration dans `config/mt5_credentials.env` ou variables d'environnement.
"""

import argparse
import json
import os
import shutil
import math
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

DEFAULT_TRADES_GLOB = "artifacts/live_training/trades_*.ndjson"
LOG_DIR = Path("logs")
ARTIFACTS = Path("artifacts")

# Safety caps to avoid runaway position creation in case of bugs or broker limits.
# Tune these values to your risk policy.
MAX_POSITIONS_TOTAL = 50
MAX_POSITIONS_PER_SYMBOL = 8

# Project policy: enforce live-only trades (no demo). When True, any non-live
# record will cause --apply to abort. Dry-run will still report non-live counts.
ENFORCE_LIVE_ONLY = True


def sha256_file(path: Path):
    h = sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def find_trades(file_arg: str | None):
    if file_arg:
        p = Path(file_arg)
        if not p.exists():
            raise FileNotFoundError(f"Fichier non trouvé: {p}")
        return [p]
    # fallback: list files matching the default glob
    import glob

    return [Path(p) for p in glob.glob(DEFAULT_TRADES_GLOB)]


def read_ndjson_sample(path: Path, max_lines=3):
    lines = []
    with open(path, "r", encoding="utf-8") as f:
        for i, ln in enumerate(f):
            if i >= max_lines:
                break
            ln = ln.strip()
            if ln:
                try:
                    lines.append(json.loads(ln))
                except Exception:
                    lines.append({"raw": ln})
    return lines


def is_record_live(obj: dict) -> bool:
    """Return True if a parsed NDJSON record indicates a live trade.

    Recognizes common keys: 'mode' == 'live', 'source_live' True, or 'live' True.
    """
    if not isinstance(obj, dict):
        return False
    if obj.get("mode") == "live":
        return True
    if obj.get("source_live") is True:
        return True
    if obj.get("live") is True:
        return True
    return False


def load_live_rules(path: Path | None):
    """Load live-rule configuration from JSON file.

    Expected format example:
      {"live_retcodes": [10009, 0], "mode_values_live": ["live"]}
    """
    rules = {"live_retcodes": [10009, 10008, 0], "mode_values_live": ["live"]}
    if path and path.exists():
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            rules.update({k: data[k] for k in data if k in rules or True})
        except Exception:
            pass
    return rules


def record_is_live_by_rules(obj: dict, rules: dict) -> bool:
    if not isinstance(obj, dict):
        return False
    # mode check
    mode = obj.get('mode')
    if isinstance(mode, str):
        mode_low = mode.lower()
        live_values = [v.lower() for v in rules.get('mode_values_live', [])]
        if mode_low in live_values:
            return True
    # source_live or live boolean
    if obj.get('source_live') is True or obj.get('live') is True:
        return True
    # retcode-based rule
    rc = obj.get('retcode')
    if rc is not None:
        try:
            if int(rc) in [int(x) for x in rules.get('live_retcodes', [])]:
                return True
        except Exception:
            pass
    return False


def backup_artifacts(target_files: list[Path]):
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bk = ARTIFACTS / f"backup_apply_{ts}"
    bk.mkdir(parents=True, exist_ok=True)
    for p in target_files:
        dst = bk / p.name
        shutil.copy2(p, dst)
    return bk


def confirm(prompt: str) -> bool:
    try:
        ans = input(prompt).strip().lower()
    except EOFError:
        return False
    return ans in ("y", "yes", "oui", "o")


def load_mt5_credentials():
    # Non invasif: load from config file if exists or from env
    cfg = Path("config/mt5_credentials.env")
    creds = {}
    if cfg.exists():
        with open(cfg) as f:
            for ln in f:
                ln = ln.strip()
                if not ln or ln.startswith("#"):
                    continue
                if "=" in ln:
                    k, v = ln.split("=", 1)
                    creds[k.strip()] = v.strip()
    # fallback env
    # accept common alternative names and map them to canonical keys
    # environment fallback for multiple variants
    env_map = {
        "MT5_ACCOUNT": ["MT5_ACCOUNT", "MT5_LOGIN", "MT5_LOGIN_ID"],
        "MT5_PASSWORD": ["MT5_PASSWORD", "MT5_PWD", "MT5_PASS"],
        "MT5_SERVER": ["MT5_SERVER"],
    }
    for canon, variants in env_map.items():
        # already present from file?
        if canon in creds:
            continue
        for v in variants:
            if os.environ.get(v):
                creds[canon] = os.environ.get(v)
                break
        # also check if variants were set in the file with alternate names
        if canon not in creds:
            for v in variants:
                if v in creds:
                    # map value under canonical name
                    creds[canon] = creds[v]
                    break
    return creds


def dry_run_report(trade_files: list[Path], out_log: Path):
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "files": [],
    }
    for p in trade_files:
        st = p.stat()
        sample = read_ndjson_sample(p, max_lines=2)
        # count non-live records for enforcement
        non_live = 0
        total = 0
        with open(p, "r", encoding="utf-8") as fcc:
            for ln in fcc:
                ln = ln.strip()
                if not ln:
                    continue
                total += 1
                try:
                    obj = json.loads(ln)
                except Exception:
                    obj = None
                if not is_record_live(obj):
                    non_live += 1
        summary["files"].append(
            {
                "path": str(p),
                "size": st.st_size,
                "lines_sample": sample,
                "sha256": sha256_file(p),
                "total_lines": total,
                "non_live_count": non_live,
            }
        )

    out_log.parent.mkdir(parents=True, exist_ok=True)
    with open(out_log, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"DRY-RUN: rapport écrit: {out_log}")
    return summary


def send_orders_placeholder(trade_files: list[Path], creds: dict):
    """
    Backward-compatible placeholder runner.

    Behavior change: if the environment variable `ALLOW_MT5_SEND` is set to '1',
    and the MetaTrader5 binding is available, and valid credentials are found,
    this function will delegate to `connect_and_send_mt5` to perform real sends.

    Otherwise it preserves the previous simulation behaviour (no external calls).
    This keeps the default safe while allowing administrators to enable real
    sends via environment when they explicitly opt in.
    """
    # opt-in guard: require explicit environment variable to allow real sends
    allow_send = os.environ.get("ALLOW_MT5_SEND", "0") == "1"
    creds_local = creds or load_mt5_credentials()

    if allow_send and MT5_AVAILABLE and creds_local.get("MT5_ACCOUNT"):
        # delegate to the real MT5 sender
        try:
            return connect_and_send_mt5(trade_files, creds_local)
        except Exception as e:
            # convert exception into a structured report similar to the simulated one
            return {"sent": [], "errors": [{"exception": str(e)}]}

    # default: simulation (backwards compatible)
    report = {"sent": [], "skipped": []}
    for p in trade_files:
        sample = read_ndjson_sample(p, max_lines=1)
        report["skipped"].append(
            {"file": str(p), "note": "simulation - not sent", "sample": sample}
        )
    return report


# Optional MetaTrader5 integration (import guarded)
try:
    import MetaTrader5 as mt5  # type: ignore
    MT5_AVAILABLE = True
except Exception:
    mt5 = None  # type: ignore
    MT5_AVAILABLE = False


def connect_and_send_mt5(trade_files: list[Path], creds: dict):
    """Connect to MT5 and send orders described in NDJSON files.

    Safety:
      - This function will attempt to connect to MT5 using `mt5.initialize()`.
      - It will not override stoploss/takeprofit logic; it transcribes fields present in the NDJSON.
      - Caller must ensure confirmation and backups were done.
    """
    report = {"sent": [], "errors": []}
    if not MT5_AVAILABLE:
        raise RuntimeError("MetaTrader5 library not available in this Python environment")

    # try to initialize MT5
    init_ok = False
    if hasattr(mt5, "initialize"):
        init_ok = mt5.initialize()
    if not init_ok:
        raise RuntimeError("mt5.initialize() failed. Check local MT5 terminal and permissions.")

    try:
        for p in trade_files:
            with open(p, "r", encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        t = json.loads(ln)
                    except Exception as e:
                        report["errors"].append({"file": str(p), "error": f"json:{e}", "line": ln})
                        continue

                    # Map trade record to mt5 order request
                    # Expected fields (best-effort):
                    #   - symbol, action (buy/sell), volume, price, sl, tp
                    symbol = t.get("symbol")
                    action = t.get("action") or t.get("side") or t.get("type")
                    volume = float(t.get("volume", 0.01))
                    price = t.get("price")
                    sl = t.get("sl") or t.get("stoploss")
                    tp = t.get("tp") or t.get("takeprofit")

                    # Build request according to MetaTrader5 API
                    request = {
                        "action": mt5.TRADE_ACTION_DEAL,
                        "symbol": symbol,
                        "volume": volume,
                        "type": (
                            mt5.ORDER_TYPE_BUY
                            if str(action).lower().startswith("b")
                            else mt5.ORDER_TYPE_SELL
                        ),
                        # price can be None (market), then mt5 will internally set it
                        "price": (
                            float(price)
                            if price is not None
                            else mt5.symbol_info_tick(symbol).ask
                        ),
                        "sl": float(sl) if sl is not None else 0.0,
                        "tp": float(tp) if tp is not None else 0.0,
                        "deviation": int(t.get("deviation", 20)),
                        "magic": int(t.get("magic", 0)),
                        "comment": t.get("comment", "execute_live_trades_safe"),
                    }
                    # validate request before sending
                    try:
                        # When actually sending to the broker, allow conservative
                        # automatic adjustment of SL/TP to satisfy minimal stop rules.
                        ok, reason = validate_mt5_request(request, mt5, adjust_if_needed=True)
                    except Exception as e:
                        ok = False
                        reason = f"validation_exception:{e}"

                    if not ok:
                        report["errors"].append(
                            {
                                "file": str(p),
                                "error": "validation_failed",
                                "reason": reason,
                                "order": request,
                            }
                        )
                        continue

                    # send order with retry logic for common broker rejections
                    try:
                        # enforce simple position caps before sending to broker
                        try:
                            total_pos = mt5.positions_total()
                            if total_pos is not None and total_pos >= MAX_POSITIONS_TOTAL:
                                report["errors"].append({
                                    "file": str(p),
                                    "error": "position_limit_reached_total",
                                    "detail": {"positions_total": total_pos},
                                })
                                continue
                            pos_sym = mt5.positions_get(symbol=symbol)
                            pos_sym_count = len(pos_sym) if pos_sym else 0
                            if pos_sym_count >= MAX_POSITIONS_PER_SYMBOL:
                                report["errors"].append({
                                    "file": str(p),
                                    "error": "position_limit_reached_symbol",
                                    "symbol": symbol,
                                    "positions_symbol": pos_sym_count,
                                })
                                continue
                        except Exception as e:
                            # log failure to evaluate positions but continue; do not treat as fatal
                            report["errors"].append({
                                "file": str(p),
                                "error": "positions_check_failed",
                                "exception": str(e),
                            })

                        # use centralized safe sender which applies preflight (price/volume)
                        try:
                            from src.utils.mt5_safe import send_order
                        except Exception:
                            send_order = None

                        if send_order is not None:
                            result = send_order(request, logger=None, mt5_module=mt5)
                        else:
                            result = _mt5_send_safe(request)
                        # if broker rejects due to invalid stops (10016), attempt one conservative remediation
                        rc = getattr(result, "retcode", None)
                        if rc == 10016:
                            try:
                                # compute market price if needed
                                eff_price = request.get("price")
                                if not eff_price:
                                    tick = mt5.symbol_info_tick(request["symbol"])
                                    eff_price = getattr(tick, "ask", None) or getattr(tick, "bid", None)
                                si = mt5.symbol_info(request["symbol"])
                                min_level = getattr(si, "trade_stops_level", None) or getattr(si, "trade_stop_level", None) or 5
                                point = getattr(si, "point", None) or 1e-5
                                # ensure SL/TP are at least min_level*point away from market
                                if request.get("sl"):
                                    req_sl = float(request["sl"])
                                    delta = abs(eff_price - req_sl)
                                    needed = float(min_level) * float(point)
                                    if delta < needed:
                                        if request.get("type") == mt5.ORDER_TYPE_BUY:
                                            new_sl = eff_price - needed
                                        else:
                                            new_sl = eff_price + needed
                                        request["sl"] = round(new_sl, 6)
                                if request.get("tp"):
                                    req_tp = float(request["tp"])
                                    delta = abs(req_tp - eff_price)
                                    needed = float(min_level) * float(point)
                                    if delta < needed:
                                        if request.get("type") == mt5.ORDER_TYPE_BUY:
                                            new_tp = eff_price + needed
                                        else:
                                            new_tp = eff_price - needed
                                        request["tp"] = round(new_tp, 6)
                                # retry once
                                if send_order is not None:
                                    result = send_order(request, logger=None, mt5_module=mt5)
                                else:
                                    result = _mt5_send_safe(request)
                            except Exception:
                                pass
                        # if still rejected due to invalid stops, DO NOT send without stops; record error instead
                        rc = getattr(result, "retcode", None)
                        if rc == 10016:
                            report["errors"].append(
                                {
                                    "file": str(p),
                                    "error": "invalid_stops_after_retries",
                                    "order": request,
                                    "result": (result._asdict() if hasattr(result, "_asdict") else str(result)),
                                }
                            )
                            continue

                        # default append result
                        report["sent"].append(
                            {
                                "file": str(p),
                                "order": request,
                                "result": (
                                    result._asdict() if hasattr(result, "_asdict") else str(result)
                                ),
                            }
                        )
                    except Exception as e:
                        report["errors"].append({"file": str(p), "error": str(e), "order": request})
    finally:
        try:
            mt5.shutdown()
        except Exception:
            pass

    return report


def build_mt5_request_from_record(t: dict):
    """Build a partial MT5 request dict from a trade record dict.

    This helper is separately testable without requiring MT5 to be installed.
    """
    symbol = t.get("symbol")
    action = t.get("action") or t.get("side") or t.get("type")
    volume = float(t.get("volume", 0.01))
    price = t.get("price")
    sl = t.get("sl") or t.get("stoploss")
    tp = t.get("tp") or t.get("takeprofit")

    req = {
        "symbol": symbol,
        "volume": volume,
        "type_hint": "buy" if str(action).lower().startswith("b") else "sell",
        "price": float(price) if price is not None else None,
        "sl": float(sl) if sl is not None else None,
        "tp": float(tp) if tp is not None else None,
        "deviation": int(t.get("deviation", 20)),
        "magic": int(t.get("magic", 0)),
        "comment": t.get("comment", "execute_live_trades_safe"),
    }
    return req


def validate_mt5_request(req: dict, mt5_module, adjust_if_needed: bool = False):
    """Validate MT5 request dict conservatively.

    Returns (True, None) if OK, or (False, reason) if invalid.
    Uses mt5_module symbol info when available; otherwise performs basic numeric checks.
    """
    symbol = req.get('symbol')
    if not symbol:
        return False, 'missing_symbol'

    # basic numeric checks
    try:
        vol = float(req.get('volume', 0))
    except Exception:
        return False, 'invalid_volume'
    if vol <= 0:
        return False, 'nonpositive_volume'

    price = req.get('price')
    sl = req.get('sl')
    tp = req.get('tp')

    # defaults / placeholders
    point = None
    min_stop_level = None
    digits = None
    vol_min = None
    vol_step = None

    # Try to enrich with mt5 symbol info when available
    si = None
    if mt5_module is not None:
        try:
            si = mt5_module.symbol_info(symbol)
            if si is None:
                return False, 'symbol_unknown'
            # prefer explicit point attribute
            point = getattr(si, 'point', None)
            digits = getattr(si, 'digits', None)
            if point is None and digits is not None:
                try:
                    point = float(10) ** (-int(digits))
                except Exception:
                    point = None
            # minimal stop level (in points)
            min_stop_level = (
                getattr(si, 'trade_stops_level', None)
                or getattr(si, 'trade_stop_level', None)
            )
            # volume constraints
            vol_min = getattr(si, 'volume_min', None) or getattr(si, 'volume_minimum', None)
            vol_step = getattr(si, 'volume_step', None) or getattr(si, 'volume_step_size', None)
        except Exception:
            si = None

    # conservative defaults when missing
    if point is None:
        point = 1e-5
    if min_stop_level is None:
        min_stop_level = 5
    if vol_min is None:
        vol_min = 0.01
    if vol_step is None:
        vol_step = 0.01

    # volume min/step checks
    try:
        if vol < float(vol_min) - 1e-12:
            return False, f'volume_below_min (vol={vol} min={vol_min})'
    except Exception:
        # if conversion fails, ignore and proceed
        pass

    # enforce step alignment within tolerance
    try:
        step = float(vol_step)
        base = float(vol_min)
        # compute number of steps from base
        steps = (vol - base) / step
        if steps < -1e-9:
            return False, f'volume_below_min (vol={vol} min={vol_min})'
        # allow small floating rounding
        if not math.isclose(round(steps), steps, rel_tol=1e-6, abs_tol=1e-9) and steps > 1e-9:
            return False, f'volume_step_mismatch (vol={vol} min={vol_min} step={vol_step})'
    except Exception:
        # ignore step enforcement if numeric retrieval fails
        pass

    # helper: round a price to symbol point/digits if available
    def _round_price(val, point_val, digits_val):
        try:
            if digits_val is not None:
                return round(float(val), int(digits_val))
            if point_val is not None and point_val > 0:
                return round(float(val) / float(point_val)) * float(point_val)
            return float(val)
        except Exception:
            return float(val)

    # obtain market price when needed
    market_price = None
    if price is None and mt5_module is not None:
        try:
            tick = mt5_module.symbol_info_tick(symbol)
            if tick is not None:
                market_price = getattr(tick, 'ask', None) or getattr(tick, 'bid', None)
        except Exception:
            market_price = None

    effective_price = price if price is not None else market_price
    if effective_price is None:
        return False, 'no_price_available'

    # numeric distances
    try:
        d_sl = abs(float(effective_price) - float(sl)) if sl is not None else None
        d_tp = abs(float(effective_price) - float(tp)) if tp is not None else None
    except Exception:
        return False, 'non_numeric_sl_tp'

    min_dist = float(min_stop_level) * float(point)

    # infer side early for adjustment logic
    type_hint = req.get('type') or req.get('type_hint')
    side = None
    if isinstance(type_hint, str):
        side = 'buy' if str(type_hint).lower().startswith('b') else 'sell'

    adjustments = []
    if d_sl is not None and d_sl + 1e-12 < min_dist:
        if adjust_if_needed:
            # compute conservative adjusted sl based on side inference
            if side == 'buy':
                new_sl = float(effective_price) - float(min_dist)
            elif side == 'sell':
                new_sl = float(effective_price) + float(min_dist)
            else:
                new_sl = float(effective_price) - float(min_dist)
            new_sl = _round_price(new_sl, point, digits)
            req['sl'] = new_sl
            adjustments.append(f'adjusted_sl->{new_sl}')
        else:
            return False, f'sl_too_close (dist={d_sl} min={min_dist})'
    if d_tp is not None and d_tp + 1e-12 < min_dist:
        if adjust_if_needed:
            if side == 'buy':
                new_tp = float(effective_price) + float(min_dist)
            elif side == 'sell':
                new_tp = float(effective_price) - float(min_dist)
            else:
                new_tp = float(effective_price) + float(min_dist)
            new_tp = _round_price(new_tp, point, digits)
            req['tp'] = new_tp
            adjustments.append(f'adjusted_tp->{new_tp}')
        else:
            return False, f'tp_too_close (dist={d_tp} min={min_dist})'

    if side == 'buy':
        if sl is not None and float(sl) >= float(effective_price):
            return False, 'sl_not_below_price_for_buy'
        if tp is not None and float(tp) <= float(effective_price):
            return False, 'tp_not_above_price_for_buy'
    if side == 'sell':
        if sl is not None and float(sl) <= float(effective_price):
            return False, 'sl_not_above_price_for_sell'
        if tp is not None and float(tp) >= float(effective_price):
            return False, 'tp_not_below_price_for_sell'

    if adjustments:
        return True, ','.join(adjustments)
    return True, None


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Executeur sûr de trades live "
            "(dry-run par défaut)"
        )
    )
    parser.add_argument(
        "--file",
        help=(
            "Fichier NDJSON de trades à exécuter. "
            "Si omis, le répertoire artifacts/live_training sera utilisé."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Activer l'envoi réel (exige confirmation interactive)",
    )
    parser.add_argument(
        "--send-real",
        action="store_true",
        help=(
            "(DANGEROUS) Effectuer l'envoi réel via MT5. "
            "Exige confirmation textuelle 'APPLY LIVE'."
        ),
    )
    args = parser.parse_args(argv)

    trade_files = find_trades(args.file)
    if not trade_files:
        print(
            "Aucun fichier de trades trouvé. "
            "Cherchez dans artifacts/live_training/trades_*.ndjson"
        )
        return 1
        return 1

    print(f"Fichiers détectés: {len(trade_files)}")
    for p in trade_files:
        print(f" - {p}")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    out_log = LOG_DIR / (
        "execute_live_trades_safe_dryrun_"
        + datetime.now().strftime("%Y%m%d_%H%M%S")
        + ".json"
    )

    # Dry-run: write report and exit unless --apply
    summary = dry_run_report(trade_files, out_log)
    # print a short confirmation
    print(f"DRY-RUN: {len(summary.get('files', []))} fichiers analysés")

    # enforce live-only policy: check non_live counts
    total_non_live = sum(f.get('non_live_count', 0) for f in summary.get('files', []))
    if ENFORCE_LIVE_ONLY and total_non_live > 0:
        print("ERROR: la politique du projet exige uniquement des enregistrements live.")
        print(f"Total non-live détectés: {total_non_live}")
        for f in summary.get('files', []):
            nn = f.get('non_live_count', 0)
            if nn:
                print(f" - {f.get('path')}: non_live={nn} / total={f.get('total_lines')}")
        print(
            "Corrigez ou filtrez les fichiers pour ne contenir que des enregistrements "
            "live avant d'utiliser --apply."
        )
        return 4

    if not args.apply:
        print(
            "DRY-RUN: aucune écriture en production. "
            "Passez --apply pour exécuter après confirmation."
        )
        return 0

    # apply requested: validate credentials
    creds = load_mt5_credentials()
    if not creds.get("MT5_ACCOUNT"):
        print(
            "ERROR: identifiants MT5 introuvables. "
            "Placez-les dans config/mt5_credentials.env ou variables d'environnement."
        )
        return 2

    print("--apply demandé. Checklist de sécurité:")
    print(" - fichiers trades détectés:", len(trade_files))
    print(" - credentials trouvés: ", ", ".join(k for k in creds.keys()))

    if not confirm("Confirmez-vous l'écriture des fichiers en production ? [oui/N]: "):
        print("Abandon: confirmation non reçue.")
        return 3

    # backup
    to_backup = (
        [ARTIFACTS / "live_report_applied.json"]
        if (ARTIFACTS / "live_report_applied.json").exists()
        else []
    )
    backup_dir = backup_artifacts(to_backup)
    print("Backup créé:", backup_dir)

    # simulate send
    if args.send_real:
        # final textual confirmation required for destructive action
        try:
            final = input("Tapez exactement 'APPLY LIVE' pour confirmer l'envoi réel: ").strip()
        except EOFError:
            final = ""
        if final.upper() != "APPLY LIVE":
            print("Abandon: confirmation textuelle non reçue. Aucun envoi réel effectué.")
            return 5

        # perform real send via MT5 API
        try:
            report = connect_and_send_mt5(trade_files, creds)
        except Exception as e:
            print("ERREUR lors de l'envoi réel:", e)
            report = {"sent": [], "errors": [{"exception": str(e)}]}
    else:
        report = send_orders_placeholder(trade_files, creds)

    # write apply report
    apply_log = LOG_DIR / (
        "execute_live_trades_apply_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".json"
    )
    with open(apply_log, "w", encoding="utf-8") as f:
        json.dump(
            {
                "applied_at": datetime.now(timezone.utc).isoformat(),
                "report": report,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )

    print("Apply terminé (simulation). Rapport écrit:", apply_log)
    print(
        "REMARQUE: Ceci est une simulation. Pour intégrer des envois réels, "
        "remplacez `send_orders_placeholder` par l'appel MT5 sécurisé "
        "et testez sur compte démo."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
