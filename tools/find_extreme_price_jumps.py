"""Inspecteur non-invasif pour détecter sauts/prix invalides dans les données sources.

Affiche les lignes où les returns absolus sont extrêmes ou où les prix sont <=0/NaN.
Ne modifie rien en prod.
"""
import os
import pandas as pd
import numpy as np


def load_df():
    path = os.path.join("data", "features_sample.csv")
    if os.path.exists(path):
        df = pd.read_csv(path)
        if "Unnamed: 0" in df.columns:
            df = df.set_index("Unnamed: 0")
            try:
                df.index = pd.to_datetime(df.index)
            except Exception:
                pass
        return df
    print("data/features_sample.csv introuvable")
    return None


def main():
    df = load_df()
    if df is None:
        return

    price_col = "close" if "close" in df.columns else df.columns[0]
    s = df[price_col]

    # invalid prices
    invalid = df[(s.isna()) | (s <= 0)]
    if not invalid.empty:
        print("Lignes avec prix invalides (<=0 or NaN):")
        print(invalid.head(20))
    else:
        print("Pas de prix invalides trouvés")

    # compute returns
    returns = s.pct_change()
    df = df.assign(_returns=returns)

    # show top absolute returns
    thr = 1.0  # 100% change
    extreme = df[df._returns.abs() > thr].sort_values(by="_returns", key=lambda x: x.abs(), ascending=False)
    print(f"\nNombre de returns absolus > {thr*100:.0f}%: {len(extreme)}")
    if not extreme.empty:
        print(extreme[[price_col, "_returns"]].head(50))

    # show top 20 absolute returns
    top = df.sort_values(by="_returns", key=lambda x: x.abs(), ascending=False).head(20)
    print("\nTop 20 des retours absolus:")
    print(top[[price_col, "_returns"]])


if __name__ == "__main__":
    main()
