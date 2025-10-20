#!/usr/bin/env python3
"""
Script pour corriger automatiquement les erreurs de lint dans live_trading_engine.py
"""

import re

def fix_live_trading_engine():
    """Corriger les erreurs de lint dans live_trading_engine.py"""
    file_path = "scripts/live_trading_engine.py"
    
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Corrections spécifiques
    fixes = [
        # Corriger l'indentation de la liste "high"
        (r'                            "high": \[\n                            p \* \(1 \+ abs\(np\.random\.normal\(0, 0\.0005\)\)\)\n                            for p in prices\n                        \],',
         '''                            "high": [
                                p * (1 + abs(np.random.normal(0, 0.0005)))
                                for p in prices
                            ],'''),
        
        # Corriger l'indentation de la liste "low"
        (r'                            "low": \[\n                            p \* \(1 - abs\(np\.random\.normal\(0, 0\.0005\)\)\)\n                            for p in prices\n                        \],',
         '''                            "low": [
                                p * (1 - abs(np.random.normal(0, 0.0005)))
                                for p in prices
                            ],'''),
    ]
    
    for pattern, replacement in fixes:
        content = re.sub(pattern, replacement, content, flags=re.MULTILINE)
    
    # Corrections générales d'indentation
    lines = content.split('\n')
    fixed_lines = []
    
    for i, line in enumerate(lines):
        # Corriger les lignes trop longues avec des commentaires simples
        if len(line) > 79 and line.strip().startswith('#'):
            # Garder les commentaires tels quels si pas de solution simple
            fixed_lines.append(line)
        # Corriger les lignes avec continuation mal alignée
        elif '                            ' in line and 'for p in prices' in line:
            # Réindenter correctement
            fixed_lines.append(line.replace('                            ', '                                '))
        else:
            fixed_lines.append(line)
    
    # Réécrire le fichier
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(fixed_lines))
    
    print("✅ Corrections appliquées à live_trading_engine.py")

if __name__ == "__main__":
    fix_live_trading_engine()