#!/usr/bin/env python3
"""
Analyse des performances du robot de trading par instrument
"""
import json
from collections import defaultdict

def analyze_trading_performance():
    """Analyser les performances par instrument"""
    
    # Lire les données de trading
    with open('data/paper_trades.json', 'r') as f:
        content = f.read().strip()
        trades = [json.loads(line) for line in content.split('\n') if line.strip()]

    # Analyser par instrument
    instruments = ['EURUSD', 'XAUUSD', 'BTCUSD']
    analysis = {}

    for symbol in instruments:
        symbol_trades = [t for t in trades if t.get('symbol') == symbol and 'side' in t]
        
        # Compter les ordres
        buy_orders = len([t for t in symbol_trades if t.get('side') == 'buy'])
        sell_orders = len([t for t in symbol_trades if t.get('side') == 'sell'])
        
        # Ordres avec prix/SL/TP valides
        valid_orders = [t for t in symbol_trades if t.get('price') not in [None, 1.0015, 'null']]
        
        # MT5 responses (ordres réels)
        mt5_orders = [t for t in symbol_trades if 'mt5_response' in t]
        successful_mt5 = [t for t in mt5_orders if 'retcode=10009' in str(t.get('mt5_response', ''))]
        failed_mt5 = [t for t in mt5_orders if 'retcode=10016' in str(t.get('mt5_response', ''))]
        
        analysis[symbol] = {
            'total_orders': len(symbol_trades),
            'buy_orders': buy_orders,
            'sell_orders': sell_orders,
            'valid_orders': len(valid_orders),
            'mt5_attempts': len(mt5_orders),
            'mt5_successful': len(successful_mt5),
            'mt5_failed': len(failed_mt5),
            'latest_price': valid_orders[-1].get('price') if valid_orders else 'N/A'
        }

    # Affichage des résultats
    print('🎯 ANALYSE PERFORMANCE ROBOT PAR INSTRUMENT')
    print('=' * 60)
    
    for symbol, data in analysis.items():
        print(f'\n📊 {symbol}:')
        print(f'  Total ordres:       {data["total_orders"]}')
        print(f'  Ordres BUY:         {data["buy_orders"]}')
        print(f'  Ordres SELL:        {data["sell_orders"]}')
        print(f'  Ordres valides:     {data["valid_orders"]}')
        print(f'  Tentatives MT5:     {data["mt5_attempts"]}')
        print(f'  Succès MT5:         {data["mt5_successful"]}')
        print(f'  Échecs MT5:         {data["mt5_failed"]}')
        print(f'  Dernier prix:       {data["latest_price"]}')
        
        if data['mt5_attempts'] > 0:
            success_rate = (data['mt5_successful'] / data['mt5_attempts']) * 100
            print(f'  Taux succès MT5:    {success_rate:.1f}%')

    # Analyse globale
    total_orders = sum(data['total_orders'] for data in analysis.values())
    total_mt5_attempts = sum(data['mt5_attempts'] for data in analysis.values())
    total_mt5_success = sum(data['mt5_successful'] for data in analysis.values())
    
    print(f'\n🌍 PERFORMANCE GLOBALE:')
    print(f'  Total ordres générés:   {total_orders}')
    print(f'  Total tentatives MT5:   {total_mt5_attempts}')
    print(f'  Total succès MT5:       {total_mt5_success}')
    if total_mt5_attempts > 0:
        global_success_rate = (total_mt5_success / total_mt5_attempts) * 100
        print(f'  Taux succès global:     {global_success_rate:.1f}%')

if __name__ == "__main__":
    analyze_trading_performance()