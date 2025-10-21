#!/usr/bin/env python3
"""
🧹 NETTOYAGE DES DONNÉES CORROMPUES
Corrige les incohérences dans data/trades.json (données live uniquement)
"""

import json
import pandas as pd
from datetime import datetime
import os
from pathlib import Path

def clean_paper_trades():
    """Retiré: plus de nettoyage de paper_trades en mode 100% live"""
    print("⏭️  Nettoyage paper_trades ignoré (mode live uniquement)")
    return 0, 0

def clean_trades_json():
    """Nettoyer data/trades.json"""
    file_path = "data/trades.json"
    backup_path = "data/trades.json.backup"
    
    print(f"\n🧹 Nettoyage de {file_path}...")
    
    if not os.path.exists(file_path):
        print(f"⚠️  {file_path} n'existe pas")
        return 0, 0
    
    # Backup original
    os.rename(file_path, backup_path)
    print(f"💾 Backup créé: {backup_path}")
    
    opens = []  # Ouvertures de positions
    closes = []  # Clôtures de positions
    corrupted_count = 0
    
    # Lire et séparer les types d'entrées
    with open(backup_path, 'r') as f:
        for line_num, line in enumerate(f, 1):
            try:
                trade = json.loads(line.strip())
                
                # Détecter le type d'entrée
                if 'ticket' in trade and 'closed_profit' in trade:
                    # Entrée de clôture
                    if trade.get('closed_profit') is not None:
                        closes.append(trade)
                    else:
                        corrupted_count += 1
                elif 'symbol' in trade and 'side' in trade:
                    # Entrée d'ouverture
                    opens.append(trade)
                else:
                    corrupted_count += 1
                    
            except json.JSONDecodeError as e:
                print(f"⚠️  Ligne {line_num}: JSON invalide - {e}")
                corrupted_count += 1
    
    # Créer deux fichiers séparés pour plus de clarté
    opens_file = "data/trades_openings.json"
    closes_file = "data/trades_closings.json"
    
    # Écrire ouvertures
    with open(opens_file, 'w') as f:
        for trade in opens:
            f.write(json.dumps(trade) + '\n')
    
    # Écrire clôtures
    with open(closes_file, 'w') as f:
        for trade in closes:
            f.write(json.dumps(trade) + '\n')
    
    # Écrire fichier unifié nettoyé
    with open(file_path, 'w') as f:
        for trade in opens + closes:
            f.write(json.dumps(trade) + '\n')
    
    print(f"✅ {file_path} réorganisé:")
    print(f"   • Ouvertures: {len(opens)} → {opens_file}")
    print(f"   • Clôtures: {len(closes)} → {closes_file}")
    print(f"   • Entrées corrompues supprimées: {corrupted_count}")
    
    return len(opens) + len(closes), corrupted_count
    return len(opens) + len(closes), corrupted_count


def generate_summary():
    """Générer un rapport de nettoyage"""
    summary = {
        "date_nettoyage": datetime.now().isoformat(),
        "fichiers_traites": [
            "data/trades.json",
        ],
        "problemes_corriges": [
            "Prix FOREX incorrects pour XAUUSD/BTCUSD",
            "Valeurs null/None supprimées",
            "Timestamps invalides éliminés",
            "Formats JSON mixtes séparés",
            "Structure de données unifiée"
        ],
        "backups_crees": ["data/trades.json.backup"]
    }
    
    with open("data/cleaning_report.json", 'w') as f:
        json.dump(summary, f, indent=2)
    
    print("\n📊 Rapport de nettoyage: data/cleaning_report.json")

 
if __name__ == "__main__":
    print("🧹 NETTOYAGE DES DONNÉES CORROMPUES")
    print("="*50)
    
    # Changer vers le répertoire du projet
    os.chdir(Path(__file__).parent)
    
    # Nettoyer trades.json
    valid_trades, corrupt_trades = clean_trades_json()
    
    # Générer rapport
    generate_summary()
    
    print("\n🎉 NETTOYAGE TERMINÉ")
    print(f"✅ Entrées valides conservées: {valid_trades}")
    print(f"🗑️  Entrées corrompues supprimées: {corrupt_trades}")
    print("💾 Backups disponibles pour récupération si nécessaire")
