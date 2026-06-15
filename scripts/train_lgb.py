#!/usr/bin/env python
"""
PHASE 4: Train LightGBM model pour prédiction de direction de trade.

Usage:
    python scripts/train_lgb.py  [--min-trades 50] [--output models/lgb_model.pkl]
"""
import json
import logging
import sys
from pathlib import Path

import numpy as np

logger = logging.getLogger("train_lgb")
logging.basicConfig(level=logging.INFO)

# Ajouter le root directory au path pour importer les modules locaux
sys.path.insert(0, str(Path(__file__).parent.parent))

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Train LightGBM model")
    parser.add_argument("--min-trades", type=int, default=50, help="Minimum trades to train")
    parser.add_argument("--output", type=str, default="models/lgb_model.pkl", help="Output model path")
    args = parser.parse_args()
    
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Load trade history from runtime state
    state_file = Path("runtime/robot_state.json")
    if not state_file.exists():
        logger.error("No robot_state.json found — no trades to train on")
        return
    
    with open(state_file) as f:
        state = json.load(f)
    
    trade_history = state.get("trade_history", [])
    logger.info(f"Loaded {len(trade_history)} trades from history")
    
    if len(trade_history) < args.min_trades:
        logger.warning(f"Only {len(trade_history)} trades, need {args.min_trades}")
        return
    
    # Extract features and labels from trades
    X, y = [], []
    feature_names = None
    
    for trade in trade_history[-200:]:  # Use last 200 trades
        # Basic features that we can extract from trade data
        features = {
            "profit": trade.get("profit", 0),
            "atr_pct": trade.get("atr_pct", 0),
            "adx": trade.get("adx", 20),
            "rsi": trade.get("rsi", 50),
            "confidence": trade.get("confidence", 0.5),
            "score": trade.get("score", 0.5),
        }
        
        if feature_names is None:
            feature_names = list(features.keys())
        
        X.append([features.get(name, 0) for name in feature_names])
        # Label: 1 if profitable, 0 otherwise
        y.append(1 if trade.get("profit", 0) > 0 else 0)
    
    if len(X) < args.min_trades:
        logger.warning(f"Only {len(X)} valid feature sets, need {args.min_trades}")
        return
    
    X = np.array(X)
    y = np.array(y)
    
    logger.info(f"Training LGB with {len(X)} samples, {len(feature_names)} features")
    logger.info(f"Classes: {np.bincount(y)}")
    
    try:
        import lightgbm as lgb
        from sklearn.model_selection import train_test_split
        
        # Split data
        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
        
        # Train model
        params = {
            "objective": "binary",
            "metric": "auc",
            "num_leaves": 31,
            "learning_rate": 0.05,
            "feature_fraction": 0.8,
            "bagging_fraction": 0.8,
            "bagging_freq": 5,
            "verbose": -1,
        }
        
        train_data = lgb.Dataset(X_train, label=y_train, feature_names=feature_names)
        valid_data = lgb.Dataset(X_test, label=y_test, reference=train_data)
        
        model = lgb.train(
            params,
            train_data,
            num_boost_round=100,
            valid_sets=[valid_data],
            callbacks=[
                lgb.early_stopping(stopping_rounds=20),
                lgb.log_evaluation(period=10),
            ],
        )
        
        # Evaluate
        from sklearn.metrics import roc_auc_score, accuracy_score
        y_pred = model.predict(X_test)
        y_pred_binary = (y_pred > 0.5).astype(int)
        
        auc = roc_auc_score(y_test, y_pred)
        acc = accuracy_score(y_test, y_pred_binary)
        
        logger.info(f"Test AUC: {auc:.4f}, Accuracy: {acc:.4f}")
        
        # Save model
        model.save_model(str(output_path))
        logger.info(f"Model saved to {output_path}")
        
    except ImportError:
        logger.error("LightGBM or scikit-learn not installed")
        logger.info("Install with: pip install lightgbm scikit-learn")

if __name__ == "__main__":
    main()
