#!/usr/bin/env python3
"""
🧹 NETTOYAGE DES DONNÉES CORROMPUES
Corrige les incohérences dans data/paper_trades.json et data/trades.json
"""

import json
import pandas as pd
from datetime import datetime
import os
from pathlib import Path

def clean_paper_trades():
    """Nettoyer data/paper_trades.json"""
    file_path = "data/paper_trades.json"
    backup_path = "data/paper_trades.json.backup"
    
    print(f"🧹 Nettoyage de {file_path}...")
    
    # Backup original
    if os.path.exists(file_path):
        with open(file_path, 'r') as original:
            content = original.read()
        with open(backup_path, 'w') as backup:
            backup.write(content)
        print(f"💾 Backup créé: {backup_path}")
    else:
        print(f"⚠️  {file_path} n'existe pas")
        return 0, 0
    cleaned_trades = []
    corrupted_count = 0
    
    # Lire le backup
    with open(backup_path, 'r') as f:
        for line_num, line in enumerate(f, 1):
            try:
                trade = json.loads(line.strip())
                
                # Détecter et corriger les prix incorrects
                symbol = trade.get('symbol')
                price = trade.get('price')
                
                # Skip entrées avec données nulles critiques
                if price is None or price == "null":
                    corrupted_count += 1
                    continue
                
                # Corriger prix FOREX appliqués aux autres instruments
                if symbol == "XAUUSD" and isinstance(price, (int, float)):
                    if price < 100:  # Prix FOREX incorrect
                        print(f"⚠️  Ligne {line_num}: XAUUSD prix incorrect {price} (ignoré)")
                        corrupted_count += 1
                        continue
                
                if symbol == "BTCUSD" and isinstance(price, (int, float)):
                    if price < 1000:  # Prix FOREX incorrect
                        print(f"⚠️  Ligne {line_num}: BTCUSD prix incorrect {price} (ignoré)")
                        corrupted_count += 1
                        continue
                
                # Valider format timestamp
                timestamp = trade.get('timestamp')
                if timestamp:
                    try:
                        datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                    except:
                        print(f"⚠️  Ligne {line_num}: timestamp invalide {timestamp}")
                        corrupted_count += 1
                        continue
                
                # Nettoyer les valeurs nulles en strings
                cleaned_trade = {}
                for key, value in trade.items():
                    if value == "null" or value is None:
                        continue  # Supprimer les valeurs null
                    cleaned_trade[key] = value
                
                # Valider structure minimum
                required_fields = ['timestamp', 'symbol']
                if all(field in cleaned_trade for field in required_fields):
                    cleaned_trades.append(cleaned_trade)
                else:
                    corrupted_count += 1
                    
            except json.JSONDecodeError as e:
                print(f"⚠️  Ligne {line_num}: JSON invalide - {e}")
                corrupted_count += 1
                continue
    
    # Écrire les données nettoyées
    with open(file_path, 'w') as f:
        for trade in cleaned_trades:
            f.write(json.dumps(trade) + '\n')
    
    print(f"✅ {file_path} nettoyé:")
    print(f"   • Entrées valides: {len(cleaned_trades)}")
    print(f"   • Entrées corrompues supprimées: {corrupted_count}")
    
    return len(cleaned_trades), corrupted_count

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

def generate_summary():
    """Générer un rapport de nettoyage"""
    summary = {
        "date_nettoyage": datetime.now().isoformat(),
        "fichiers_traites": ["data/paper_trades.json", "data/trades.json"],
        "problemes_corriges": [
            "Prix FOREX incorrects pour XAUUSD/BTCUSD",
            "Valeurs null/None supprimées",
            "Timestamps invalides éliminés",
            "Formats JSON mixtes séparés",
            "Structure de données unifiée"
        ],
        "backups_crees": [
            "data/paper_trades.json.backup",
            "data/trades.json.backup"
        ]
    }
    
    with open("data/cleaning_report.json", 'w') as f:
        json.dump(summary, f, indent=2)
    
    print(f"\n📊 Rapport de nettoyage: data/cleaning_report.json")

if __name__ == "__main__":
    print("🧹 NETTOYAGE DES DONNÉES CORROMPUES")
    print("="*50)
    
    # Changer vers le répertoire du projet
    os.chdir(Path(__file__).parent)
    
    # Nettoyer paper_trades.json
    valid_paper, corrupt_paper = clean_paper_trades()
    
    # Nettoyer trades.json
    valid_trades, corrupt_trades = clean_trades_json()
    
    # Générer rapport
    generate_summary()
    
    print(f"\n🎉 NETTOYAGE TERMINÉ")
    print(f"✅ Entrées valides conservées: {valid_paper + valid_trades}")
    print(f"🗑️  Entrées corrompues supprimées: {corrupt_paper + corrupt_trades}")
    print(f"💾 Backups disponibles pour récupération si nécessaire")