#!/usr/bin/env python3
"""
Corrections MT5 et optimisations système - Étape 3 de l'audit
"""

import os
import shutil
from pathlib import Path


def enhance_mt5_connector():
    """Améliorer le connecteur MT5 avec gestion d'erreurs robuste"""
    mt5_connector_path = "src/utils/mt5_connector.py"
    
    # Amélioration 1: Ajouter timeouts et retry logic
    enhanced_content = '''
def connect_with_retry(login, password, server, max_retries=3, timeout=30):
    """Connexion MT5 avec retry automatique et timeout"""
    for attempt in range(max_retries):
        try:
            if not mt5.initialize(
                login=login, password=password, server=server, timeout=timeout
            ):
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)  # Exponential backoff
                    continue
                return False
            return True
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise e
    return False

def mt5_health_check():
    """Vérification santé complète MT5"""
    if not MT5_AVAILABLE:
        return {"status": "unavailable", "mock": True}
    
    try:
        # Test connection
        terminal_info = mt5.terminal_info()
        account_info = mt5.account_info()
        
        if terminal_info is None or account_info is None:
            return {"status": "disconnected", "error": "No terminal/account info"}
        
        # Test symbol availability
        symbols = mt5.symbols_get()
        if not symbols:
            return {"status": "limited", "error": "No symbols available"}
        
        return {
            "status": "healthy",
            "terminal": terminal_info.name if terminal_info else "Unknown",
            "account": account_info.login if account_info else "Unknown",
            "symbols_count": len(symbols) if symbols else 0
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}
'''
    
    # Lire le fichier existant
    with open(mt5_connector_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Ajouter les améliorations
    content += enhanced_content
    
    # Réécrire
    with open(mt5_connector_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print("✅ MT5 Connector amélioré avec retry logic et health checks")


def fix_xauusd_signal_generation():
    """Corriger la génération de signaux pour XAUUSD"""
    live_engine_path = "scripts/live_trading_engine.py"
    
    # Amélioration spécifique pour XAUUSD
    xauusd_fix = '''
def generate_enhanced_xauusd_signal(self, data):
    """Signal XAUUSD amélioré avec analyse technique spécialisée"""
    try:
        if data is None or len(data) < 20:
            return self.generate_fallback_technical_signal("XAUUSD")
        
        # XAUUSD réagit fortement aux:
        # 1. Support/Resistance psychologiques (1900, 2000, 2100, etc.)
        # 2. Divergences RSI
        # 3. Pattern engulfing sur H1
        
        current_price = data['close'].iloc[-1]
        
        # Niveaux psychologiques XAUUSD
        psychological_levels = [1900, 2000, 2100, 2200, 2300, 2400, 2500]
        nearest_level = min(psychological_levels, 
                           key=lambda x: abs(x - current_price))
        
        distance_to_level = abs(current_price - nearest_level)
        level_proximity = 1 - min(distance_to_level / 50, 1.0)  # Max 50$ distance
        
        # RSI divergence pour XAUUSD
        rsi = self.calculate_rsi(data['close'], 14)
        price_trend = (data['close'].iloc[-1] - data['close'].iloc[-5]) / data['close'].iloc[-5]
        rsi_trend = (rsi.iloc[-1] - rsi.iloc[-5]) / rsi.iloc[-5]
        
        # Divergence detection
        divergence_strength = abs(price_trend - rsi_trend)
        bullish_divergence = (price_trend < 0 and rsi_trend > 0 and rsi.iloc[-1] < 30)
        bearish_divergence = (price_trend > 0 and rsi_trend < 0 and rsi.iloc[-1] > 70)
        
        # Signal calculation
        base_confidence = 0.3  # Base minimum pour XAUUSD
        
        if bullish_divergence:
            confidence = base_confidence + (divergence_strength * 0.4) + (level_proximity * 0.3)
            return {"action": "buy", "confidence": min(confidence, 0.95)}
        elif bearish_divergence:
            confidence = base_confidence + (divergence_strength * 0.4) + (level_proximity * 0.3)
            return {"action": "sell", "confidence": min(confidence, 0.95)}
        else:
            # Trend following with psychological levels
            if current_price > nearest_level and level_proximity > 0.8:
                confidence = base_confidence + (level_proximity * 0.2)
                return {"action": "buy", "confidence": min(confidence, 0.7)}
            elif current_price < nearest_level and level_proximity > 0.8:
                confidence = base_confidence + (level_proximity * 0.2)
                return {"action": "sell", "confidence": min(confidence, 0.7)}
        
        return {"action": "hold", "confidence": base_confidence}
        
    except Exception as e:
        self.logger.warning(f"Erreur signal XAUUSD spécialisé: {e}")
        return self.generate_fallback_technical_signal("XAUUSD")

def calculate_rsi(self, prices, period=14):
    """Calcul RSI robuste"""
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))
'''
    
    print("✅ Signal XAUUSD spécialisé créé")
    return xauusd_fix


def optimize_logging_system():
    """Optimiser le système de logging"""
    
    # Configuration logging optimisée
    logging_config = '''
import logging
import logging.handlers
import gzip
import shutil
from pathlib import Path

def setup_optimized_logging(logger_name, log_level=logging.INFO):
    """Configuration logging optimisée avec compression et rotation"""
    
    logger = logging.getLogger(logger_name)
    logger.setLevel(log_level)
    
    # Éviter duplication
    if logger.handlers:
        logger.handlers.clear()
    
    # Formatter optimisé
    formatter = logging.Formatter(
        '%(asctime)s|%(levelname)s|%(funcName)s|%(message)s',
        datefmt='%H:%M:%S'
    )
    
    # Handler fichier avec compression
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    
    def rotator(source, dest):
        """Compresser lors de la rotation"""
        with open(source, "rb") as f_in:
            with gzip.open(dest + ".gz", "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
        os.remove(source)
    
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / f"{logger_name}.log",
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5
    )
    file_handler.rotator = rotator
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)
    
    # Handler console minimal
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)  # Moins verbeux
    console_formatter = logging.Formatter('%(levelname)s: %(message)s')
    console_handler.setFormatter(console_formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger
'''
    
    # Créer le fichier logging config
    with open("src/utils/optimized_logging.py", "w", encoding="utf-8") as f:
        f.write(logging_config)
    
    print("✅ Système de logging optimisé créé")


def create_position_sizing_system():
    """Créer système de position sizing adaptatif"""
    
    position_sizing_code = '''
import numpy as np
import pandas as pd

class AdaptivePositionSizing:
    """Position sizing adaptatif selon volatilité et corrélation"""
    
    def __init__(self, base_risk_pct=0.02, max_risk_pct=0.05):
        self.base_risk_pct = base_risk_pct
        self.max_risk_pct = max_risk_pct
        self.volatility_window = 20
        self.correlation_threshold = 0.7
    
    def calculate_optimal_size(self, symbol, current_price, historical_data, 
                              existing_positions=None):
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
            "BTCUSD": "crypto"
        }
        
        current_type = symbol_types.get(symbol, "unknown")
        existing_types = [symbol_types.get(pos, "unknown") for pos in positions]
        
        # Même type = corrélation élevée
        if current_type in existing_types:
            return 0.5
        
        return 1.0
'''
    
    # Créer le fichier
    Path("src/utils").mkdir(exist_ok=True)
    with open("src/utils/adaptive_position_sizing.py", "w", encoding="utf-8") as f:
        f.write(position_sizing_code)
    
    print("✅ Système position sizing adaptatif créé")


def main():
    """Exécuter toutes les améliorations MT5 et système"""
    print("🔧 AMÉLIORATION MT5 ET OPTIMISATIONS SYSTÈME")
    print("=" * 50)
    
    enhance_mt5_connector()
    fix_content = fix_xauusd_signal_generation()
    optimize_logging_system()
    create_position_sizing_system()
    
    print("\n✅ TOUTES LES AMÉLIORATIONS APPLIQUÉES")
    print("- MT5 connector avec retry et timeouts")
    print("- Signal XAUUSD spécialisé") 
    print("- Logging optimisé avec compression")
    print("- Position sizing adaptatif")
    
    # Retourner le contenu XAUUSD pour intégration manuelle
    return fix_content


if __name__ == "__main__":
    xauusd_fix = main()
    print(f"\n📝 Code XAUUSD à intégrer dans live_trading_engine.py:")
    print(xauusd_fix[:500] + "...")