"""Volume Profile — Analyse du volume par niveau de prix.

Calcule la distribution du volume sur une fenêtre glissante pour identifier :
- Point of Control (POC): niveau avec le plus de volume
- Value Area High/Low (VAH/VAL): 70% du volume
- Volume Nodes: zones de support/résistance dynamiques

Usage:
    vp = VolumeProfile()
    levels = vp.analyze(df)
    if levels["poc"]:
        logger.info(f"POC={levels['poc']:.2f}, VAH={levels['vah']:.2f}, VAL={levels['val']:.2f}")
"""
import logging
import numpy as np
import pandas as pd
from dataclasses import dataclass

logger = logging.getLogger("volume_profile")


@dataclass
class VolumeLevels:
    """Niveaux du Volume Profile."""
    poc: float | None = None        # Point of Control
    vah: float | None = None        # Value Area High
    val: float | None = None        # Value Area Low
    total_volume: float = 0.0
    price_range: tuple[float, float] = (0.0, 0.0)
    num_bins: int = 50
    
    def to_dict(self) -> dict:
        return {
            "poc": self.poc,
            "vah": self.vah,
            "val": self.val,
            "total_volume": self.total_volume,
            "price_range": self.price_range,
            "num_bins": self.num_bins,
        }


class VolumeProfile:
    """Analyse le volume par niveau de prix."""
    
    def __init__(self, num_bins: int = 50, lookback: int = 100,
                 value_area_pct: float = 0.70):
        """
        Args:
            num_bins: Nombre de niveaux de prix
            lookback: Nombre de bougies pour le calcul
            value_area_pct: Pourcentage de la value area (défaut 70%)
        """
        self.num_bins = num_bins
        self.lookback = lookback
        self.value_area_pct = value_area_pct
    
    def analyze(self, df: pd.DataFrame) -> VolumeLevels:
        """Calcule le Volume Profile sur les données récentes.
        
        Args:
            df: DataFrame avec colonnes 'open', 'high', 'low', 'close', 'volume'
        
        Returns:
            VolumeLevels avec POC, VAH, VAL
        """
        if df is None or len(df) < 10:
            return VolumeLevels()
        
        # Take last N bars
        data = df.tail(self.lookback).copy()
        
        if "volume" not in data.columns or data["volume"].sum() == 0:
            return VolumeLevels()
        
        # Price range
        high = data["high"].max()
        low = data["low"].min()
        
        if high == low:
            return VolumeLevels()
        
        # Create price bins
        bin_edges = np.linspace(low, high, self.num_bins + 1)
        bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
        
        # Distribute volume across bins
        volume_profile = np.zeros(self.num_bins)
        
        for _, row in data.iterrows():
            bar_high = row["high"]
            bar_low = row["low"]
            bar_vol = row["volume"]
            
            if bar_vol == 0:
                continue
            
            # Find which bins this bar touches
            mask = (bin_edges[:-1] <= bar_high) & (bin_edges[1:] >= bar_low)
            
            if mask.sum() == 0:
                continue
            
            # Distribute volume proportionally
            vol_per_bin = bar_vol / mask.sum()
            volume_profile[mask] += vol_per_bin
        
        # Total volume
        total_volume = volume_profile.sum()
        
        if total_volume == 0:
            return VolumeLevels()
        
        # Point of Control (highest volume)
        poc_idx = np.argmax(volume_profile)
        poc = bin_centers[poc_idx]
        
        # Value Area (70% of total volume around POC)
        target_volume = total_volume * self.value_area_pct
        accumulated = volume_profile[poc_idx]
        
        va_low_idx = poc_idx
        va_high_idx = poc_idx
        
        while accumulated < target_volume:
            # Expand to neighbor with more volume
            low_vol = volume_profile[va_low_idx - 1] if va_low_idx > 0 else 0
            high_vol = volume_profile[va_high_idx + 1] if va_high_idx < self.num_bins - 1 else 0
            
            if low_vol == 0 and high_vol == 0:
                break
            
            if low_vol >= high_vol:
                va_low_idx -= 1
                accumulated += volume_profile[va_low_idx]
            else:
                va_high_idx += 1
                accumulated += volume_profile[va_high_idx]
        
        val = bin_centers[va_low_idx]
        vah = bin_centers[va_high_idx]
        
        return VolumeLevels(
            poc=poc,
            vah=vah,
            val=val,
            total_volume=total_volume,
            price_range=(low, high),
            num_bins=self.num_bins,
        )
    
    def get_support_resistance(self, df: pd.DataFrame,
                               num_levels: int = 3) -> list[tuple[float, str]]:
        """Retourne les niveaux S/R basés sur le volume.
        
        Returns:
            Liste de (prix, type) — type est "support" ou "resistance"
        """
        levels = self.analyze(df)
        sr = []
        
        if levels.poc is not None:
            current_price = df["close"].iloc[-1]
            
            # POC as S/R
            if levels.poc < current_price:
                sr.append((levels.poc, "support"))
            else:
                sr.append((levels.poc, "resistance"))
            
            # VAL as support
            if levels.val is not None and levels.val < current_price:
                sr.append((levels.val, "support"))
            
            # VAH as resistance
            if levels.vah is not None and levels.vah > current_price:
                sr.append((levels.vah, "resistance"))
        
        # Sort by distance to current price
        if sr:
            current_price = df["close"].iloc[-1]
            sr.sort(key=lambda x: abs(x[0] - current_price))
        
        return sr[:num_levels]
    
    def is_near_poc(self, price: float, df: pd.DataFrame,
                    tolerance_pct: float = 0.1) -> bool:
        """Vérifie si le prix est près du POC."""
        levels = self.analyze(df)
        
        if levels.poc is None:
            return False
        
        distance_pct = abs(price - levels.poc) / levels.poc * 100
        return distance_pct <= tolerance_pct


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================
_default_vp = VolumeProfile()

def analyze(df: pd.DataFrame) -> VolumeLevels:
    """Analyse le volume profile (fonction convenience)."""
    return _default_vp.analyze(df)

def get_support_resistance(df: pd.DataFrame, num_levels: int = 3) -> list[tuple[float, str]]:
    """Retourne les niveaux S/R (fonction convenience)."""
    return _default_vp.get_support_resistance(df, num_levels)

def is_near_poc(price: float, df: pd.DataFrame, tolerance_pct: float = 0.1) -> bool:
    """Vérifie si le prix est près du POC (fonction convenience)."""
    return _default_vp.is_near_poc(price, df, tolerance_pct)
