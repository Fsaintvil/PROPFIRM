import pandas as pd

file_path = 'ReportHistory-1513441721.xlsx'

# Lire le fichier sans header
df = pd.read_excel(file_path, sheet_name='Sheet1', header=None)

print("="*120)
print("ANALYSE COMPLÈTE - ReportHistory-1513441721.xlsx")
print("="*120)

# Afficher tout le contenu utile (lignes 0-50)
print("\n>>> MÉTADONNÉES DU RAPPORT (lignes 0-10):")
for i in range(min(15, len(df))):
    row_data = df.iloc[i].values
    print(f"Row {i}: {row_data}")

# Chercher les sections
print("\n\n>>> IDENTIFICATION DES SECTIONS CLÉS:")
for i, row in enumerate(df.iterrows()):
    row_vals = row[1].values
    row_str = str(row_vals[0]) if len(row_vals) > 0 else ""

    if 'Positions' in str(row_vals) or 'Position' in str(row_vals):
        print(f"  Ligne {i}: Positions trouvée")
    if 'Deals' in str(row_vals):
        print(f"  Ligne {i}: Deals trouvée")
    if 'Total' in str(row_vals):
        print(f"  Ligne {i}: Total trouvée")
    if 'Perte' in str(row_vals) or 'Profit' in str(row_vals):
        print(f"  Ligne {i}: Profit/Perte trouvée")

# Rechercher les sections de données
print("\n\n>>> RECHERCHE DE LIGNE D'EN-TÊTE TABLE:")
for i in range(min(100, len(df))):
    row = df.iloc[i]
    # Chercher une ligne avec des noms de colonnes
    if 'Ticket' in str(row.values) or 'Time' in str(row.values) or 'Symbol' in str(row.values):
        print(f"  Ligne {i}: Potentiel en-tête trouvé")
        print(f"    {row.values[:10]}")

# Afficher les lignes importantes
print("\n\n>>> DONNÉES CLÉS (dernière partie du rapport):")
for i in range(max(0, len(df)-50), len(df)):
    row = df.iloc[i]
    non_null = row.dropna()
    if len(non_null) > 0:
        print(f"Ligne {i}: {non_null.values}")
