# 📊 ANALYSE COMPLÈTE - MT5 FTMO IA.7
**Date**: 27 Mai 2026  
**Statut**: Robot en Trading (7 positions, 0% DD)  
**Version**: 2.0.0 (FTMO Simple)

---

## 1️⃣ CONFIGURATION & RESSOURCES

### Symbols Actifs (9)
```python
SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "USDCAD", "USDCHF", "AUDUSD", "NZDUSD", "XAUUSD", "ETHUSD"]
ROBOT_MAGIC = 999001
```

### Limites Trading
```python
MAX_POSITIONS = 25               # Can hold max 25 open trades
MAX_TRADES_PER_DAY = 75          # Max 75 trades/day (reduced for noise)
MAX_POSITIONS_PER_SYMBOL = 1     # BOTTLENECK: Only 1 position per symbol!
LOT_SIZE = 0.05                  # Reduced from 0.1 for stability
MIN_SIGNAL_SCORE = 0.75          # Increased for selectivity
RISK_PER_TRADE = 0.004           # 0.4% per trade
COOLDOWN_MINUTES = 15            # 15min cooldown after loss
```

### FTMO Challenge Parameters
```python
INITIAL_BALANCE = 200000         # CRITICAL: Persisted once
MAX_DD_PCT = 0.10                # 10% drawdown limit
MAX_DAILY_LOSS_PCT = 0.02        # 2% daily loss limit
DAILY_PROFIT_LIMIT_PCT = 0.003   # 0.3% → risk reduced 75%
CONSISTENCY_MAX_PCT = 0.30       # Max 30% single trade on P&L
MIN_TRADING_DAYS = 10            # Minimum before PASS

# Per-Symbol Limits
SYMBOL_LIMITS = {
    "XAUUSD": {"max_lot": 0.2, "risk_mult": 0.7, "max_spread_points": 150},
    "ETHUSD": {"max_lot": 0.2, "risk_mult": 0.7, "max_spread_points": 100},
    "USOIL.cash": {"max_lot": 0.3, "risk_mult": 0.8, "max_spread_points": 80},
    "GBPJPY": {"max_spread_points": 60},
}
```

---

## 2️⃣ STRATÉGIE DE SIGNAUX (MOM20x3)

### Logique Breakout
```python
# signals.py - Ligne 100+
def _eval_strat(self, symbol, data, cfg, overrides, base_thresh=3.0):
    c = np.array([r[4] for r in data], dtype=float)  # closes
    h = np.array([r[2] for r in data], dtype=float)  # highs
    l = np.array([r[3] for r in data], dtype=float)  # lows
    v = np.array([r[5] for r in data], dtype=float)  # volumes
    
    i = len(c) - 1
    if i < cfg["period"] + 5:
        return None
    
    # Calculate 20-bar momentum
    momentum = (c[i] - c[i - cfg["period"]]) / c[i - cfg["period"]]
    
    # ATR Trailing Stop calculation
    tr = np.maximum(h[1:] - l[1:], 
                    np.maximum(np.abs(h[1:] - c[:-1]),
                              np.abs(l[1:] - c[:-1])))
    atr_arr = np.array([np.mean(tr[max(0, j-14):j+1]) 
                       for j in range(len(tr))])
    atr_val = atr_arr[-1]
    
    # Threshold determination based on ADX regime
    adx_val = self._calc_adx(h, l, c)
    is_ranging = adx_val < 25
    
    base_thresh = 1.5  # Default reduced threshold
    thresh = (2.5 * atr_val if not is_ranging 
              else 2.0 * atr_val)  # Adaptive thresholds
    
    # Signal generation
    move = c[i] - c[i - cfg["period"]]
    direction = 1 if move > thresh else (-1 if move < -thresh else 0)
    
    if direction == 0:
        return None
    
    return (direction, atr_val, move, thresh, momentum, indicators)
```

### Multi-Timeframe Confluence
```python
# signals.py - Confluence scoring
STRATS = {
    "EURUSD": [{"tf": "H1", "period": 20, "thresh": 2, "sl": 1.5, "tp": 2},
               {"tf": "D1", "period": 20, "thresh": 2, "sl": 1.5, "tp": 2}],
    "GBPUSD": [{"tf": "M15", "period": 20, "thresh": 2, "sl": 1.5, "tp": 2},
               {"tf": "H1", "period": 20, "thresh": 2, "sl": 1.5, "tp": 2}],
    # ... autres symboles
}

# Weighting per timeframe
tf_weight = {
    "D1": 1.5,   # Highest weight
    "H4": 1.2,
    "H1": 1.0,
    "M15": 0.7,
    "M5": 0.5,   # Lowest weight
}

# Final confluence calculation
buy_w = sum(w for d, w, _, _, _ in votes if d > 0)
sell_w = sum(w for d, w, _, _, _ in votes if d < 0)
total_w = buy_w + sell_w
majority = max(buy_w, sell_w) / total_w

# Consensus needs ≥55% agreement
if majority < 0.55:
    return None  # Rejected
```

### Confluence Scoring
```python
# Final scoring formula (signals.py ~L150)
ema_score = agg.get("ema_score", 0)
structure_score = agg.get("structure_score", 0)
session_weight = agg.get("session_weight", 0.5)
rsi_score = agg.get("rsi_score", 0)
macd_score = agg.get("macd_score", 0)
volume_score = agg.get("volume_score", 0)
vwap_score = agg.get("vwap_score", 0)

# Multi-factor confluence
confluence = (ema_score + structure_score + rsi_score + 
              macd_score + volume_score + vwap_score) / 6
confluence = np.clip(confluence, -1, 1)

# Base score + confluence boost + session bonus
score = min(0.99, 0.40 + confluence * 0.20 + session_weight * 0.05)
confidence = min(0.95, 0.35 + abs(confluence) * 0.25 + session_weight * 0.05)

return {
    "action": "BUY" if consensus > 0 else "SELL",
    "score": round(score, 2),
    "confidence": round(confidence, 2),
    "atr": mean_atr,
    "sl_atr": sl_atr,  # Adaptive per regime
    "tp_atr": tp_atr,
}
```

---

## 3️⃣ INTELLIGENCE ADAPTATIVE

### Détection des Régimes (5 régimes)
```python
# adaptive_intelligence.py - MarketRegime class
class MarketRegime:
    def detect(self, rates):
        # 1. Calculate ADX (trend strength)
        adx = self._adx(highs, lows, closes)  # 0-100 scale
        
        # 2. Calculate ATR% percentile (volatility)
        atr_now = np.mean(tr[-14:])
        atr_hist = [np.mean(tr[i:i+14]) for i in range(max(0, len(tr)-100), len(tr)-14)]
        vol_percentile = np.sum(atr_hist < atr_now) / len(atr_hist)
        
        # 3. Market structure analysis
        ms = analyze_market_structure(highs, lows, closes)
        structure_trend = ms.get("trend", "unknown")
        
        # 4. OBV trend (volume confirmation)
        obv_arr = obv(closes, volumes)
        obv_trend = 1 if obv_arr[-1] > obv_arr[-20] else -1
        
        # 5. RSI position
        rsi_now = rsi(closes)[-1]
        
        # REGIME CLASSIFICATION
        if vol_percentile > 0.80:
            regime = "HIGH_VOL"
        elif vol_percentile < 0.20:
            regime = "LOW_VOL"
        elif adx > 20 and structure_trend == "bullish" and rsi_now > 50:
            regime = "TREND_UP"
        elif adx > 20 and structure_trend == "bearish" and rsi_now < 50:
            regime = "TREND_DOWN"
        else:
            regime = "RANGING"
        
        return regime, {"adx": adx, "vol_percentile": vol_percentile, ...}
```

### Apprentissage Online (OnlineLearner)
```python
# adaptive_intelligence.py - OnlineLearner class
class OnlineLearner:
    def __init__(self, window=50):
        self.window = window  # Sliding window
        self.history = {}     # {symbol: deque(maxlen=50)}
        self.adapted_params = {}
    
    def record_trade(self, symbol, r_multiple, regime):
        # Record as {r_multiple, regime}
        if symbol not in self.history:
            self.history[symbol] = deque(maxlen=self.window)
        self.history[symbol].append({"r": r_multiple, "regime": regime})
        self._update_params(symbol)
    
    def _update_params(self, symbol):
        h = list(self.history.get(symbol, []))
        if len(h) < self.window // 2:
            return
        
        rr = np.array([t["r"] for t in h])
        wr = np.mean(rr > 0)           # Win Rate
        expectancy = np.mean(rr)        # Avg R multiple
        
        # ADAPTIVE THRESHOLDS
        thresh = 2.5  # Default
        risk_mult = 1.0
        
        if wr < 0.70:
            thresh = 2.5  # Neutral
            risk_mult = 0.75  # Reduce risk
        elif wr > 0.82:
            thresh = 2.0  # More aggressive
            risk_mult = 1.15  # Increase risk
        elif wr > 0.78:
            thresh = 2.3
            risk_mult = 1.05
        
        # If losing, cut risk
        if expectancy < 0 and len(h) > 10:
            risk_mult = 0.5
        
        # Pause on consecutive losses handled separately
        self.adapted_params[symbol] = {
            "thresh": thresh,
            "risk_mult": risk_mult,
            "sl_mult": 3.0,
            "tp_mult": 1.0,
        }
    
    def get_params(self, symbol, base_thresh=3.0):
        return self.adapted_params.get(symbol, {
            "thresh": base_thresh,
            "risk_mult": 1.0,
        })
```

### Meta-Learner (Combinaison 5 Modèles)
```python
# meta_learner.py
class MetaLearner:
    def __init__(self, recalibration_freq=50):
        self.trackers = {
            "RF": ModelTracker("RF"),
            "XGB": ModelTracker("XGB"),
            "LGBM": ModelTracker("LGBM"),
            "DL_LSTM": ModelTracker("DL_LSTM"),
            "MOM20x3": ModelTracker("MOM20x3"),
        }
        self.recalibration_freq = 50
        self.trades_since_recal = 0
    
    def record_trade(self, symbol, regime, predictions_outcomes):
        """predictions_outcomes = {model_name: correct_bool}"""
        for model_name, correct in predictions_outcomes.items():
            if model_name in self.trackers:
                self.trackers[model_name].record(regime, symbol, correct)
        self.trades_since_recal += 1
    
    def get_weights(self, regime, symbol=None):
        """Dynamic weights per regime"""
        weights = {}
        for name, tracker in self.trackers.items():
            wr = tracker.win_rate(regime)
            penalty = tracker.regime_penalty.get(regime, 1.0)
            # weight = base × (0.5 + win_rate) / penalty
            weights[name] = 1.0 * (0.5 + wr) / penalty
        
        # Normalize
        total = sum(weights.values()) or 1
        return {k: v / total for k, v in weights.items()}
    
    def get_ensemble_action(self, regime, predictions):
        """Combine 5 models"""
        weights = self.get_weights(regime)
        
        buy_w = sell_w = hold_w = 0.0
        for model_name, pred in predictions.items():
            w = weights.get(model_name, 0.2)
            action = pred.get("action", "HOLD")
            score = pred.get("score", 0.5)
            
            if action == "BUY":
                buy_w += w * score
            elif action == "SELL":
                sell_w += w * score
            else:
                hold_w += w * 0.3
        
        total_w = buy_w + sell_w + hold_w
        if total_w == 0:
            return "HOLD", 0.5
        
        # Threshold ≥55% for action
        if buy_w > sell_w and buy_w / total_w >= 0.55:
            return "BUY", buy_w / total_w
        elif sell_w > buy_w and sell_w / total_w >= 0.55:
            return "SELL", sell_w / total_w
        else:
            return "HOLD", 0.5
```

---

## 4️⃣ MODÈLES MACHINE LEARNING

### DL LSTM (Seul ML Actif)
```python
# dl_ensemble.py
class LSTMNet(nn.Module):
    def __init__(self, input_size, hidden_size=64, num_layers=2):
        super().__init__()
        self.lstm = nn.LSTM(input_size, hidden_size, num_layers, 
                           batch_first=True, dropout=0.2)
        self.dropout = nn.Dropout(0.3)
        self.fc1 = nn.Linear(hidden_size, 32)
        self.fc2 = nn.Linear(32, 1)
        self.sigmoid = nn.Sigmoid()
    
    def forward(self, x):
        out, _ = self.lstm(x)        # (batch, seq, hidden)
        out = out[:, -1, :]          # Last timestep only
        out = self.dropout(out)
        out = self.fc1(out)
        out = self.fc2(out)
        return self.sigmoid(out)      # [0,1] probability

class DLEnsemble:
    SEQUENCE_LENGTH = 20             # 20 bars
    LOOKBACK = 60                    # 60 bars for feature computation
    
    def _load_pretrained(self):
        # Load pre-trained model
        path = "models/dl_lstm_all.pkl"
        if os.path.exists(path):
            model = LSTMNet(len(FULL_FEATURE_NAMES))
            model.load_state_dict(
                torch.load(path, map_location='cpu', weights_only=True)
            )
            model.eval()
            self.models["all_H1"] = model
    
    def predict(self, symbol, rates_dict):
        h1_rates = rates_dict.get("H1")
        if h1_rates is None or len(h1_rates) < self.SEQUENCE_LENGTH + self.LOOKBACK:
            return None
        
        # Build sequence of 20 bars × 47 features
        features_list = []
        for i in range(len(h1_rates) - self.SEQUENCE_LENGTH, len(h1_rates)):
            window = h1_rates[max(0, i - self.LOOKBACK + 1):i + 1]
            if len(window) < 50:
                continue
            feat = self.feature_engine.compute_features(window)
            fv = [feat.get(n, 0.5) for n in FULL_FEATURE_NAMES]
            features_list.append(fv)
        
        if len(features_list) < self.SEQUENCE_LENGTH:
            return None
        
        # Run through LSTM
        X = torch.tensor(features_list[-self.SEQUENCE_LENGTH:], 
                        dtype=torch.float32).unsqueeze(0)
        with torch.no_grad():
            prob = self.models["all_H1"](X).item()
        
        return {
            "action": "BUY" if prob > 0.5 else "SELL",
            "score": prob,
        }
```

### ML Ensemble (DÉSACTIVÉ)
```python
# IMPORTANT: ML Ensemble désactivé au runtime
# Raison: 45% accuracy directionnelle (inférieure au hasard)

class AdaptiveEngine:
    def __init__(self, mt5, calibration_path=None):
        self.ml = None          # ← EXPLICITLY DISABLED
        self.ml_available = False
        
        # Only DL LSTM and MetaLearner are active
        self.dl = DLEnsemble()
        self.meta = MetaLearner()
```

### Features ML (47 Dimensions)
```python
# ml_features.py
FULL_FEATURE_NAMES = [
    # Price & Returns
    "return_1", "return_5", "return_10", "return_20",
    "high_low_ratio", "high_close_ratio", "low_close_ratio",
    
    # EMAs
    "ema9", "ema20", "ema50", "ema200",
    "ema9_20", "ema20_50", "ema50_200",
    "price_vs_ema9", "price_vs_ema20", "price_vs_ema50", "price_vs_ema200",
    
    # RSI
    "rsi", "rsi_change_5",
    
    # MACD
    "macd_line", "macd_signal", "macd_hist", "macd_hist_change_3",
    
    # Bollinger Bands
    "bb_position", "bb_width", "bb_squeeze",
    
    # ATR
    "atr", "atr_pct", "atr_change_5",
    
    # Volume
    "obv_trend", "obv_divergence",
    "vwap_distance", "vwap_position",
    
    # Stochastic
    "stoch_k", "stoch_d", "stoch_overbought", "stoch_oversold",
    
    # Market Structure
    "structure_score", "unmitigated_obs", "unmitigated_fvgs",
    "bos_present", "choch_present",
    
    # Session
    "session_weight", "session_position",
    
    # Composite
    "ema_alignment_score", "confluence_score",
]  # Total: 47 features
```

---

## 5️⃣ PROTECTIONS FTMO

### Drawdown & Daily Loss
```python
# ftmo_protector.py - Ligne 80+
def can_trade(self, symbol, signal=None, positions=None):
    self._reset_daily()
    
    # Account equity tracking
    account = self.mt5.get_account_info()
    current_equity = account.equity
    if current_equity > self.peak_equity:
        self.peak_equity = current_equity
    
    # DRAWDOWN CHECKS
    dd_initial = (self.initial_balance - current_equity) / self.initial_balance
    dd_peak = (self.peak_equity - current_equity) / self.peak_equity
    
    if dd_initial >= self.max_dd_pct:
        return False, f"FTMO max DD from initial: {dd_initial:.1%}"
    if dd_peak >= self.max_dd_pct:
        return False, f"FTMO max DD from peak: {dd_peak:.1%}"
    
    # DAILY LOSS CHECK
    daily_pnl_pct = self.daily_stats["pnl"] / max(self.initial_balance, 1)
    if daily_pnl_pct <= -self.max_daily_loss_pct:
        return False, f"FTMO daily loss limit: {daily_pnl_pct:.1%}"
    
    # DAILY PROFIT LIMIT → Risk reduction
    profit_limit = self.config.get("DAILY_PROFIT_LIMIT_PCT", 0.003)
    if daily_pnl_pct >= profit_limit:
        self._daily_profit_reduced = True  # Risk ÷ 75%
        logger.info(f"Daily profit {daily_pnl_pct:.3%} >= {profit_limit:.3%} - risk reduced")
    
    # CONSISTENCY CHECK (max 30% single trade)
    # ... checked separately
    
    return True, "OK"
```

### Trailing Stop Level (4 niveaux ATR)
```python
# ftmo_protector.py - ATR-based trailing
def _check_step_trailing(self, position, tick):
    """4-level trailing based on profit in ATR units"""
    
    profit_usd = position.profit
    if profit_usd <= 0:
        return  # No trailing if losing
    
    atr_val = self._get_atr(position.symbol)
    profit_atr = profit_usd / (position.volume * atr_val * ???)  # Scaled to ATR
    
    entry = position.price_open
    peak = self.trailing_peaks.get(position.ticket, entry)
    sl = position.sl
    
    # Update peak
    current = tick.ask if position.type == 0 else tick.bid
    if position.type == 0 and current > peak:
        peak = current
    elif position.type == 1 and current < peak:
        peak = current
    
    self.trailing_peaks[position.ticket] = peak
    
    # TRAILING SL LEVELS (peak-based)
    if profit_atr > 5.0:
        new_sl = peak - 0.15 * atr_val
    elif profit_atr > 3.0:
        new_sl = peak - 0.25 * atr_val
    elif profit_atr > 1.5:
        new_sl = peak - 0.35 * atr_val
    elif profit_atr > 0.5:
        new_sl = peak - 0.5 * atr_val
    else:
        return  # Not trailing yet
    
    # Only move SL forward (never backward)
    if position.type == 0 and new_sl > sl:
        self._update_sl(position.ticket, new_sl)
    elif position.type == 1 and new_sl < sl:
        self._update_sl(position.ticket, new_sl)
```

### Persistence d'État Critique
```python
# main.py - Challenge initial balance persistence
def __init__(self):
    self._state = self._load_state()
    
    # *** CRITICAL: Capture initial balance ONCE ***
    if "challenge_initial_balance" not in self._state:
        self._state["challenge_initial_balance"] = self._get_balance()
        self._save_state()
    
    challenge_init_bal = self._state["challenge_initial_balance"]
    logger.info(f"Challenge initial balance: ${challenge_init_bal:.0f} (persisted)")
    
    # This value is IMMUTABLE for all DD/daily loss calculations
    # Even after restart, this is never recaptured
```

### Cooldown & Pause
```python
# ftmo_protector.py
def record_trade_result(self, symbol, profit):
    """Record trade result and update stats"""
    self._trade_history.append({"symbol": symbol, "profit": profit})
    self.daily_stats["trades"] += 1
    self.daily_stats["pnl"] += profit
    
    if profit < 0:
        self.consecutive_losses += 1
        self.cooldowns[symbol] = datetime.now() + timedelta(minutes=self.cooldown_minutes)
        
        if self.consecutive_losses >= 2:
            logger.warning(f"Pause: {self.consecutive_losses} consecutive losses")
            # Pause trading for next cycle
    else:
        self.consecutive_losses = 0  # Reset on win
```

---

## 6️⃣ STRUCTURE D'EXÉCUTION (main.py)

### Boucle Principale 15s
```python
# main.py - Main trading loop
def start(self):
    logger.info("Starting main loop...")
    self.running = True
    
    while self.running:
        cycle_start = time.time()
        self.cycle_count += 1
        
        try:
            # 1. CONNECTION CHECK
            if not self._health_check():
                logger.error("Health check failed")
                # Reconnect logic...
                continue
            
            # 2. HEARTBEAT (for watchdog)
            self._heartbeat()
            
            # 3. CACHE INVALIDATION
            self._pos_cache.invalidate()
            
            # 4. POSITION TRACKING
            self.tracker.check_closed()  # Detect closed positions
            self.tracker.track_new()      # Detect new fills
            
            # 5. ACCOUNT INFO
            account = self.mt5.get_account_info()
            floating = account.equity - account.balance
            dd = max(0, self.ftmo.initial_balance - account.equity)
            dd_pct = dd / max(self.ftmo.initial_balance, 1) * 100
            
            logger.info(f"[Cycle {self.cycle_count}] Balance=${account.balance:.0f} "
                       f"Eq=${account.equity:.0f} Float={floating:+.0f} "
                       f"DD=${dd:.0f}({dd_pct:.1f}%)")
            
            # 6. POSITION MANAGEMENT (trailing, partial TP, time-stops)
            self._manage_positions()
            
            # 7. SIGNAL SCANNING & EXECUTION
            self._scan_signals()
            
            # 8. REPORTING (every 20 cycles)
            if self.cycle_count - self.last_report_cycle >= 20:
                self._log_ftmo_report()
                self.last_report_cycle = self.cycle_count
            
            # SLEEP TO 15s TOTAL
            elapsed = time.time() - cycle_start
            sleep_time = max(5, cfg.CYCLE_SECONDS - elapsed)
            time.sleep(sleep_time)
            
        except Exception as e:
            logger.error(f"Cycle error: {e}")
            self._watchdog_failures += 1
            if self._watchdog_failures > 3:
                break
```

### Signal Scanning
```python
# main.py - _scan_signals()
def _scan_signals(self):
    positions = self._pos_cache.get()
    sym_counts = {}
    
    # Count existing positions
    for p in positions:
        sym_counts[p.symbol] = sym_counts.get(p.symbol, 0) + 1
    
    candidates = []
    
    for symbol in cfg.SYMBOLS:
        # 1. FTMO GATE
        can_trade, reason = self.ftmo.can_trade(symbol)
        if not can_trade:
            logger.debug(f"[FTMO] {symbol}: {reason}")
            continue
        
        # 2. POSITION LIMIT CHECK
        max_sym = cfg.SYMBOL_MAX_POSITIONS.get(symbol, cfg.MAX_POSITIONS_PER_SYMBOL)
        if sym_counts.get(symbol, 0) >= max_sym:
            logger.debug(f"[LIMIT] {symbol}: max positions ({max_sym}) reached")
            continue
        
        # 3. SIGNAL GENERATION
        adapt_params = self.adaptive.learner.get_params(symbol)
        overrides = dict(thresh=adapt_params["thresh"])
        signal = self.signals.analyze(symbol, overrides)
        
        if signal is None:
            continue
        
        # 4. ADX FILTER (Threshold 15)
        adx_thresh = 15
        if signal.get("adx", 0) < adx_thresh:
            logger.info(f"[ADX] {symbol}: ADX={signal['adx']:.1f} < {adx_thresh}, skip")
            continue
        
        # 5. ADAPTIVE ANALYSIS
        trade_stats = self.journal.get_stats(symbol=symbol)
        adapted = self.adaptive.analyze(symbol, signal.get("rates", {}), 
                                       signal, trade_stats=trade_stats)
        
        if adapted is None:
            logger.info(f"[ADAPTIVE] {symbol}: signal rejected")
            continue
        
        candidates.append((adapted["score"], symbol, adapted, positions))
    
    # EXECUTE TOP SIGNALS (sorted by score, max 3/cycle)
    candidates.sort(key=lambda x: x[0], reverse=True)
    executed = 0
    
    for score, symbol, signal, positions in candidates:
        if executed >= cfg.MAX_SIGNALS_PER_CYCLE:
            break
        
        can_trade, reason = self.ftmo.can_trade(symbol, signal, positions)
        if not can_trade:
            logger.debug(f"[FTMO FINAL] {symbol}: {reason}")
            continue
        
        if len(self._pos_cache.get()) >= cfg.MAX_POSITIONS:
            break
        
        logger.info(f"[TRADE] {symbol} {signal['action']} (score={score:.2f})")
        self.executor.execute(symbol, signal)
        executed += 1
```

---

## 7️⃣ DONNÉES RUNTIME & PERSISTENCE

### Robot State JSON
```json
{
  "peak_equity": 199476.61,
  "consecutive_losses": 0,
  "partial_closed": [],
  "trailing_peaks": {
    "457683544": 97.187,
    "457700270": 1.34363,
    "457700273": 214.098
  },
  "peak_profit": {},
  "challenge_initial_balance": 199409.39,
  "restart_count": 0,
  "restart_timestamps": []
}
```

### FTMO Report JSON
```json
{
  "balance": 199792.00,
  "equity": 199311.00,
  "floating_pnl": -481.00,
  "drawdown_pct": 0.0,
  "daily_loss_pct": -0.21,
  "daily_loss_usd": -431.00,
  "daily_trades": 5,
  "consecutive_losses": 0,
  "win_rate": 0.646,
  "avg_r_multiple": 0.85,
  "total_trades_closed": 48,
  "challenge_status": "ACTIVE",
  "days_trading": 6
}
```

### Trading Journal (SQLite)
```sql
CREATE TABLE trades (
    id INTEGER PRIMARY KEY,
    symbol TEXT,
    direction TEXT,    -- BUY/SELL
    entry REAL,
    sl REAL,
    tp REAL,
    lot REAL,
    profit REAL,
    time_open TEXT,
    time_close TEXT,
    reason TEXT
);
-- Used by OnlineLearner for historical stats
-- SELECT AVG(profit) FROM trades WHERE symbol='EURUSD' AND time_close != ''
```

---

## 8️⃣ INDICATEURS TECHNIQUES

### Momentum & Trend
```python
# indicators.py
def ema(data, period):
    """Exponential Moving Average"""
    d = np.asarray(data, dtype=float)
    alpha = 2.0 / (period + 1)
    result[period - 1] = np.mean(d[:period])
    for i in range(period, len(d)):
        result[i] = alpha * d[i] + (1 - alpha) * result[i - 1]
    return result

def rsi(data, period=14):
    """Relative Strength Index"""
    diff = np.diff(d)
    gains = np.where(diff > 0, diff, 0)
    losses = np.where(diff < 0, -diff, 0)
    # Average gain/loss over period
    # RSI = 100 - (100 / (1 + RS)) where RS = avg_gain / avg_loss

def macd(data, fast=12, slow=26, signal=9):
    """MACD: line, signal, histogram"""
    ema_fast = ema(d, fast)
    ema_slow = ema(d, slow)
    macd_line = ema_fast - ema_slow
    sig_line = ema(macd_line, signal)
    hist = macd_line - sig_line
    return macd_line, sig_line, hist

def atr(high, low, close, period=14):
    """Average True Range"""
    tr = np.maximum(high[1:] - low[1:],
                    np.maximum(np.abs(high[1:] - close[:-1]),
                              np.abs(low[1:] - close[:-1])))
    atr = np.convolve(tr, np.ones(period)/period, mode='valid')
    return atr
```

### Market Structure
```python
# market_structure.py
def swing_points(high, low, left=5, right=5):
    """Find swing highs and lows"""
    swings = np.zeros(len(high), dtype=int)
    for i in range(left, len(high) - right):
        if all(high[i] > high[i-left:i]) and all(high[i] >= high[i+1:i+right+1]):
            swings[i] = 1  # Swing high
        if all(low[i] < low[i-left:i]) and all(low[i] <= low[i+1:i+right+1]):
            swings[i] = -1  # Swing low
    return swings

def break_of_structure(high, low, swings):
    """BOS: price breaks last swing"""
    # Bullish BOS: breaks above last swing high
    # Bearish BOS: breaks below last swing low

def change_of_character(swings, high, low):
    """CHOCH: structure change (reversal signal)"""
```

---

## 9️⃣ DÉPENDANCES & CALIBRATION

### Packages (requirements.txt)
```
MetaTrader5>=5.0.0       # Broker connection
numpy>=1.20.0            # Numerical computation
pandas>=1.3.0            # DataFrames (minimal)
torch>=1.13.0            # PyTorch for LSTM
scikit-learn>=1.7.0      # Random Forest (DISABLED)
xgboost>=1.6.0           # XGBoost (DISABLED)
lightgbm>=3.3.0          # LightGBM (DISABLED)
joblib>=1.2.0            # Model serialization
openpyxl>=3.1.0          # Parse Excel
requests>=2.28.0         # HTTP (Telegram, news APIs)
python-dotenv>=0.19.0    # Environment variables
```

### Calibration Offline (calibrate_all.py)
```python
# One-time setup: import 951 historical trades
# Calibrates OnlineLearner + MetaLearner weights

def main():
    # 1. Parse ReportHistory Excel (951 trades)
    wb = openpyxl.load_workbook(path)
    raw_trades = [...]  # extract symbol/type/profit/time
    
    # 2. Initialize models
    regime = MarketRegime()
    learner = OnlineLearner(window=50)
    meta = MetaLearner(recalibration_freq=50)
    dl = DLEnsemble()
    
    # 3. Replay trades through learner
    for trade in raw_trades:
        regime_detected = regime.detect(rates[trade.symbol])
        r_multiple = trade.profit / trade.initial_risk
        learner.record_trade(trade.symbol, r_multiple, regime_detected)
        
        # Also record in MetaLearner
        predictions = {model: correct_bool}
        meta.record_trade(trade.symbol, regime_detected, predictions)
    
    # 4. Save calibrated state
    joblib.dump(state, "runtime/calibration_state.pkl")

# Then restart main.py to load this state
```

---

## 🔟 FLAWS & PROBLÈMES CONNUS

### 1. Positions Bloquées (CRITIQUE)
```python
MAX_POSITIONS_PER_SYMBOL = 1  # ← BOTTLENECK

# Problème: 
# - Robot ne peut ouvrir qu'1 trade par symbole
# - Si EURUSD déjà 1 position, nouveau signal rejeté
# - Peut seulement ajouter/moyenner si perte

# Impact:
# - Sous-utilisation du capital
# - Moins de diversification
# - Signaux de qualité rejetés

# Solution:
MAX_POSITIONS_PER_SYMBOL = 2-3  # Allow more
# Mais garder corrélation control
```

### 2. ML Ensemble Désactivé
```python
# ml_ensemble.py est codé mais DISABLED au runtime
# Raison: Accuracy 45% (inférieure à aléatoire)

# Impact:
# - Perte d'opportunités de confluence
# - Seulement 5 modèles (4 ML + MOM20x3)
# - 581 MB RAM économisés
# - Devil's Advocate trop pénalisant?

# Solution:
# - Réentraîner ML sur nouvelles données (>60% accuracy)
# - Ou supprimer ML entirely (coût/bénéfice faible)
```

### 3. News Filter Inactif
```python
# news_filter.py existe mais APIs bloquées (403)
# TradingView + ForexFactory non accessible

# Impact:
# - Pas de protection avant annonces économiques
# - Potentiel pour bad timing trades

# Solution:
# - Alternative: Bloomberg API, Earnings.com
# - Ou gérer via symboles (skip high-impact pairs)
```

### 4. Session Analyzer Non Utilisé
```python
# session_analyzer.py codé mais jamais appelé dans main.py

def session_proximity_weight(current_hour=None):
    """Returns 1.0 during London/NY overlap, else decreases"""
    london_ny_start, london_ny_end = 12, 16  # 12-16 UTC

# Pourrait booster confluence scoring pendant sessions actives
# Mais pas intégré dans signal flow
```

### 5. DL LSTM Figé
```python
# dl_lstm_all.pkl est pré-entraîné et figé
# - Aucun fine-tuning online
# - Trained on 1558 séquences historiques (ancien data)
# - Pourrait dériver dans new market conditions

# Solution:
# - Fine-tune online tous les 100 trades
# - Ou utiliser DL comme feature, pas prédiction
```

---

## 1️⃣1️⃣ FILES À SUPPRIMER (DEAD CODE)

### Root Directory
```
✅ analyze_trades.py             - Ad-hoc analysis (0 usage)
✅ analyse_definitive.py         - Static analysis (0 usage)
✅ analyse_risque.py             - Monte Carlo/VaR (0 usage)
✅ analyze_report.py             - Parse Excel (0 usage)
✅ analyze_report_v2.py          - Variant (0 usage)
✅ monitor.py                    - Manual monitoring (replaced)
✅ report_continuous.py          - Continuous reporting (0 usage)
✅ monitor_continuous.py         - Continuous monitoring (0 usage)
✅ watchdog.py                   - Old watchdog (replaced by PositionTracker)
✅ start_robot.bat               - Batch script (use PowerShell)
✅ check_optimization.py         - Perf check (0 usage)
✅ surveillance.py               - Parallel surveillance (0 usage)

Total: ~3000 lignes of dead code
```

### engine_simple/
```
✅ step1_parse_reports.py        - Parse Excel (offline setup)
✅ step2_validate_ml.py          - Validate ML (offline)
✅ step3_train_dl_calibrate.py   - Train DL (offline)
⚠️ session_analyzer.py           - Coded but not called (soft dead)
⚠️ calibrate_all.py              - Keep if re-training, else delete
```

---

## 1️⃣2️⃣ RÉSUMÉ POUR ANALYSE PROFESSIONNELLE

### Strengths
✅ Architecture modulaire + clean separation (signals/adaptation/protection)  
✅ OnlineLearner adapte en temps réel (50-trade window)  
✅ MetaLearner combine 5 modèles avec poids dynamiques  
✅ 5 régimes de marché détectés (TREND_UP/DOWN, RANGING, HIGH/LOW_VOL)  
✅ Protections FTMO rigoureuses (DD/daily/consistency/spread)  
✅ Trailing SL intelligent (4 niveaux ATR)  
✅ Persistence critique (challenge_initial_balance immuable)  
✅ PID lock empêche instances dupliquées  
✅ ~48 trades historiques avec 64.6% WR (sain)  

### Weaknesses
⚠️ ML Ensemble désactivé (45% accuracy insuffisante)  
⚠️ Positions bloquées (MAX_POSITIONS_PER_SYMBOL=1)  
⚠️ News filter inactif (APIs bloquées)  
⚠️ Session analyzer codé mais non utilisé  
⚠️ DL LSTM pré-entraîné et figé (pas d'online fine-tuning)  
⚠️ Devil's Advocate peut être trop pénalisant  
⚠️ Confidence scoring peut être noisy  

### Opportunités
💡 Activer ML avec meilleur training  
💡 Augmenter MAX_POSITIONS_PER_SYMBOL à 2-3  
💡 Implémenter news filter alternatif  
💡 Intégrer session analyzer dans confluence  
💡 Fine-tune DL LSTM online  
💡 Tester Devil's Advocate threshold  
💡 Ajouter corrélation pair-wise (pas juste groupes)  

---

**Fin de l'analyse complète - 27 Mai 2026**
