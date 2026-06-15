import numpy as np
import pandas as pd

file_path = 'ReportHistory-1513441721.xlsx'

try:
    # Lire tous les sheets
    xls = pd.ExcelFile(file_path)
    print("="*100)
    print(f"FICHIER: {file_path}")
    print(f"Sheets disponibles: {xls.sheet_names}")
    print("="*100)

    # Lire chaque sheet
    for sheet_name in xls.sheet_names:
        print(f"\n{'='*100}")
        print(f"SHEET: {sheet_name}")
        print(f"{'='*100}")

        df = pd.read_excel(file_path, sheet_name=sheet_name)
        print(f"Dimensions: {df.shape[0]} rows × {df.shape[1]} cols\n")

        print(f"Colonnes: {list(df.columns)}\n")

        print("Aperçu (5 premières lignes):")
        print(df.head().to_string())

        print("\n\nDernières 5 lignes:")
        print(df.tail().to_string())

        print("\n\nTypes de données:")
        print(df.dtypes)

        print("\n\nStatistiques descriptives:")
        print(df.describe().to_string())

        # Analyse spécifique par type de colonne
        numeric_cols = df.select_dtypes(include=[np.number]).columns
        print("\n\nAnalyse numérique détaillée:")
        for col in numeric_cols:
            print(f"  {col}:")
            print(f"    Min: {df[col].min():.2f}, Max: {df[col].max():.2f}, Mean: {df[col].mean():.2f}")
            print(f"    Median: {df[col].median():.2f}, Std: {df[col].std():.2f}")

except Exception as e:
    print(f"Erreur: {e}")
    import traceback
    traceback.print_exc()
