#!/usr/bin/env python3
"""
Script d'arrêt d'urgence immédiat du robot de trading
Ferme toutes les positions et suspend le trading pour 5 minutes
"""

import sys
import os
from pathlib import Path
from datetime import datetime, timedelta

# Ajouter le chemin du projet
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from scripts.live_trading_engine import LiveTradingEngine
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False
    print("⚠️ MT5 non disponible - Mode simulation")

def emergency_stop_now():
    """Active immédiatement l'arrêt d'urgence"""
    
    print("🚨 ACTIVATION ARRÊT D'URGENCE IMMÉDIAT")
    print("=" * 50)
    
    # 1. Créer le fichier d'arrêt d'urgence
    control_dir = Path("control")
    control_dir.mkdir(exist_ok=True)
    
    emergency_file = control_dir / "emergency_stop"
    emergency_until = datetime.now() + timedelta(minutes=5)
    
    with open(emergency_file, 'w') as f:
        f.write("EMERGENCY_STOP_ACTIVE\n")
        f.write(f"Timestamp: {datetime.now().isoformat()}\n")
        f.write("Duration: 5 minutes\n")
        f.write(f"Until: {emergency_until.isoformat()}\n")
        f.write("Reason: User requested immediate halt\n")
        f.write("Status: ACTIVE\n")
    
    print(f"✅ Fichier d'arrêt créé: {emergency_file}")
    
    # 2. Fermer toutes les positions MT5 si disponible
    if MT5_AVAILABLE:
        try:
            if mt5.initialize():
                positions = mt5.positions_get()
                if positions and len(positions) > 0:
                    print(f"📊 {len(positions)} positions ouvertes détectées")
                    
                    closed_count = 0
                    for position in positions:
                        try:
                            # Déterminer le type d'ordre de fermeture
                            if position.type == mt5.ORDER_TYPE_BUY:
                                order_type = mt5.ORDER_TYPE_SELL
                                price = mt5.symbol_info_tick(position.symbol).bid
                            else:
                                order_type = mt5.ORDER_TYPE_BUY
                                price = mt5.symbol_info_tick(position.symbol).ask
                            
                            # Créer la requête de fermeture
                            request = {
                                "action": mt5.TRADE_ACTION_DEAL,
                                "position": position.ticket,
                                "symbol": position.symbol,
                                "volume": position.volume,
                                "type": order_type,
                                "price": price,
                                "magic": 234000,
                                "comment": "Emergency Stop Close",
                                "type_time": mt5.ORDER_TIME_GTC,
                                "type_filling": mt5.ORDER_FILLING_IOC,
                            }
                            
                            # Envoyer l'ordre de fermeture
                            result = mt5.order_send(request)
                            
                            if result.retcode == mt5.TRADE_RETCODE_DONE:
                                closed_count += 1
                                print(f"✅ Position fermée: {position.symbol} ticket {position.ticket}")
                            else:
                                print(f"❌ Échec fermeture position {position.ticket}: {result.comment}")
                                
                        except Exception as e:
                            print(f"❌ Erreur fermeture position {position.ticket}: {e}")
                    
                    print(f"🔄 Fermeture d'urgence terminée: {closed_count}/{len(positions)} positions fermées")
                else:
                    print("ℹ️ Aucune position ouverte à fermer")
                    
                mt5.shutdown()
            else:
                print("❌ Impossible de se connecter à MT5")
                
        except Exception as e:
            print(f"❌ Erreur lors de la fermeture des positions: {e}")
    else:
        print("🔄 Mode simulation - Positions fermées virtuellement")
    
    # 3. Créer un log d'audit
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    
    audit_file = logs_dir / f"emergency_stop_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    with open(audit_file, 'w') as f:
        f.write(f"EMERGENCY STOP ACTIVATED\n")
        f.write(f"Timestamp: {datetime.now().isoformat()}\n")
        f.write(f"Duration: 5 minutes\n")
        f.write(f"Until: {emergency_until.isoformat()}\n")
        f.write(f"MT5 Available: {MT5_AVAILABLE}\n")
        f.write(f"Status: SUCCESS\n")
    
    print(f"📝 Log d'audit créé: {audit_file}")
    
    print("\n🚨 ARRÊT D'URGENCE ACTIVÉ AVEC SUCCÈS")
    print(f"⏰ Trading suspendu jusqu'à: {emergency_until.strftime('%H:%M:%S')}")
    print("🔄 Le robot reprendra automatiquement après cette période")
    
    return True

if __name__ == "__main__":
    try:
        emergency_stop_now()
    except Exception as e:
        print(f"❌ ERREUR CRITIQUE lors de l'arrêt d'urgence: {e}")
        sys.exit(1)