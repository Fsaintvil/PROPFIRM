"""Test Anticipation Engine — entraînement USDCAD."""
import sys, logging, time
from pathlib import Path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
from engine_simple.anticipation import AnticipationData, DLModelWrapper

t0 = time.time()

# Préparer les données
data = AnticipationData("USDCAD", sequence_length=60).load()
data.prepare_target(horizon=12)
X, y = data.to_sequences(max_samples=20000)
elapsed = time.time() - t0
print(f"Sequences: {X.shape}, targets: {y.shape}, temps: {elapsed:.1f}s")

# Entraînement
t1 = time.time()
model = DLModelWrapper("USDCAD", input_size=X.shape[2]).build()
result = model.train(X, y, epochs=5, batch_size=512)
elapsed = time.time() - t1
print(f"Entraînement: accuracy={result['accuracy']:.4f}, temps: {elapsed:.1f}s")

# Test prédiction
seq = X[-1:].copy()
pred = model.predict(seq)
print(f"Prédiction sur dernière séquence: {pred}")

# Prédiction sur une séquence aléatoire
import numpy as np
rand_idx = np.random.randint(0, len(X))
rand_seq = X[rand_idx:rand_idx+1]
pred2 = model.predict(rand_seq)
true = y[rand_idx]
print(f"Prédiction séquence #{rand_idx}: {pred2} (vraie valeur: {'HAUSSE' if true else 'BAISSE'})")

# Sauvegarde
model.save()
print(f"Temps total: {time.time()-t0:.1f}s")
