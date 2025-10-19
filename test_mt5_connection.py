#!/usr/bin/env python3
"""Test de connexion MT5 pour vérification production"""
import MetaTrader5 as mt5

print(f"MetaTrader5 disponible: {mt5 is not None}")

result = mt5.initialize()
print(f"Initialisation: {result}")

if result:
    info = mt5.account_info()
    if info:
        print(f"Compte connecté: {info.login}")
        print(f"Serveur: {info.server}")
        print(f"Balance: {info.balance}")
        print(f"Equity: {info.equity}")
    else:
        print("Aucune info compte disponible")
    mt5.shutdown()
else:
    error = mt5.last_error()
    print(f"Erreur initialisation: {error}")
