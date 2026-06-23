"""Adaptive Parameters — Ajustement dynamique des paramètres.

Apprend et ajuste automatiquement les paramètres de trading en fonction
des performances récentes. Basé sur un window glissant de N trades.

Usage:
    ap = AdaptiveParameters("BTCUSD")
    params = ap.get_adapted_params()
    ap.record_trade(pnl=150, win=True)
"""
import logging
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("adaptive_params")


@dataclass
class AdaptedParams:
    """Paramètres adaptés pour un symbole."""
    # Multipliers (1.0 = neutre)
    threshold_mult: float = 1.0
    risk_mult: float = 1.0
    sl_mult: float = 1.0
    tp_mult: float = 1.0
    trailing_mult: float = 1.0
    
    # Metadata
    win_rate: float = 0.5
    profit_factor: float = 1.0
    avg_pnl: float = 0.0
    sample_size: int = 0
    last_update: float = 0.0
    confidence: float = 0.0  # 0-1 based on sample size
    
    def to_dict(self) -> dict:
        return {
            "threshold_mult": self.threshold_mult,
            "risk_mult": self.risk_mult,
            "sl_mult": self.sl_mult,
            "tp_mult": self.tp_mult,
            "trailing_mult": self.trailing_mult,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "avg_pnl": self.avg_pnl,
            "sample_size": self.sample_size,
            "last_update": self.last_update,
            "confidence": self.confidence,
        }


class AdaptiveParameters:
    """Ajuste dynamiquement les paramètres par symbole."""
    
    def __init__(self, symbol: str, lookback: int = 100,
                 min_trades: int = 20, state_dir: str = "runtime"):
        self.symbol = symbol
        self.lookback = lookback
        self.min_trades = min_trades
        self._state_dir = Path(state_dir)
        self._state_file = self._state_dir / f"adaptive_{symbol}.json"
        
        # Trade history
        self._trades: list[dict] = []
        self._params = AdaptedParams()
        
        self._load_state()
    
    def _load_state(self):
        """Charge l'état depuis le disque."""
        try:
            if self._state_file.exists():
                with open(self._state_file, "r") as f:
                    data = json.load(f)
                
                self._params = AdaptedParams(**data.get("params", {}))
                
                trades = data.get("trades", [])
                self._trades = trades[-self.lookback:]  # Keep last N
                
                logger.debug(f"  [ADAPTIVE] {self.symbol}: loaded {len(self._trades)} trades, "
                            f"WR={self._params.win_rate:.1%}")
        except Exception as e:
            logger.warning(f"  [ADAPTIVE] {self.symbol}: load failed — {e}")
    
    def _save_state(self):
        """Sauvegarde l'état sur le disque."""
        try:
            self._state_dir.mkdir(parents=True, exist_ok=True)
            
            data = {
                "params": self._params.to_dict(),
                "trades": self._trades[-self.lookback:],
                "last_update": time.time(),
            }
            
            with open(self._state_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning(f"  [ADAPTIVE] {self.symbol}: save failed — {e}")
    
    def record_trade(self, pnl: float, win: bool, **kwargs):
        """Enregistre un trade et recalcule les paramètres."""
        trade = {
            "pnl": pnl,
            "win": win,
            "time": time.time(),
            **kwargs,
        }
        
        self._trades.append(trade)
        
        # Keep last N
        if len(self._trades) > self.lookback:
            self._trades = self._trades[-self.lookback:]
        
        # Recalculate
        self._recalculate()
        self._save_state()
    
    def _recalculate(self):
        """Recalcule les paramètres adaptés."""
        trades = self._trades
        
        if len(trades) < self.min_trades:
            self._params.confidence = len(trades) / self.min_trades * 0.5
            return
        
        # Win rate
        wins = sum(1 for t in trades if t.get("win", False))
        wr = wins / len(trades)
        
        # Profit factor
        gross_profit = sum(t["pnl"] for t in trades if t["pnl"] > 0)
        gross_loss = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))
        pf = gross_profit / gross_loss if gross_loss > 0 else 2.0
        
        # Average PnL
        avg_pnl = sum(t["pnl"] for t in trades) / len(trades)
        
        # Confidence based on sample size
        confidence = min(len(trades) / 100, 1.0)
        
        # ── ADAPTIVE LOGIC ──
        threshold_mult = 1.0
        risk_mult = 1.0
        sl_mult = 1.0
        tp_mult = 1.0
        trailing_mult = 1.0
        
        if wr > 0.60:
            # Good performance → more aggressive
            threshold_mult = 0.95
            risk_mult = 1.1
            tp_mult = 1.1
        elif wr > 0.55:
            # Decent → slightly aggressive
            threshold_mult = 0.98
            risk_mult = 1.05
        elif wr < 0.45:
            # Bad → conservative
            threshold_mult = 1.05
            risk_mult = 0.8
            sl_mult = 0.9
        elif wr < 0.50:
            # Below average → more conservative
            threshold_mult = 1.02
            risk_mult = 0.9
        
        # PF adjustment
        if pf > 2.0:
            tp_mult = 1.2
        elif pf < 1.0:
            risk_mult *= 0.7
            tp_mult = 0.9
        
        # Streak adjustment (last 5 trades)
        last_5 = trades[-5:]
        last_5_wr = sum(1 for t in last_5 if t.get("win", False)) / len(last_5)
        
        if last_5_wr < 0.2:
            # Losing streak → reduce risk
            risk_mult *= 0.7
        elif last_5_wr > 0.8:
            # Winning streak → slight boost
            risk_mult *= 1.1
        
        self._params = AdaptedParams(
            threshold_mult=threshold_mult,
            risk_mult=risk_mult,
            sl_mult=sl_mult,
            tp_mult=tp_mult,
            trailing_mult=trailing_mult,
            win_rate=wr,
            profit_factor=pf,
            avg_pnl=avg_pnl,
            sample_size=len(trades),
            last_update=time.time(),
            confidence=confidence,
        )
        
        logger.debug(f"  [ADAPTIVE] {self.symbol}: WR={wr:.1%}, PF={pf:.2f}, "
                     f"risk_mult={risk_mult:.2f}, thresh_mult={threshold_mult:.2f}")
    
    def get_adapted_params(self) -> AdaptedParams:
        """Retourne les paramètres adaptés."""
        return self._params
    
    def get_status(self) -> dict:
        """Retourne le statut de l'adaptation."""
        return {
            "symbol": self.symbol,
            "sample_size": self._params.sample_size,
            "confidence": self._params.confidence,
            "win_rate": self._params.win_rate,
            "profit_factor": self._params.profit_factor,
            "risk_mult": self._params.risk_mult,
            "threshold_mult": self._params.threshold_mult,
        }


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================
_adaptive_instances: dict[str, AdaptiveParameters] = {}

def get_adaptive(symbol: str, lookback: int = 100,
                 min_trades: int = 20) -> AdaptiveParameters:
    """Retourne (ou crée) l'instance adaptive pour un symbole."""
    if symbol not in _adaptive_instances:
        _adaptive_instances[symbol] = AdaptiveParameters(
            symbol, lookback, min_trades
        )
    return _adaptive_instances[symbol]

def record_trade(symbol: str, pnl: float, win: bool, **kwargs):
    """Enregistre un trade (fonction convenience)."""
    get_adaptive(symbol).record_trade(pnl, win, **kwargs)

def get_adapted_params(symbol: str) -> AdaptedParams:
    """Retourne les paramètres adaptés (fonction convenience)."""
    return get_adaptive(symbol).get_adapted_params()
