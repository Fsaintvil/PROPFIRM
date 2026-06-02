# MT5 FTMO IA.7 - EXECUTIVE SUMMARY
**27 Mai 2026** | **Robot ACTIF** | **Balance: $199,792** | **DD: 0%**

---

## 🎯 VUE D'ENSEMBLE

| Aspect | Détail |
|--------|--------|
| **Statut** | 🟢 En trading (7 positions, 6 cycles testés) |
| **Balance** | $199,792 (↑ de $199,385) |
| **Drawdown** | 0% (excellent) |
| **Win Rate** | 64.6% (48 trades fermés) |
| **Daily Loss** | -0.21% (limite: 2%) |
| **Target FTMO** | +10% profit = $220,000 |
| **Magic Number** | 999001 |
| **Cycle** | 15 secondes |

---

## 🏗️ ARCHITECTURE CORE

### 3 Piliers
1. **Génération Signaux** (MOM20x3 + Confluence)
   - Multi-timeframe (M15, H1, D1)
   - ADX regime filter (trending vs ranging)
   - Score: 0-0.99 (confluence EMA/RSI/MACD/Structure)

2. **Intelligence Adaptative** (5 régimes × 5 modèles)
   - MarketRegime: détecte TREND_UP/DOWN/RANGING/HIGH/LOW_VOL
   - OnlineLearner: adapte thresholds sur 50 derniers trades
   - MetaLearner: combine RF/XGB/LGBM/DL_LSTM/MOM20x3 avec poids dynamiques

3. **Protections FTMO** (Strictes)
   - Max DD: 10% (peak ou initial)
   - Max daily loss: 2%
   - Consistency: max 30% single trade
   - Trailing SL: 4 niveaux ATR
   - Spread check: symbol-specific limits

---

## 📊 STRATÉGIE SIGNAUX

**MOM20x3 Breakout**
```
close[i] - close[i-20] > threshold × ATR → SIGNAL

Thresholds (ADX-based):
  - TRENDING (ADX≥20): 2.5×ATR
  - RANGING (ADX<20): 2.0×ATR

Multi-TF Confluence:
  D1 (weight 1.5) + H4 (1.2) + H1 (1.0) + M15 (0.7)
  → Majority vote ≥55% required

Final Score: 0.40 + confluence×0.20 + session_weight×0.05
```

---

## 🤖 INTELLIGENCE ADAPTATIVE

### MarketRegime (5 régimes)
| Régime | Condition | SL | TP |
|--------|-----------|----|----|
| TREND_UP | ADX>20, MA>0.2%, RSI>50 | 3×ATR | 6×ATR |
| TREND_DOWN | ADX>20, MA<-0.2%, RSI<50 | 3×ATR | 6×ATR |
| RANGING | ADX≤20 | 2×ATR | 4×ATR |
| HIGH_VOL | ATR%>80% | 2.5×ATR | 5×ATR, risk×0.7 |
| LOW_VOL | ATR%<20% | 2×ATR | 4×ATR |

### OnlineLearner (Fenêtre 50 trades)
- WR > 82%: seuil -0.5 (agressif), risk +15%
- WR < 70%: seuil neutre, risk -25%
- Expectancy < 0: risk -50%
- Pause après 2 pertes consécutives

### MetaLearner (5 modèles)
- RF, XGB, LGBM: **DÉSACTIVÉS** (45% accuracy)
- DL_LSTM: **ACTIF** (1558 séquences, 47 features, pré-entraîné)
- MOM20x3: **ACTIF** (base signal)
- Poids dynamiques par régime
- Devil's Advocate: risque÷2 si fort désaccord

---

## 💾 MODÈLES ML

### DL LSTM (Seul ML Actif)
- **File**: models/dl_lstm_all.pkl (261 KB)
- **Input**: 20 bars × 47 features (momentum, vol, RSI, MACD, structure)
- **Architecture**: 2 layers LSTM, 64 hidden, dropout 0.2/0.3
- **Output**: [0,1] probabilité UP
- **Training**: 1558 séquences, 10 epochs, loss=0.60
- **Status**: Pré-entraîné, figé (aucun fine-tuning online)

### ML Ensemble (DÉSACTIVÉ)
- 300+ modèles (RF/XGB/LGBM × symbol × TF × variant)
- Accuracy directionnelle: 45% (pire qu'aléatoire)
- **Raison suppression**: 581 MB RAM pour rien
- **Verdict**: Info-only, trop coûteux

---

## 🛡️ PROTECTIONS FTMO (STRICTES)

### Drawdown & Daily Loss
- DD max: 10% (peak ou initial) → FAIL
- Daily loss max: 2% → FAIL
- Daily profit ≥0.3% → Risk ÷75%
- Consistency: max 30% single trade → FAIL

### Position Management
- Max 25 positions total
- Max 1 position/symbole (BOTTLENECK!)
- Cooldown 30min après loss
- Pause après 2 losses consécutives

### Trailing SL (4 niveaux ATR)
```
profit > 5.0×ATR  → SL = peak - 0.15×ATR
profit > 3.0×ATR  → SL = peak - 0.25×ATR
profit > 1.5×ATR  → SL = peak - 0.35×ATR
profit > 0.5×ATR  → SL = peak - 0.5×ATR
```

### Spread Limits (Symbol-specific)
- XAUUSD: 150 pts max
- ETHUSD: 100 pts max
- GBPJPY: 60 pts max
- Autres: 50 pts max

---

## 📁 STRUCTURE FICHIERS

### CORE (À GARDER)
```
✅ main.py (600+ lignes)           - Boucle 15s, orchestration
✅ engine_simple/
   ├─ signals.py                   - MOM20x3 + confluence
   ├─ adaptive_intelligence.py      - Régimes + OnlineLearner + MetaLearner
   ├─ ftmo_protector.py            - DD/daily/consistency checks
   ├─ dl_ensemble.py               - LSTM pré-entraîné
   ├─ meta_learner.py              - Combinaison 5 modèles
   ├─ mt5_connector.py             - Connexion MT5
   ├─ trade_journal.py             - SQLite historique
   ├─ indicators.py                - EMA, RSI, MACD, ATR, etc.
   ├─ market_structure.py          - HH/HL/LH/LL, BOS, CHOCH
   ├─ ml_features.py               - 47 features
   ├─ notifier.py                  - Telegram alerts
   └─ news_filter.py               - Filtrage news
✅ config_simple.py                - Config Python
✅ config/config.json              - Config JSON
```

### DEAD CODE (À Supprimer)
```
❌ analyze_trades.py               - Analysis ad-hoc (~200 lignes)
❌ analyse_definitive.py           - Static analysis (~250 lignes)
❌ analyse_risque.py               - Monte Carlo/VaR (~300 lignes)
❌ monitor.py                      - Monitoring manuel (~200 lignes)
❌ watchdog.py                     - Old watchdog (~250 lignes)
❌ monitor_continuous.py           - Continuous monitoring
❌ report_continuous.py            - Continuous reporting
❌ analyze_report.py               - Parse Excel
❌ start_robot.bat                 - Batch script
❌ surveillance.py                 - Parallel surveillance

engine_simple/:
❌ step1_parse_reports.py          - Parse Excel offline
❌ step2_validate_ml.py            - Validate ML offline
❌ step3_train_dl_calibrate.py     - Train DL offline

Total Dead Code: ~3000 lignes
```

### SOFT DEAD (Non utilisé)
- **session_analyzer.py**: Coded but not called (session weighting exists but not optimized)
- **calibrate_all.py**: Setup one-time (garder si ré-training)

---

## 🔴 PROBLÈMES CRITIQUES

### 1. MAX_POSITIONS_PER_SYMBOL = 1 (BOTTLENECK)
```
Problème:
  - Robot ne peut ouvrir qu'1 trade par symbole
  - Nouveau signal rejeté si symbole déjà 1 position
  - Sous-utilisation du capital

Solution:
  MAX_POSITIONS_PER_SYMBOL = 2 ou 3
  + Garder correlation groups limit
```

### 2. ML Ensemble Désactivé (45% Accuracy)
```
Problème:
  - 300+ modèles codés mais non utilisés
  - 581 MB RAM économisé
  - Perte d'opportunités confluence

Solution:
  - Réentraîner ML (target 60%+ accuracy)
  - Ou supprimer entirely (cost/benefit très faible)
```

### 3. News Filter Inactif (APIs Bloquées)
```
Problème:
  - TradingView + ForexFactory retournent 403
  - Pas de protection avant annonces économiques

Solution:
  - Alternative API (Bloomberg, Investing.com)
  - Ou gérer via symboles (skip high-impact pairs)
```

### 4. Session Analyzer Non Intégré
```
Problème:
  - Code exists mais jamais appelé
  - Session weight calculé mais non optimisé

Solution:
  - Intégrer dans confluence scoring
  - Boost durant 12-16h UTC (London/NY overlap)
```

### 5. DL LSTM Figé (Pas d'Online Adaptation)
```
Problème:
  - Trained sur 1558 séquences historiques
  - Aucun fine-tuning online
  - Pourrait dériver dans new conditions

Solution:
  - Fine-tune tous les 100 trades
  - Ou utiliser comme feature (pas prédiction)
```

---

## 📈 PERFORMANCE ACTUELLE

### Account Metrics
| Métrique | Valeur |
|----------|--------|
| Balance | $199,792 |
| Equity | $199,311 |
| Floating P&L | -$481 |
| Daily Loss | -0.21% |
| Drawdown | 0% |
| Positions | 7 |
| Trades Closed | 48 |
| Win Rate | 64.6% |
| Avg R Multiple | 0.85 |

### Positions Ouvertes
```
EURUSD:   -$54
GBPUSD:   -$34
GBPJPY:   -$31
USDCHF:   -$33
NZDUSD:   -$8
XAUUSD:   -$142  (High volatility)
USOIL:    -$16
```

### Targets
```
Challenge: +$20,000 (10%) → $220,000 = PASS
Current:   -$208 loss (de $200K initial)
Target:    10 days minimum trading (current: 6 days)
```

---

## 💡 INSIGHTS POUR OPTIMISATION

### Quick Wins
1. ✅ **Augmenter MAX_POSITIONS_PER_SYMBOL** (1→2)
   - Leverage capital mieux
   - Diversify positions
   - Gain: +50% more trade capacity

2. ✅ **Intégrer Session Analyzer**
   - Boost confluence durant 12-16h UTC
   - Reduce faux signaux hors sessions

3. ✅ **Nettoyer Dead Code**
   - Supprimer 3000+ lignes inutilisées
   - Réduit complexité

4. ✅ **Implémenter News Filter Alternatif**
   - Protéger avant annonces économiques
   - Éviter volatilité extrême

### Medium-Term Improvements
5. 🔄 **Fine-tune DL LSTM Online**
   - Recalibrate tous 100 trades
   - Adapter aux new market conditions

6. 🔄 **Réentraîner ML Ensemble**
   - Target 60%+ accuracy
   - Ou supprimer si cost/benefit faible

7. 🔄 **Optimiser Devil's Advocate**
   - Tester threshold (0.7 vs 0.5 vs 0.8)
   - Balance conservatisme/opportunité

---

## 🎮 COMMANDES CLÉS

```powershell
# Démarrer le robot
python main.py

# Voir l'état
cat runtime/robot_state.json
cat runtime/ftmo_report.json

# Tests
pytest tests/

# Calibrer sur historique (one-time setup)
python calibrate_all.py

# Stop graceful
Ctrl+C  # Laisse FTMO sauvegarder l'état
```

---

## ✅ VERDICT PROFESSIONNEL

### Strengths
✅ Architecture solide + modulaire  
✅ Learner adaptatif en temps réel  
✅ 5 régimes détectés intelligemment  
✅ Protections FTMO rigoureuses  
✅ Trailing SL intelligent (4 niveaux)  
✅ PID lock empêche duplication  

### Weaknesses
⚠️ ML Ensemble inefficace (45% accuracy)  
⚠️ Positions bloquées (MAX=1/symbol)  
⚠️ Session analyzer non utilisé  
⚠️ News filter inactif  
⚠️ DL LSTM figé  

### Opportunities
💡 Augmenter MAX_POSITIONS_PER_SYMBOL  
💡 Intégrer session analyzer  
💡 Réentraîner ML  
💡 Implémenter news filter  
💡 Fine-tune DL online  

---

**Status**: 🟢 READY FOR PROFESSIONAL ANALYSIS

Document généré: 27 Mai 2026  
Fichiers détaillés: ANALYSE_COMPLETE_PROJET.md
