#!/usr/bin/env python3
"""
Check GR Symbols — diagnostic des conditions de déblocage des 5 symboles RÈGLE D'OR.
Créé 16 Août 2026 (Robot Manager) pour le suivi lundi 17/08.

But : vérifier en direct pourquoi un symbole GR ne produit pas de trade :
  - momentum 20 (mom20) vs seuil ATR (thresh) → "pas de signal MOM20x3"
  - spread réel vs max_spread_points + max_spread_atr_ratio → "Spread too high"
  - régime (BAISSIER/HAUSSIER) vs BUY-only → "aucun trade possible en baissier"
  - min_score effectif (cfg + dynamic) vs score des signaux générés

Usage :
    python scripts/check_gr_symbols.py                # rapport unique
    python scripts/check_gr_symbols.py --watch 300    # boucle toutes les 300s
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

BASE = Path(__file__).parent.parent
GR_SYMBOLS = [
    "US100.cash", "US30.cash", "JP225.cash", "SOLUSD", "BTCUSD",
    "XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "USDCAD", "AUDUSD", "NZDUSD", "USDCHF",
]

GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
RESET = "\033[0m"


def load_symbol_limits():
    """Charge symbol_limits exactement comme refresh_symbol_limits (frais, bypass cache)."""
    try:
        from config.schema import _load_yaml, _interpolate, _deep_merge, ConfigSchema
        _load_yaml.cache_clear()
        raw = _interpolate(_load_yaml(BASE / "config" / "default.yaml"))
        env_path = BASE / "config" / "production.yaml"
        if env_path.exists():
            raw = _deep_merge(raw, _interpolate(_load_yaml(env_path)))
        cfg = ConfigSchema(**raw)
        return {sym: lim.model_dump(exclude_none=True) for sym, lim in cfg.symbol_limits.items()}
    except Exception as e:
        print(f"[WARN] Chargement symbol_limits impossible: {e}")
        return {}


def get_market_snapshot():
    """Snapshot marché MT5 : prix, mom20, ATR(14) H1, spread réel."""
    import numpy as np
    import MetaTrader5 as mt5

    if not mt5.initialize():
        print("[ERR] MT5 non initialisé")
        return {}

    out = {}
    for sym in GR_SYMBOLS:
        info = mt5.symbol_info(sym)
        tick = mt5.symbol_info_tick(sym)
        if not info or not tick:
            out[sym] = {"available": False}
            continue
        rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H1, 0, 30)
        snap = {"available": True, "bid": tick.bid, "ask": tick.ask,
                "spread": tick.ask - tick.bid, "point": info.point}
        if rates is not None and len(rates) > 21:
            closes = np.array([r["close"] for r in rates])
            highs = np.array([r["high"] for r in rates])
            lows = np.array([r["low"] for r in rates])
            snap["mom20"] = float(closes[-1] - closes[-21])
            snap["ma20"] = float(closes[-20:].mean())
            tr = np.maximum(highs[1:] - lows[1:],
                            np.maximum(abs(highs[1:] - closes[:-1]),
                                       abs(lows[1:] - closes[:-1])))
            snap["atr14"] = float(np.mean(tr[-14:]))
        out[sym] = snap
    mt5.shutdown()
    return out


def evaluate(sym, snap, limits):
    """Diagnostic complet du symbole."""
    if not snap.get("available"):
        return RED + f"{sym}: INJOIGNABLE" + RESET

    cfg = limits.get(sym, {})
    lines = [f"--- {sym} (bid={snap['bid']:.2f}, spread={snap['spread']*snap.get('point',1):.0f} pts) ---"]

    # 1. Momentum vs threshold (seuil stratégie)
    mom = snap.get("mom20")
    if mom is None:
        lines.append(f"  [MOM] données insuffisantes")
    else:
        ma20 = snap.get("ma20", 0)
        regime = "HAUSSIER" if mom > 0 else "BAISSIER"
        adx_approx = None
        lines.append(f"  [MOM] mom20={mom:+.2f} ({mom/ma20*100:+.2f}%) → régime {regime}")
        # BUY-only check
        allow_buys = cfg.get("allow_buys", True)
        allow_shorts = cfg.get("allow_shorts", False)
        if not allow_buys:
            lines.append(RED + f"  [BLOCAGE] allow_buys=false → aucun BUY possible" + RESET)
        if not allow_shorts and mom < 0:
            lines.append(YELLOW + f"  [BLOQUE] BUY-only + régime baissier → 0 trade possible" + RESET)

    # 2. Spread check (même logique que ftmo_protector._check_spread)
    spread = snap.get("spread", 0)
    point = snap.get("point", 0)
    max_sp = cfg.get("max_spread_points", 120)
    spread_pts_ok = spread < max_sp * point * 1.05
    atr = snap.get("atr14")
    atr_ok = True
    atr_ratio = None
    if atr and atr > 0:
        max_atr_ratio = cfg.get("max_spread_atr_ratio", 0.15)
        atr_ratio = spread / atr
        atr_ok = atr_ratio < max_atr_ratio
    lines.append(f"  [SPREAD] pts_ok={spread_pts_ok} (spread={spread:.5f} < {max_sp*point:.5f})")
    if atr_ratio is not None:
        mr = cfg.get("max_spread_atr_ratio", 0.15)
        ok = GREEN if atr_ok else RED
        lines.append(f"  [SPREAD] ATR_ratio={atr_ratio:.1%} {'<' if atr_ok else '>'} {mr:.0%} {ok}")

    # 3. min_score effectif
    min_score = cfg.get("min_score", 0.60)
    lines.append(f"  [SCORE] min_score cfg={min_score} (dynamic possible via signal_validator)")

    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", type=int, default=0, help="Mode boucle (secondes)")
    args = ap.parse_args()

    while True:
        print("=" * 62)
        print(f"GR SYMBOLS CHECK — {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 62)
        limits = load_symbol_limits()
        snaps = get_market_snapshot()
        for sym in GR_SYMBOLS:
            print(evaluate(sym, snaps.get(sym, {}), limits))
        print()
        if not args.watch:
            break
        time.sleep(args.watch)


if __name__ == "__main__":
    main()