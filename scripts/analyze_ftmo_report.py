"""
Analyse du Excel ReportHistory-1513621052.xlsx
Usage: python scripts/analyze_ftmo_report.py <excel_path>
"""
import pandas as pd, sys, numpy as np

path = sys.argv[1] if len(sys.argv) > 1 else "ReportHistory-1513621052.xlsx"
df = pd.read_excel(path, header=6)
df = df[df["Type"].isin(["buy", "sell"])].copy()
df["Volume"] = pd.to_numeric(df["Volume"], errors="coerce")
df = df[df["Volume"].notna()].copy()
df["Profit"] = pd.to_numeric(df["Profit"], errors="coerce")
df["Commission"] = pd.to_numeric(df["Commission"], errors="coerce")

print(f"Total trades: {len(df)}")
print(f"Total PnL: ${df['Profit'].sum():.2f}")
print(f"WR: {(df['Profit']>0).mean()*100:.1f}%")
print(f"PF: {abs(df[df['Profit']>0]['Profit'].sum())/max(1,abs(df[df['Profit']<=0]['Profit'].sum())):.2f}")
print()
for sym in sorted(df["Symbole"].unique()):
    sd = df[df["Symbole"] == sym]
    wins = sd[sd["Profit"] > 0]
    losses = sd[sd["Profit"] <= 0]
    wr = len(wins)/len(sd)*100
    gw = wins["Profit"].sum() if len(wins)>0 else 0
    gl = abs(losses["Profit"].sum()) if len(losses)>0 else 1
    pf = gw/max(1,gl)
    print(f"{sym:8s} | {len(sd):3d} tr | WR={wr:5.1f}% | PnL=${sd['Profit'].sum():+8.2f} | PF={pf:.2f}")
