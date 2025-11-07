"""
🔧 CONFIGURATION UNIFIÉE PROPFIRM
Centralisation de toutes les configurations dans un seul endroit
Remplace settings.py, settings.json, risk.json, trading_decision.json
"""

import os
import json
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict, field

# Chargement optionnel des variables d'environnement
try:
    from dotenv import load_dotenv
    # 1) .env à la racine
    env_path = Path(".") / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
        print("✅ Variables d'environnement chargées depuis .env")
    # 2) Fichier additionnel des credentials MT5 si présent
    mt5_env = Path("config") / "mt5_credentials.env"
    if mt5_env.exists():
        load_dotenv(dotenv_path=mt5_env, override=True)
        print("✅ Credentials MT5 chargés depuis config/mt5_credentials.env")
except ImportError:
    print("ℹ️  python-dotenv non installé - variables OS uniquement")


@dataclass
class MT5Config:
    """Configuration MetaTrader 5"""
    login: Optional[str] = None
    password: Optional[str] = None
    server: Optional[str] = None
    terminal_path: str = "C:\\Program Files\\MetaTrader 5\\terminal64.exe"
    timeout: int = 30
    retry_count: int = 3


@dataclass
class TradingConfig:
    """Configuration trading principal"""
    symbols: List[str] = field(default_factory=list)
    lot_sizes: Dict[str, float] = field(default_factory=dict)
    max_risk_per_trade: float = 0.02
    max_trades_per_day: int = 20
    trading_interval: int = 930  # secondes
    confidence_threshold: float = 0.50
    live_trading: bool = False  # Mode simulation par défaut


@dataclass
class AIConfig:
    """Configuration systèmes IA"""
    enable_meta_learning: bool = True
    enable_reinforcement_learning: bool = True
    enable_regime_detection: bool = True
    enable_portfolio_optimizer: bool = True
    use_gpu: bool = False
    random_seed: int = 42


@dataclass
class StopLossConfig:
    """Configuration Stop Loss optimisés (nouveaux paramètres)"""
    eurusd_pips: float = 0.0005  # 5 pips (corrigé de 20 pips)
    xauusd_dollars: float = 2.0  # 2 dollars (corrigé de 5 dollars)
    btcusd_dollars: float = 150.0  # 150 dollars (corrigé de 500 dollars)
    volatility_multiplier_max: float = 0.5  # Amplification modérée
    risk_reward_ratio: float = 2.0  # 1:2 Risk/Reward


@dataclass
class LoggingConfig:
    """Configuration logging"""
    level: str = "INFO"
    log_dir: str = "logs"
    enable_compression: bool = True
    max_log_files: int = 10
    

@dataclass
class NotificationConfig:
    """Configuration notifications"""
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    slack_webhook_url: Optional[str] = None
    email_smtp_server: Optional[str] = None
    email_smtp_port: int = 587
    email_username: Optional[str] = None
    email_password: Optional[str] = None


class UnifiedConfig:
    """Configuration unifiée - Point d'entrée unique"""
    
    def __init__(self):
        # Charger depuis les variables d'environnement
        self.mt5 = self._load_mt5_config()
        self.trading = self._load_trading_config()
        self.ai = self._load_ai_config()
        self.stop_loss = self._load_stop_loss_config()
        self.logging = self._load_logging_config()
        self.notifications = self._load_notification_config()
        
        # Valider configuration
        self._validate_config()
        
    def _load_mt5_config(self) -> MT5Config:
        """Charger configuration MT5"""
        # Support both MT5_PASSWORD and MT5_PWD, and MT5_PATH or MT5_TERMINAL
        pwd = os.getenv("MT5_PASSWORD") or os.getenv("MT5_PWD")
        terminal = os.getenv("MT5_PATH") or os.getenv("MT5_TERMINAL") or MT5Config.terminal_path
        return MT5Config(
            login=os.getenv("MT5_LOGIN"),
            password=pwd,
            server=os.getenv("MT5_SERVER"),
            terminal_path=terminal,
            timeout=int(os.getenv("MT5_TIMEOUT", "30")),
            retry_count=int(os.getenv("MT5_RETRY_COUNT", "3"))
        )
    
    def _load_trading_config(self) -> TradingConfig:
        """Charger configuration trading"""
        symbols_str = os.getenv("TRADING_SYMBOLS", "EURUSD,XAUUSD,BTCUSD")
        symbols = [s.strip() for s in symbols_str.split(",")]
        
        # Lot sizes par défaut
        default_lot = float(os.getenv("DEFAULT_LOT_SIZE", "0.01"))
        lot_sizes = {symbol: default_lot for symbol in symbols}
        
        return TradingConfig(
            symbols=symbols,
            lot_sizes=lot_sizes,
            max_risk_per_trade=float(os.getenv("MAX_RISK_PER_TRADE", "2.0")) / 100,
            max_trades_per_day=int(os.getenv("MAX_TRADES_PER_DAY", "20")),
            trading_interval=int(os.getenv("TRADING_INTERVAL", "930")),
            confidence_threshold=float(os.getenv("CONFIDENCE_THRESHOLD", "0.50")),
            live_trading=os.getenv("LIVE_TRADING", "false").lower() == "true"
        )
    
    def _load_ai_config(self) -> AIConfig:
        """Charger configuration IA"""
        return AIConfig(
            enable_meta_learning=(
                os.getenv("ENABLE_META_LEARNING", "true").lower() == "true"
            ),
            enable_reinforcement_learning=(
                os.getenv("ENABLE_REINFORCEMENT_LEARNING", "true").lower() == "true"
            ),
            enable_regime_detection=(
                os.getenv("ENABLE_REGIME_DETECTION", "true").lower() == "true"
            ),
            enable_portfolio_optimizer=(
                os.getenv("ENABLE_PORTFOLIO_OPTIMIZER", "true").lower() == "true"
            ),
            use_gpu=os.getenv("USE_GPU", "false").lower() == "true",
            random_seed=int(os.getenv("RANDOM_SEED", "42"))
        )
    
    def _load_stop_loss_config(self) -> StopLossConfig:
        """Charger configuration Stop Loss (nouveaux paramètres optimisés)"""
        return StopLossConfig(
            eurusd_pips=float(os.getenv("EURUSD_SL_PIPS", "0.0005")),
            xauusd_dollars=float(os.getenv("XAUUSD_SL_DOLLARS", "2.0")),
            btcusd_dollars=float(os.getenv("BTCUSD_SL_DOLLARS", "150.0")),
            volatility_multiplier_max=float(os.getenv("VOLATILITY_MULTIPLIER_MAX", "0.5")),
            risk_reward_ratio=float(os.getenv("RISK_REWARD_RATIO", "2.0"))
        )
    
    def _load_logging_config(self) -> LoggingConfig:
        """Charger configuration logging"""
        return LoggingConfig(
            level=os.getenv("LOG_LEVEL", "INFO"),
            log_dir=os.getenv("LOG_DIR", "logs"),
            enable_compression=os.getenv("ENABLE_LOG_COMPRESSION", "true").lower() == "true",
            max_log_files=int(os.getenv("MAX_LOG_FILES", "10"))
        )
    
    def _load_notification_config(self) -> NotificationConfig:
        """Charger configuration notifications"""
        return NotificationConfig(
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID"),
            slack_webhook_url=os.getenv("SLACK_WEBHOOK_URL"),
            email_smtp_server=os.getenv("SMTP_SERVER"),
            email_smtp_port=int(os.getenv("SMTP_PORT", "587")),
            email_username=os.getenv("SMTP_USERNAME"),
            email_password=os.getenv("SMTP_PASSWORD")
        )
    
    def _validate_config(self):
        """Valider la configuration"""
        warnings = []
        
        # Validation MT5
        if not self.mt5.login:
            warnings.append("MT5_LOGIN non configuré")
        if not self.mt5.password:
            warnings.append("MT5_PASSWORD non configuré")
        if not self.mt5.server:
            warnings.append("MT5_SERVER non configuré")
            
        # Validation trading
        if self.trading.max_risk_per_trade > 0.05:  # 5%
            warnings.append(
                "Risque par trade très élevé: "
                f"{self.trading.max_risk_per_trade*100:.1f}%"
            )
            
        # Afficher warnings
        if warnings:
            print("⚠️  Configuration incomplète:")
            for warning in warnings:
                print(f"   • {warning}")
    
    def to_dict(self) -> Dict[str, Any]:
        """Exporter configuration en dictionnaire"""
        return {
            'mt5': asdict(self.mt5),
            'trading': asdict(self.trading),
            'ai': asdict(self.ai),
            'stop_loss': asdict(self.stop_loss),
            'logging': asdict(self.logging),
            'notifications': asdict(self.notifications)
        }
    
    def save_to_file(self, filepath: str):
        """Sauvegarder configuration dans un fichier JSON"""
        with open(filepath, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)
        print(f"✅ Configuration sauvegardée: {filepath}")


# Instance globale - Point d'accès unique
CONFIG = UnifiedConfig()

# Rétrocompatibilité avec l'ancien code
INSTRUMENTS = CONFIG.trading.symbols
MT5_LOGIN = CONFIG.mt5.login
MT5_PWD = CONFIG.mt5.password
MT5_SERVER = CONFIG.mt5.server
AUTO_EXECUTION = CONFIG.trading.live_trading
LOG_DIR = Path(CONFIG.logging.log_dir)
LOG_DIR.mkdir(parents=True, exist_ok=True)

if __name__ == "__main__":
    print("🔧 CONFIGURATION UNIFIÉE PROPFIRM")
    print("="*50)
    print(f"Symboles: {CONFIG.trading.symbols}")
    print(f"Risque par trade: {CONFIG.trading.max_risk_per_trade*100:.1f}%")
    print(f"SL EURUSD: {CONFIG.stop_loss.eurusd_pips*10000:.0f} pips")
    print(f"SL XAUUSD: ${CONFIG.stop_loss.xauusd_dollars}")
    print(f"SL BTCUSD: ${CONFIG.stop_loss.btcusd_dollars}")
    print(f"Mode live: {CONFIG.trading.live_trading}")
    
    # Sauvegarder exemple
    CONFIG.save_to_file("config/unified_config_example.json")
