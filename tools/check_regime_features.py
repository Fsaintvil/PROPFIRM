"""Diagnostic non-invasif pour les features utilisées par le détecteur HMM.

Usage: exécuter localement (hors production) pour vérifier NaN/Inf/constantes
et pour lancer un entraînement rapide du détecteur de régimes (mode verbose).

Ce script n'envoie aucun ordre et n'altère aucune donnée en production.
"""
import os
import json
from datetime import datetime

import numpy as np
import pandas as pd


def load_sample_or_synth():
    path = os.path.join("data", "features_sample.csv")
    if os.path.exists(path):
        df = pd.read_csv(path)
        if "Unnamed: 0" in df.columns:
            df = df.set_index("Unnamed: 0")
            try:
                df.index = pd.to_datetime(df.index)
            except Exception:
                pass
        return df

    # Générer données synthétiques non-invasives
    idx = pd.date_range(end=pd.Timestamp.now(), periods=500, freq="1T")
    np.random.seed(42)
    returns = np.random.normal(0, 0.0005, len(idx))
    price = 1.0 + np.cumsum(returns)
    df = pd.DataFrame({
        "open": price,
        "high": price * (1 + np.abs(np.random.normal(0, 0.0002, len(idx)))),
        "low": price * (1 - np.abs(np.random.normal(0, 0.0002, len(idx)))),
        "close": price,
        "volume": np.random.randint(10, 100, len(idx)),
    }, index=idx)
    return df


def basic_feature_check(df):
    # Extraire features via le module de détection s'il existe
    from scripts.market_regime_detection import MarketRegimeDetector

    detector = MarketRegimeDetector(n_regimes=3)
    features = detector.extract_regime_features(df)

    report = {
        "generated_at": datetime.now().isoformat(),
        "n_rows": int(features.shape[0]),
        "n_columns": int(features.shape[1]),
        "columns": {},
    }

    for c in features.columns:
        s = features[c]
        report["columns"][c] = {
            "dtype": str(s.dtype),
            "n_null": int(s.isna().sum()),
            "n_inf": int(np.isinf(s.values).sum()),
            "n_unique": int(s.nunique()),
            "min": float(s.min()) if s.size else None,
            "max": float(s.max()) if s.size else None,
            "var": float(s.var()) if s.size else None,
        }

    # Tenter un entraînement HMM (non-invasif) et capturer le score
    try:
        regimes, probs, X_scaled = detector.fit_hmm_model(features)
        try:
            score = detector.hmm_model.score(X_scaled) if detector.hmm_model is not None else None
        except Exception:
            score = None
        report["hmm_score"] = float(score) if score is not None else None
        report["hmm_used"] = detector.hmm_model is not None
    except Exception as e:
        report["hmm_error"] = str(e)

    # Exporter un résumé CSV avec percentiles pour faciliter l'inspection
    try:
        out_dir = os.path.join("artifacts", "diagnostics")
        os.makedirs(out_dir, exist_ok=True)
        csv_path = os.path.join(out_dir, "regime_features_summary.csv")
        percentiles = [0.001, 0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99, 0.999]
        rows = []
        for c in features.columns:
            arr = features[c].replace([np.inf, -np.inf], np.nan).dropna().values
            if arr.size == 0:
                q = {p: None for p in percentiles}
            else:
                q = {p: float(np.quantile(arr, p)) for p in percentiles}
            rows.append({"feature": c, **{f"p{int(p*1000)}": q[p] for p in percentiles}})

        pd.DataFrame(rows).to_csv(csv_path, index=False)
        report["summary_csv"] = csv_path
    except Exception:
        report["summary_csv"] = None

    out_path = os.path.join(out_dir, f"regime_features_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print("Diagnostic écrit:", out_path)
    print("Résumé CSV:", report.get("summary_csv"))
    return report


def main():
    df = load_sample_or_synth()
    report = basic_feature_check(df)
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
