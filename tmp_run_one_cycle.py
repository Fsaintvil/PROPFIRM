#!/usr/bin/env python3
"""Script de diagnostic: lance un seul cycle du moteur en foreground
pour capturer les logs et afficher les signaux calculés.
"""
from scripts.live_trading_engine import LiveTradingEngine
import logging

def main():
    # Forcer un seul symbole pour diagnostic
    engine = LiveTradingEngine(symbols=['EURUSD'])

    # Forcer level DEBUG pour capter les diagnostics ajoutés
    engine.logger.setLevel(logging.DEBUG)

    # Initialiser les systèmes AI (lazy)
    engine.initialize_ai_systems()

    # Récupérer les données live (ou fallback)
    data = engine.get_live_data('EURUSD', count=200)
    if data is None:
        print('Aucune donnée EURUSD reçue depuis MT5, utilisation de simulation/fallback')
        data = engine.generate_simulation_data(200)
        if data is None:
            print('Échec génération simulation - abort')
            return

    # Obtenir les signaux (inclut apply_advanced_decision_engine)
    signals = engine.get_ai_signals(data, symbol='EURUSD')

    print('--- RESULT SIGNALS ---')
    for k, v in signals.items():
        try:
            print(f"{k}: {v}")
        except Exception:
            print(f"{k}: <unprintable>")

if __name__ == '__main__':
    main()
