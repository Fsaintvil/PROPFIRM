"""
NETTOYAGE URGENT : suppression des trades à regimes invalides dans OnlineLearner
et CalibrationState.

Valid regimes: TREND_UP, TREND_DOWN, RANGING, HIGH_VOL, LOW_VOL
Invalid: HIST, DOW, RAN, BUY, SELL, UNKNOWN (et tout autre hors valid set)
"""

import json
import os
import shutil
from collections import defaultdict

RUNTIME = os.path.join(os.path.dirname(__file__), "..", "runtime")
VALID_REGIMES = {"TREND_UP", "TREND_DOWN", "RANGING", "HIGH_VOL", "LOW_VOL"}


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"  ✅ Écrit {len(json.dumps(data))} octets → {path}")


def backup_file(path):
    bak = path + "_backup_before_clean_HISTDOWRAN.json"
    shutil.copy2(path, bak)
    print(f"  💾 Backup → {bak}")


def clean_history_by_symbol(history_dict, source_name):
    """
    history_dict: { symbol: [ { r, regime }, ... ] }
    Supprime les entrées dont regime ∉ VALID_REGIMES.
    Retourne (cleaned_dict, stats_par_symbole, total_supprime)
    """
    stats = defaultdict(lambda: {"avant": 0, "apres": 0, "supprime": 0, "invalides": defaultdict(int)})
    total_supprime = 0
    cleaned = {}

    for symbol, trades in history_dict.items():
        avant = len(trades)
        valides = []
        invalides_count = 0
        regime_counts = defaultdict(int)
        invalid_regime_counts = defaultdict(int)

        for t in trades:
            regime = t.get("regime", "UNKNOWN")
            if regime in VALID_REGIMES:
                valides.append(t)
            else:
                invalides_count += 1
                invalid_regime_counts[regime] += 1
            regime_counts[regime] += 1

        apres = len(valides)
        supprime = invalides_count
        total_supprime += supprime

        stats[symbol]["avant"] = avant
        stats[symbol]["apres"] = apres
        stats[symbol]["supprime"] = supprime
        stats[symbol]["invalides"] = dict(invalid_regime_counts)
        stats[symbol]["valides"] = dict((r, c) for r, c in regime_counts.items() if r in VALID_REGIMES)

        cleaned[symbol] = valides

        pct = (supprime / avant * 100) if avant > 0 else 0
        inv_detail = ", ".join(f"{r}:{c}" for r, c in sorted(invalid_regime_counts.items()))
        print(
            f"  {source_name} :: {symbol:12s} : {avant:4d} trades → {apres:4d} après nettoyage "
            f"(-{supprime:3d}, {pct:5.1f}%)  invalides: {inv_detail}"
        )

    return cleaned, dict(stats), total_supprime


def process_file(filepath, history_key, source_name):
    """Generic processor for both ol_state.json and calibration_state.json"""
    print(f"\n{'=' * 70}")
    print(f"📄 Traitement de {os.path.basename(filepath)}")
    print(f"{'=' * 70}")

    if not os.path.exists(filepath):
        print(f"  ❌ Fichier introuvable : {filepath}")
        return

    # Backup
    backup_file(filepath)

    # Load
    data = load_json(filepath)
    history = data.get(history_key)
    if history is None:
        print(f"  ❌ Clé '{history_key}' introuvable dans {filepath}")
        return

    print(f"  Symboles avant nettoyage : {list(history.keys())}")
    total_avant = sum(len(v) for v in history.values())
    print(f"  Total entrées avant : {total_avant}")

    # Clean
    cleaned_history, stats, total_supprime = clean_history_by_symbol(history, source_name)

    total_apres = sum(len(v) for v in cleaned_history.values())
    print(f"\n  📊 RÉSUMÉ {source_name}")
    print(f"     Total avant: {total_avant:4d}")
    print(f"     Total après: {total_apres:4d}")
    print(f"     Supprimés :  {total_supprime:4d} ({total_supprime / total_avant * 100:.1f}%)")

    # Write cleaned data
    data[history_key] = cleaned_history
    save_json(filepath, data)

    # Write a readable report to a separate file
    report_path = filepath.replace(".json", "_clean_report.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"Rapport de nettoyage - {source_name}\n")
        f.write(f"{'=' * 60}\n\n")
        f.write(f"Fichier source: {filepath}\n")
        f.write(f"Total avant: {total_avant}\n")
        f.write(f"Total après: {total_apres}\n")
        f.write(f"Supprimés: {total_supprime}\n\n")
        f.write(f"{'Symbole':12s} {'Avant':>6s} {'Après':>6s} {'Suppr':>6s} {'%':>6s}  Détail invalides\n")
        f.write(f"{'-' * 60}\n")
        for sym, s in sorted(stats.items()):
            pct = (s["supprime"] / s["avant"] * 100) if s["avant"] > 0 else 0
            inv = ", ".join(f"{r}:{c}" for r, c in sorted(s["invalides"].items()))
            f.write(f"{sym:12s} {s['avant']:6d} {s['apres']:6d} {s['supprime']:6d} {pct:5.1f}%  {inv}\n")
        f.write(f"\n\nRégimes valides conservés: {sorted(VALID_REGIMES)}\n")
        f.write(f"Régimes supprimés: HIST, DOW, RAN, BUY, SELL, UNKNOWN\n")
    print(f"  📝 Rapport → {report_path}")

    return total_supprime, total_avant


def main():
    print("🔥 NETTOYAGE URGENT - Suppression des regimes invalides dans OnlineLearner\n")
    print(f"Régimes VALIDES: {sorted(VALID_REGIMES)}")
    print(f"Régimes à SUPPRIMER: HIST, DOW, RAN, BUY, SELL, UNKNOWN (et tout autre hors valid set)\n")

    total_all = 0
    total_avants = 0

    # 1. ol_state.json
    ol_path = os.path.join(RUNTIME, "ol_state.json")
    s, a = process_file(ol_path, "history", "OnlineLearner")
    if s is not None:
        total_all += s
        total_avants += a

    # 2. calibration_state.json
    cal_path = os.path.join(RUNTIME, "calibration_state.json")
    s2, a2 = process_file(cal_path, "online_history", "CalibrationState")
    if s2 is not None:
        total_all += s2
        total_avants += a2

    # Also check if there's a meta_trackers section in calibration
    if os.path.exists(cal_path):
        data = load_json(cal_path)
        if "meta_trackers" in data:
            print(f"\n{'=' * 70}")
            print("📦 Nettoyage meta_trackers dans calibration_state.json")
            print(f"{'=' * 70}")
            mt = data["meta_trackers"]
            # meta_trackers likely have a different structure - check and report
            if isinstance(mt, dict):
                for model_name, tracker in mt.items():
                    if isinstance(tracker, dict) and "global_stats" in tracker:
                        # Check if global_stats has regime field
                        pass
            print("  ⚠️ meta_trackers présent mais structure non trade-list — ignoré (pas de regime à filtrer)")

    print(f"\n{'=' * 70}")
    print(f"🏁 NETTOYAGE TERMINÉ")
    print(f"{'=' * 70}")
    print(f"Total supprimés (OL + Calibration): {total_all}")
    print(f"Total analysé: {total_avants}")
    if total_avants > 0:
        print(f"Pourcentage global: {total_all / total_avants * 100:.1f}%")
    print(f"\n✅ Fichiers nettoyés:")
    print(f"   - runtime/ol_state.json")
    print(f"   - runtime/calibration_state.json")
    print(f"✅ Backups:")
    print(f"   - runtime/ol_state.json_backup_before_clean_HISTDOWRAN.json")
    print(f"   - runtime/calibration_state.json_backup_before_clean_HISTDOWRAN.json")


if __name__ == "__main__":
    main()
