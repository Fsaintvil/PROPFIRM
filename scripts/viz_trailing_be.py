"""Visualisation professionnelle Trailing & Breakeven — LIT LA CONFIG RÉELLE.

Robot Manager — 20 Août 2026.
Source de vérité : engine_simple/ftmo_config.py (TRAILING_BY_SYMBOL,
TRAILING_BY_REGIME, BE_BUFFER_BY_SYMBOL) + config/default.yaml
(SL/TP, partial_tp_progress, time_stop_max_hours_profit).

Usage :
    python scripts/viz_trailing_be.py XAUUSD    # figure détaillée par symbole
    python scripts/viz_trailing_be.py BTCUSD
    python scripts/viz_trailing_be.py all       # 3 détaillées + 1 comparative

Sorties : runtime/trailing_be_<SYM>.png + runtime/trailing_be_COMPARE.png
"""

import sys
import numpy as np
import matplotlib.pyplot as plt
import matplotlib

# Import matplotlib avec backend Agg (pas d'affichage interactif requis)
matplotlib.use("Agg")

sys.path.insert(0, ".")
import config_simple as cfg
from engine_simple.ftmo_config import get_trailing_for_symbol, get_be_buffer_for_symbol
from engine_simple.trailer import BE_PROGRESSIVE_LEVELS

# ── Paramètres du scénario ─────────────────────────────────────────────────
CYCLE_S = 15
DURATION_H = 12.0

# Profils des 3 symboles (régime TREND_UP — le seul utilisé en BUY-only)
SYMBOLS = {
    "XAUUSD": dict(color="#b8860b", label="XAUUSD — Or (trailing SERRÉ)"),
    "BTCUSD": dict(color="#f7931a", label="BTCUSD — Bitcoin (trailing LARGE)"),
    "EURUSD": dict(color="#1a73e8", label="EURUSD — Forex (fallback standard)"),
}


def get_sym_params(sym: str) -> dict:
    """Lit les VRAIES valeurs depuis la config (source de vérité unique)."""
    sl = cfg.SYMBOL_LIMITS.get(sym, {})
    params = {
        "sl_atr": sl.get("sl_atr_trending", 1.5),
        "tp_atr": sl.get("tp_atr_trending", 6.0),
        "partial_progress": sl.get("partial_tp_progress", 0.65),
        "ts_profit_h": sl.get("time_stop_max_hours_profit", 12.0),
        "trailing": get_trailing_for_symbol(sym, "TREND_UP"),
        "be_buffer": get_be_buffer_for_symbol(sym, "TREND_UP"),
    }
    return params


def protected_sl(profit_atr: float, p: dict) -> float:
    """Rejoue la logique du trailer en unités ×ATR (SL relatif à l'entrée).

    Retourne le SL en ×ATR AU-DESSUS de l'entrée (négatif = sous l'entrée).
    """
    sl = -p["sl_atr"]  # SL initial
    # 1) BE progressif : le plus haut palier atteint (sl_improves implicite)
    for thresh, buf in BE_PROGRESSIVE_LEVELS:
        if profit_atr > thresh:
            sl = max(sl, buf)
    # 2) Trailing N1→N5 : peak−trail_dist (peak ≈ profit_atr atteint)
    for thresh, dist in p["trailing"]:
        if profit_atr > thresh:
            sl = max(sl, profit_atr - dist)
    return sl


def simulate_price(seed: int, atr: float, tp_atr: float) -> tuple:
    """Simule une trajectoire de prix réaliste en ×ATR (12h, cycle 15s).

    Phases : montée → retrace → forte montée → retrace → montée finale.
    """
    rng = np.random.RandomState(seed)
    n = int(DURATION_H * 3600 / CYCLE_S)
    noise = rng.randn(n) * 0.012 * atr
    t = np.zeros(n)
    # Construction par segments (en ×ATR)
    seg = lambda h0, h1, v0, v1: np.linspace(v0, v1, max(1, int((h1 - h0) * 3600 / CYCLE_S)))
    base = np.concatenate([
        seg(0, 2.0, 0, 2.0),
        seg(2, 3.5, 2.0, 1.5),
        seg(3.5, 6.0, 1.5, 4.5),
        seg(6.0, 7.5, 4.5, 4.0),
        seg(7.5, 10.5, 4.0, min(7.25, tp_atr - 0.25)),
        seg(10.5, 12.0, min(7.25, tp_atr - 0.25), min(7.0, tp_atr - 0.5)),
    ])
    n = len(base)
    price = base + noise[:n] * 0.6
    price = np.minimum(price, tp_atr - 0.2)
    price = np.maximum(price, -0.2)
    hours = np.linspace(0, DURATION_H, n)
    peak = np.maximum.accumulate(price)
    return hours, price, peak


def make_detailed_fig(sym: str) -> None:
    """Figure détaillée 4 panneaux pour un symbole (axe TEMPS réel)."""
    p = get_sym_params(sym)
    color = SYMBOLS[sym]["color"]

    hours, price, peak = simulate_price(seed=hash(sym) % 1000, atr=1.0, tp_atr=p["tp_atr"])
    sl_series = np.array([protected_sl(peak[i], p) for i in range(len(peak))])

    # Évènements temporels
    def first_time(cond):
        idx = np.argmax(cond) if np.any(cond) else None
        return hours[idx] if idx is not None else None

    t_be = first_time(peak >= 1.0)
    t_n1 = first_time(peak >= p["trailing"][0][0])
    t_partial = first_time(peak >= p["partial_progress"] * p["tp_atr"])
    t_tp = first_time(price >= p["tp_atr"] - 0.05)

    fig, axes = plt.subplots(4, 1, figsize=(14, 15), gridspec_kw={"height_ratios": [3, 2, 1.6, 1.4]})
    fig.suptitle(
        f"{sym} — Trailing & Breakeven (régime TREND_UP) · Config RÉELLE\n"
        f"SL {p['sl_atr']}×ATR · TP {p['tp_atr']}×ATR · Partial TP {p['partial_progress']:.2f} du chemin "
        f"(75% volume) · Time-stop profit {p['ts_profit_h']:.0f}h · BE buffer {p['be_buffer']}×ATR",
        fontsize=11.5, fontweight="bold",
    )

    # ── Panneau A : trajectoire + SL (temps réel) ──
    ax = axes[0]
    ax.plot(hours, price, color=color, lw=1.6, label="Prix (×ATR depuis l'entrée)", zorder=3)
    ax.plot(hours, peak, color="#34a853", lw=1.1, ls="--", alpha=0.7, label="Peak cumulé")
    ax.plot(hours, sl_series, color="#d93025", lw=2.2, label="SL protégé (×ATR)", zorder=4)
    ax.axhline(0, color="#80868b", lw=1, ls=":", label="Entrée")
    ax.axhline(-p["sl_atr"], color="#f29900", lw=1, ls="--", alpha=0.6, label=f"SL initial (−{p['sl_atr']}×ATR)")
    ax.axhline(p["tp_atr"], color="#1a73e8", lw=1, ls="--", alpha=0.4, label=f"TP ({p['tp_atr']}×ATR)")
    ax.axvspan(0, 4.0, color="#d93025", alpha=0.06, label="Time-stop perte (≤4h)")
    ax.axvspan(p["ts_profit_h"], DURATION_H, color="#f29900", alpha=0.12,
               label=f"Time-stop profit ({p['ts_profit_h']:.0f}h)")
    ax.axvline(p["ts_profit_h"], color="#f29900", lw=1.5, ls="--")

    for th, label, c in [(t_be, "BE pur", "#f29900"), (t_partial, "Partial TP", "#7b1fa2"),
                         (t_n1, f"Lock N1", "#1a73e8")]:
        if th is not None:
            ax.axvline(th, color=c, lw=1.2, ls=":")
            ax.annotate(label, (th, p["tp_atr"]), textcoords="offset points",
                        xytext=(8, -8), fontsize=8, color=c, fontweight="bold")

    ax.set_xlabel("Temps réel (heures depuis l'ouverture)")
    ax.set_ylabel("×ATR")
    ax.legend(loc="lower right", fontsize=8, ncol=2)
    ax.set_xlim(0, DURATION_H)
    ax.set_ylim(-2.5, p["tp_atr"] + 1.5)
    ax.grid(alpha=0.3)

    # ── Panneau B : profil de verrouillage ──
    ax = axes[1]
    grid = np.linspace(0, p["tp_atr"] + 1.0, 500)
    lock = np.array([protected_sl(g, p) for g in grid])
    ax.plot(grid, lock, color="#d93025", lw=2.5, label="SL verrouillé (×ATR)")
    ax.plot(grid, grid, color="#34a853", lw=1.5, ls="--", alpha=0.7, label="Référence : SL = peak")
    ax.axhline(0, color="#80868b", lw=1, ls=":")
    for thresh, buf in BE_PROGRESSIVE_LEVELS:
        ax.axvline(thresh, color="#f29900", lw=0.8, alpha=0.4)
    for i, (thresh, dist) in enumerate(p["trailing"]):
        ax.axvline(thresh, color="#1a73e8", lw=0.8, alpha=0.5)
        ax.annotate(f"N{i+1}", (thresh, thresh - dist), textcoords="offset points",
                    xytext=(3, -18), fontsize=7, color="#1a73e8")
    ax.set_xlabel("Profit atteint (×ATR)")
    ax.set_ylabel("SL protégé (×ATR)")
    ax.legend(loc="upper left", fontsize=8)
    ax.set_xlim(0, p["tp_atr"] + 1.0)
    ax.set_ylim(-p["sl_atr"] - 0.5, p["tp_atr"] + 0.5)
    ax.grid(alpha=0.3)
    ax.set_title("Profil de verrouillage : profit sécurisé à chaque niveau (config réelle)", fontsize=10)

    # ── Panneau C : zoom BE progressif ──
    ax = axes[2]
    mask = grid <= 2.7
    ax.plot(grid[mask], lock[mask], color="#d93025", lw=2.5, label="SL (BE progressif)")
    for thresh, buf in BE_PROGRESSIVE_LEVELS:
        ax.axvline(thresh, color="#f29900", lw=1, alpha=0.6)
        ax.axhline(buf, color="#f29900", lw=0.8, alpha=0.4, ls=":")
        ax.annotate(f"{buf:+.2f}×ATR", (thresh, buf), textcoords="offset points",
                    xytext=(4, 4), fontsize=7.5, color="#f29900", fontweight="bold")
    ax.fill_between([1.3, 2.5], 0.15, 0.15, color="#f29900", alpha=0.15, label="Ancienne zone morte (avant fix 17/08)")
    ax.fill_between([1.0, 2.5], 0.0, 0.75, color="#34a853", alpha=0.08,
                    label="Montée rapide +0.15×ATR/0.30×ATR (fix 17/08)")
    ax.set_xlabel("Profit atteint (×ATR)")
    ax.set_ylabel("SL protégé (×ATR)")
    ax.set_title("Zoom BE progressif : escalier +0.15×ATR tous les 0.30×ATR", fontsize=10)
    ax.set_xlim(0.5, 2.7)
    ax.set_ylim(-0.2, 1.0)
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(alpha=0.3)

    # ── Panneau D : ligne du temps des sorties ──
    ax = axes[3]
    ax.set_xlim(0, DURATION_H)
    ax.set_ylim(-0.3, 1.3)
    ax.axis("off")
    ty = 0.5
    ax.axhline(ty, color="#80868b", lw=2)
    ax.plot(0, ty, "o", color="#80868b", ms=8, zorder=5)
    ax.text(-0.25, ty + 0.12, "Ouverture", fontsize=9, ha="left", color="#80868b")
    steps = [
        (t_be, "BE pur", "#f29900"),
        (t_n1, "Lock N1", "#1a73e8"),
        (t_partial, "Partial TP (75% fermé)", "#7b1fa2"),
        (t_tp, "TP → sortie", "#34a853"),
        (p["ts_profit_h"], f"Time-stop {p['ts_profit_h']:.0f}h → sortie forcée", "#d93025"),
    ]
    for th, label, c in steps:
        if th is None or th > DURATION_H:
            continue
        ax.plot(th, ty, "o", color=c, ms=9, zorder=5)
        ax.annotate(f"{label}\n(t+{th:.1f}h)", (th, ty), textcoords="offset points",
                    xytext=(0, 14), fontsize=8, ha="center", color=c, fontweight="bold")
    ax.set_title("Ligne du temps : les sorties possibles (temps réel)", fontsize=10)

    plt.tight_layout(rect=[0, 0, 1, 0.95])
    out = f"runtime/trailing_be_{sym}.png"
    plt.savefig(out, dpi=130, bbox_inches="tight")
    plt.close(fig)
    print(f"OK — {out} (BE t={t_be if t_be else '-'}h, N1 t={t_n1 if t_n1 else '-'}h, "
          f"partial t={t_partial if t_partial else '-'}h, TP t={t_tp if t_tp else 'jamais'}h, "
          f"ts {p['ts_profit_h']:.0f}h)")


def make_compare_fig() -> None:
    """Figure comparative : profils de verrouillage des 3 symboles."""
    fig, axes = plt.subplots(1, 3, figsize=(18, 6))
    fig.suptitle("Comparaison des philosophies de verrouillage — XAUUSD vs BTCUSD vs EURUSD (config RÉELLE)",
                 fontsize=12, fontweight="bold")

    for ax, sym in zip(axes, ["XAUUSD", "BTCUSD", "EURUSD"]):
        p = get_sym_params(sym)
        color = SYMBOLS[sym]["color"]
        grid = np.linspace(0, 8.5, 500)
        lock = np.array([protected_sl(g, p) for g in grid])

        ax.plot(grid, lock, color="#d93025", lw=2.8, label="SL verrouillé")
        ax.plot(grid, grid, color="#34a853", lw=1.2, ls="--", alpha=0.6, label="SL = peak (réf.)")
        ax.axhline(0, color="#80868b", lw=1, ls=":")
        # TP vertical
        ax.axvline(p["tp_atr"], color="#1a73e8", lw=1.5, ls=":", alpha=0.8)
        ax.annotate(f"TP {p['tp_atr']}×ATR", (p["tp_atr"], -1.4), fontsize=8, color="#1a73e8", ha="center")
        # Partial TP
        ax.axvline(p["partial_progress"] * p["tp_atr"], color="#7b1fa2", lw=1.2, ls=":", alpha=0.8)
        ax.annotate(f"Partial {p['partial_progress']:.2f}×TP", (p["partial_progress"] * p["tp_atr"], -0.7),
                    fontsize=8, color="#7b1fa2", ha="center")
        # Locks N
        for i, (thresh, dist) in enumerate(p["trailing"]):
            ax.plot(thresh, thresh - dist, "o", color="#1a73e8", ms=5)
            ax.annotate(f"N{i+1}", (thresh, thresh - dist), textcoords="offset points",
                        xytext=(4, -14), fontsize=7, color="#1a73e8")

        # Zone "premier verrouillage" — différence clé entre symboles
        first_lock = p["trailing"][0][0]
        ax.axvline(first_lock, color="#d93025", lw=1.2, ls=":", alpha=0.7)
        ax.annotate(f"1er lock {first_lock}×ATR", (first_lock, -1.4), fontsize=8,
                    color="#d93025", ha="center", xytext=(0, -12), textcoords="offset points")

        ax.set_title(f"{sym}\nSL {p['sl_atr']}× · TP {p['tp_atr']}× · Partial {p['partial_progress']:.2f} · "
                     f"1er lock {first_lock}×", fontsize=10, color=color)
        ax.set_xlabel("Profit atteint (×ATR)")
        ax.set_ylabel("SL protégé (×ATR)")
        ax.set_xlim(0, 8.5)
        ax.set_ylim(-2.2, 8.5)
        ax.grid(alpha=0.3)
        if ax is axes[0]:
            ax.legend(loc="upper left", fontsize=8)

    plt.tight_layout(rect=[0, 0, 1, 0.94])
    out = "runtime/trailing_be_COMPARE.png"
    plt.savefig(out, dpi=130, bbox_inches="tight")
    plt.close(fig)
    print(f"OK — {out}")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "all"
    if target == "all" or target == "XAUUSD":
        make_detailed_fig("XAUUSD")
    if target == "all" or target == "BTCUSD":
        make_detailed_fig("BTCUSD")
    if target == "all" or target == "EURUSD":
        make_detailed_fig("EURUSD")
    if target == "all" or target == "compare":
        make_compare_fig()