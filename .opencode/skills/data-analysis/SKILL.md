# Skill Data Analysis — Analyse financière avec pandas/numpy

## Quand l'utiliser
- Analyse des trades logs (CSV) pour détecter des patterns
- Calcul de métriques de performance (Sharpe, Sortino, Calmar)
- Aggrégation des résultats de backtest
- Data cleaning et validation des données historiques
- Visualisation des performances (PnL, drawdown, WR rolling)

## 1. Analyse des trades logs

### Chargement et nettoyage
```python
import pandas as pd
import numpy as np

# Charger le fichier CSV des trades
df = pd.read_csv('runtime/trades_log.csv', parse_dates=['time'])

# Filtrer les trades dupliqués (après restart)
df = df.drop_duplicates(subset=['ticket', 'symbol'])

# Nettoyer les lignes avec des valeurs aberrantes
df = df[df['profit'].notna()]
df = df[df['profit'].abs() < 10000]  # éliminer les outliers extrêmes
```

### Métriques rolling
```python
# Win rate rolling sur 50 trades
df['win'] = (df['profit'] > 0).astype(int)
df['wr_50'] = df['win'].rolling(50, min_periods=10).mean()

# Profit factor rolling
def rolling_pf(series, window=50):
    wins = series[series > 0].sum()
    losses = abs(series[series < 0].sum())
    return wins / max(losses, 1)

df['pf_50'] = df['profit'].rolling(window).apply(
    lambda x: rolling_pf(x), raw=False
)
```

### Analyse par symbole
```python
# Top perfos
per_sym = df.groupby('symbol').agg(
    trades=('profit', 'count'),
    wr=('win', 'mean'),
    pnl=('profit', 'sum'),
    avg_win=('profit', lambda x: x[x > 0].mean()),
    avg_loss=('profit', lambda x: x[x < 0].mean()),
).sort_values('pnl', ascending=False)

# Ajouter profit factor
per_sym['pf'] = (
    per_sym['avg_win'] * per_sym['trades'] * per_sym['wr'] /
    max(per_sym['avg_loss'] * per_sym['trades'] * (1 - per_sym['wr']), 1)
)
```

## 2. Métriques financières

### Sharpe Ratio
```python
def sharpe_ratio(profits: pd.Series, risk_free=0.0, periods=252):
    """Ratio de Sharpe annualisé.
    
    periods: 252 pour daily, 52 pour weekly, 12 pour monthly
    """
    excess = profits - risk_free / periods
    if excess.std() == 0:
        return 0.0
    return np.sqrt(periods) * excess.mean() / excess.std()

# Application
daily_pnl = df.resample('D', on='time')['profit'].sum()
sharpe = sharpe_ratio(daily_pnl, periods=365)
```

### Drawdown
```python
def max_drawdown(equity_curve: pd.Series) -> tuple:
    """Calcule le drawdown maximum et sa durée.
    
    Returns:
        (dd_pct, dd_duration_days, peak_date, trough_date)
    """
    peak = equity_curve.expanding().max()
    dd = (equity_curve - peak) / peak
    max_dd_idx = dd.idxmin()
    max_dd = dd[min_max_dd]
    
    # Durée du drawdown
    peak_before = peak[:max_dd_idx].idxmax() if not peak[:max_dd_idx].empty else equity_curve.index[0]
    recovery = dd[max_dd_idx:]
    trough_date = max_dd_idx
    recovery_date = recovery[recovery >= 0].index[0] if any(recovery >= 0) else None
    
    return (abs(max_dd), (recovery_date - peak_before).days if recovery_date else None,
            peak_before, trough_date)
```

### Sortino Ratio (pénalise seulement la volatilité négative)
```python
def sortino_ratio(profits: pd.Series, risk_free=0.0, periods=252):
    downside = profits[profits < 0]
    if len(downside) == 0 or downside.std() == 0:
        return sharpe_ratio(profits, risk_free, periods)
    excess = profits.mean() - risk_free / periods
    return np.sqrt(periods) * excess / downside.std()
```

### Calmar Ratio (rendement / drawdown max)
```python
def calmar_ratio(profits: pd.Series, equity_curve: pd.Series):
    total_return = equity_curve.iloc[-1] / equity_curve.iloc[0] - 1
    max_dd_pct, _, _, _ = max_drawdown(equity_curve)
    return total_return / max_dd_pct if max_dd_pct > 0 else 0
```

## 3. Walk-Forward Analysis

```python
from sklearn.model_selection import TimeSeriesSplit

def walk_forward_analysis(df, model_func, n_splits=5):
    """Walk-forward avec TimeSeriesSplit.
    
    Args:
        df: DataFrame avec colonnes 'feature_1', 'feature_2', ..., 'target'
        model_func: fonction qui prend X_train, y_train et retourne un modèle
    """
    tscv = TimeSeriesSplit(n_splits=n_splits)
    results = []
    
    for i, (train_idx, test_idx) in enumerate(tscv.split(df)):
        train = df.iloc[train_idx]
        test = df.iloc[test_idx]
        
        X_train = train[[c for c in train.columns if c.startswith('feature_')]]
        y_train = train['target']
        X_test = test[[c for c in test.columns if c.startswith('feature_')]]
        y_test = test['target']
        
        model = model_func(X_train, y_train)
        score = model.score(X_test, y_test)
        results.append(score)
        
    return {
        'scores': results,
        'mean': np.mean(results),
        'std': np.std(results),
        'min': np.min(results),
        'max': np.max(results),
    }
```

## 4. Analyse de sur-optimisation (Overfitting)

```python
def detect_overfitting(backtest_wr: float, live_wr: float, 
                       n_trades: int, threshold: float = 0.20):
    """Détecte le sur-optimisation par l'écart backtest→live.
    
    threshold: écart max acceptable (20 pts = 0.20)
    """
    gap = backtest_wr - live_wr
    return {
        'gap': gap,
        'overfitted': gap > threshold,
        'severity': 'CRITICAL' if gap > 0.25 else (
            'WARNING' if gap > 0.20 else (
                'INFO' if gap > 0.10 else 'OK'
            )
        ),
        'message': f"Écart backtest→live de {gap:.1%} "
                   f"({'✅ OK' if gap <= threshold else '🔴 SUR-OPTIMISATION'})"
    }
```

## 5. Visualisation rapide

```python
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

def plot_equity_curve(equity_curve: pd.Series, title="Equity Curve"):
    """Trace la courbe d'equity avec drawdown."""
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), gridspec_kw={'height_ratios': [3, 1]})
    
    # Equity curve
    ax1.plot(equity_curve.index, equity_curve.values, color='blue', linewidth=0.8)
    ax1.set_title(title, fontsize=14)
    ax1.set_ylabel('Equity ($)')
    ax1.grid(True, alpha=0.3)
    
    # Drawdown
    peak = equity_curve.expanding().max()
    dd = (equity_curve - peak) / peak * 100
    ax2.fill_between(dd.index, 0, dd.values, color='red', alpha=0.3)
    ax2.set_ylabel('Drawdown (%)')
    ax2.set_xlabel('Date')
    ax2.grid(True, alpha=0.3)
    
    plt.tight_layout()
    return fig

# WR rolling chart
def plot_wr_rolling(df, window=50, title="Win Rate Rolling"):
    fig, ax = plt.subplots(figsize=(12, 4))
    df['wr_rolling'] = (df['profit'] > 0).rolling(window, min_periods=10).mean()
    ax.plot(df['time'], df['wr_rolling'], color='green', linewidth=0.8)
    ax.axhline(0.50, color='red', linestyle='--', alpha=0.5, label='50%')
    ax.axhline(0.60, color='green', linestyle='--', alpha=0.3, label='60%')
    ax.set_title(title)
    ax.legend()
    return fig
```

## 6. Commandes utiles

```bash
# Analyse rapide des trades
python scripts/validate_strategy.py --csv runtime/trades_log.csv

# Heatmap PnL (année × symbole)
python scripts/heatmap.py

# Backtest complet
python scripts/backtest_multi_tf.py

# Rapport de backtest
python scripts/report_backtest_multi.py --summary

# Validation Walk-Forward
python -m pytest tests/test_walk_forward_validator.py -v
```

## Liens vers le projet
- `scripts/validate_strategy.py` — Validation statistique des trades
- `scripts/heatmap.py` — Heatmap PnL annuelle
- `scripts/backtest_multi_tf.py` — Backtest multi-timeframe
- `scripts/report_backtest_multi.py` — Rapport de backtest
- `runtime/trades_log.csv` — Fichier des trades à analyser
