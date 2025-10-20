from typing import List, Optional

class AdaptivePositionSizing:
    """Position sizing adaptatif selon volatilité et corrélation"""
    
    def __init__(self, base_risk_pct=0.02, max_risk_pct=0.05):
        self.base_risk_pct = base_risk_pct
        self.max_risk_pct = max_risk_pct
        self.volatility_window = 20
        self.correlation_threshold = 0.7
    
    def calculate_optimal_size(self, symbol, current_price, historical_data,
                               existing_positions: Optional[List[str]] = None):
        """Calculer taille position optimale"""
        try:
            # 1. Volatilité récente
            returns = historical_data['close'].pct_change().dropna()
            volatility = returns.rolling(self.volatility_window).std().iloc[-1]
            volatility_norm = min(volatility / 0.02, 2.0)  # Normaliser
            
            # 2. Ajustement selon volatilité
            vol_adjusted_risk = self.base_risk_pct / volatility_norm
            
            # 3. Corrélation avec positions existantes
            correlation_penalty = 1.0
            if existing_positions:
                correlation_penalty = self.calculate_correlation_penalty(
                    symbol, existing_positions, historical_data
                )
            
            # 4. Risk final
            final_risk_pct = min(
                vol_adjusted_risk * correlation_penalty,
                self.max_risk_pct
            )
            
            return {
                "risk_pct": final_risk_pct,
                "volatility": volatility,
                "correlation_penalty": correlation_penalty,
                "recommended_size": final_risk_pct
            }
            
        except Exception as e:
            return {
                "risk_pct": self.base_risk_pct,
                "error": str(e),
                "recommended_size": self.base_risk_pct
            }
    
    def calculate_correlation_penalty(self, symbol, positions, data):
        """Calculer pénalité corrélation"""
        # Logique simplifiée - à améliorer selon besoins
        if len(positions) == 0:
            return 1.0
        
        # Si plus de 2 positions, réduire
        if len(positions) >= 2:
            return 0.7
        
        # Corrélation FOREX vs CRYPTO vs METAL
        symbol_types = {
            "EURUSD": "forex",
            "XAUUSD": "metal",
            "BTCUSD": "crypto",
        }
        
        current_type = symbol_types.get(symbol, "unknown")
        existing_types = [symbol_types.get(pos, "unknown") for pos in positions]
        
        # Même type = corrélation élevée
        if current_type in existing_types:
            return 0.5
        
        return 1.0
