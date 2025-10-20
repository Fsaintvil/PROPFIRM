#!/usr/bin/env python3
"""
Analyse financière détaillée du robot de trading
"""

def analyze_financial_performance():
    """Analyser les performances financières réelles"""
    
    # Données observées dans les logs MT5 réels
    capital_initial = 100004.25  # Balance initiale le 20-10-2025 07:22:02
    capital_actuel = 99985.66    # Balance actuelle le 20-10-2025 10:00:50
    equity_actuelle = 99972.12   # Equity actuelle le 20-10-2025 10:00:50
    
    # Calculs de performance
    variation_balance = capital_actuel - capital_initial
    variation_pct = (variation_balance / capital_initial) * 100
    drawdown = capital_actuel - equity_actuelle
    drawdown_pct = (drawdown / capital_actuel) * 100
    
    print('💰 ANALYSE FINANCIÈRE RÉELLE DU ROBOT')
    print('=' * 50)
    print(f'Capital initial:     {capital_initial:,.2f} USD')
    print(f'Balance actuelle:    {capital_actuel:,.2f} USD')
    print(f'Equity actuelle:     {equity_actuelle:,.2f} USD')
    print('')
    print(f'Variation balance:   {variation_balance:+,.2f} USD ({variation_pct:+.3f}%)')
    print(f'Drawdown actuel:     -{drawdown:,.2f} USD (-{drawdown_pct:.2f}%)')
    print('')
    
    # Évaluation de performance
    print('📊 ÉVALUATION DE PERFORMANCE:')
    if variation_balance < 0:
        print(f'🔴 PERTE nette de {abs(variation_balance):.2f} USD')
        print('   Le robot a généré une perte sur la période')
    else:
        print(f'🟢 GAIN net de {variation_balance:.2f} USD')
        print('   Le robot a généré un profit sur la période')
    
    print(f'⚠️  Drawdown: {drawdown:.2f} USD (positions ouvertes en négatif)')
    
    # Performance par rapport aux ordres générés
    total_ordres = 58  # Du précédent rapport
    mt5_success = 5    # Ordres réellement exécutés
    
    print('\n🎯 EFFICACITÉ OPÉRATIONNELLE:')
    print(f'Total ordres générés:   {total_ordres}')
    print(f'Ordres exécutés MT5:    {mt5_success}')
    print(f'Taux d\'exécution:       {(mt5_success/total_ordres)*100:.1f}%')
    
    if mt5_success > 0:
        perte_par_ordre = abs(variation_balance) / mt5_success
        print(f'Perte moyenne/ordre:    {perte_par_ordre:.2f} USD')
    
    # Risk Management
    print('\n⚡ GESTION DES RISQUES:')
    if drawdown_pct < 2:
        print('🟢 Drawdown acceptable (< 2%)')
    elif drawdown_pct < 5:
        print('🟡 Drawdown modéré (2-5%)')
    else:
        print('🔴 Drawdown élevé (> 5%)')
        
    print('\n📋 RECOMMANDATIONS:')
    if variation_balance < 0:
        print('- Revoir la stratégie de Stop Loss/Take Profit')
        print('- Analyser les erreurs "Invalid stops" MT5')
        print('- Réduire la taille des positions')
        print('- Vérifier les signaux de trading')

if __name__ == "__main__":
    analyze_financial_performance()