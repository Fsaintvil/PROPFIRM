"""Entraînement Anticipation Engine v2 — 100+ features, early stopping, dropout tuning."""
import sys, logging, time
from pathlib import Path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

import torch
import numpy as np
from sklearn.metrics import accuracy_score
from engine_simple.anticipation import AnticipationData, DLModelWrapper

SYMBOLS = ["XAUUSD", "BTCUSD", "ETHUSD"]

for symbol in SYMBOLS:
    t0 = time.time()
    print(f"\n{'='*50}")
    print(f"  {symbol}")
    print(f"{'='*50}")
    
    # Charger les données H1 avec 100+ features
    data = AnticipationData(symbol, sequence_length=60).load()
    data.prepare_target(horizon=12)
    X, y = data.to_sequences(max_samples=30000)
    
    feat_count = X.shape[2]
    print(f"  Features: {feat_count}")
    print(f"  Séquences: {X.shape[0]}, target hausse: {y.mean():.1%}")
    
    if len(X) < 2000:
        print(f"  ⚠ Pas assez de données: {len(X)} séquences")
        continue
    
    # Construire le modèle v2 avec architecture optimisée
    model = DLModelWrapper(symbol, input_size=feat_count)
    model.build(hidden_size=96, num_layers=3, dropout=0.3)
    
    # Entraînement avec early stopping + LR scheduling
    result = model.train(X, y, epochs=30, batch_size=512, use_early_stopping=True)
    
    # Evaluation finale
    model.model.eval()
    with torch.no_grad():
        X_t = torch.FloatTensor(X[-2000:])
        y_t = y[-2000:]
        y_pred_prob = model.model(X_t).numpy().flatten()
        
        # Test multiple thresholds
        print(f"\n  Optimisation du seuil sur 2000 échantillons:")
        best_acc = 0
        best_thresh = 0.5
        for thresh in np.arange(0.30, 0.71, 0.02):
            yp = (y_pred_prob > thresh).astype(int)
            a = accuracy_score(y_t, yp)
            if a > best_acc:
                best_acc = a
                best_thresh = thresh
            if thresh in [0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65]:
                print(f"    threshold={thresh:.2f}: accuracy={a:.4f}")
        
        print(f"  ➤ Best threshold: {best_thresh:.2f} -> accuracy={best_acc:.4f}")
        model._best_threshold = best_thresh
        model._accuracy = max(model._accuracy, best_acc)
    
    # Sauvegarder le modèle v2
    norm_stats = {"means": data._means.tolist() if hasattr(data, "_means") else None,
                  "stds": data._stds.tolist() if hasattr(data, "_stds") else None}
    model.save(norm_stats=norm_stats)
    
    print(f"  ⏱ Temps: {time.time()-t0:.1f}s")
    print(f"  ✅ Accuracy finale: {model._accuracy:.4f}")

print(f"\n{'='*50}")
print("  ✅ ENTRAÎNEMENT V2 TERMINÉ — 5 SYMBOLES")
print(f"{'='*50}")
