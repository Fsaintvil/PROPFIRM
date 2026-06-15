"""Analyse tous les fichiers de données disponibles."""
import glob
import os

import pandas as pd

print("=" * 60)
print("1. journal_sf_ia7_template.csv")
print("=" * 60)
df = pd.read_csv("journal_sf_ia7_template.csv", on_bad_lines='skip')
print(f"  Trades: {len(df)}")
print(f"  Date range: {df['date'].min()} to {df['date'].max()}")
dates = sorted(df['date'].unique())
print(f"  Unique dates ({len(dates)}): {dates}")
print(f"  Symbols: {sorted(df['symbol'].unique())}")
print(f"  Directions: {df['direction'].unique()}")
print(f"  PnL sum: {df['pnl'].sum():.2f}")
won = (df["pnl"] > 0).sum()
lost = (df["pnl"] < 0).sum()
print(f"  WR: {won}/{won+lost} = {won/(won+lost)*100:.1f}%")

print()
print("=" * 60)
print("2. TRADE_EXPORT_AUG2025_FEB2026.csv")
print("=" * 60)
df2 = pd.read_csv("TRADE_EXPORT_AUG2025_FEB2026.csv")
print(f"  Rows: {len(df2)}")
print(f"  Columns: {list(df2.columns)}")
print(f"  Symbols: {sorted(df2['symbol'].unique())}")
print(f"  Directions: {df2['direction'].unique()}")
dates2 = sorted(df2['timestamp'].str[:10].unique())
print(f"  Date range: {dates2[0]} to {dates2[-1]}")
print(f"  Unique dates: {len(dates2)}")
print(f"  ret values: {df2['ret'].min()} to {df2['ret'].max()}")
print("  Sample rows:")
print(f"    {df2.iloc[0].to_dict()}")
print(f"    {df2.iloc[1].to_dict()}")

print()
print("=" * 60)
print("3. ReportHistory Excel files in workspace")
print("=" * 60)
for fpath in sorted(glob.glob("ReportHistory*.xlsx")):
    size = os.path.getsize(fpath)
    try:
        xl = pd.ExcelFile(fpath)
        print(f"\n  {os.path.basename(fpath)} ({size//1024}KB)")
        print(f"    Sheets: {xl.sheet_names}")
        for sheet in xl.sheet_names:
            df_s = pd.read_excel(fpath, sheet_name=sheet, nrows=3)
            print(f"    [{sheet}] cols ({len(df_s.columns)}): {list(df_s.columns)}")
    except Exception as e:
        print(f"  {os.path.basename(fpath)}: ERROR {e}")

print()
print("=" * 60)
print("4. Checking SF_IA7 exports")
print("=" * 60)
desktop_sf = os.path.expanduser("~/Desktop/SF_IA7/exports/historical")
if os.path.isdir(desktop_sf):
    files = os.listdir(desktop_sf)
    csvs = [f for f in files if f.endswith(".csv")]
    xlsx = [f for f in files if f.endswith(".xlsx")]
    print(f"  CSV files: {len(csvs)}")
    print(f"  XLSX files: {len(xlsx)}")
    # Sample a few
    for fname in csvs[:5]:
        fpath = os.path.join(desktop_sf, fname)
        size = os.path.getsize(fpath)
        print(f"    {fname} ({size//1024}KB)")
else:
    print(f"  Not found: {desktop_sf}")
