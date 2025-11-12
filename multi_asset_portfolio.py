"""Enhanced MultiAssetPortfolioOptimizer (inverse-volatility weighted)
Favorise les rééquilibrages fréquents et plus de trades.
"""
from typing import Any, Dict
import numpy as np


class MultiAssetPortfolioOptimizer:
    def __init__(self, volatility_floor: float = 1e-6, normalize: bool = True, **kwargs: Any):
        """
        Args:
            volatility_floor (float): valeur minimale pour éviter la division par zéro
            normalize (bool): normalise la somme des poids à 1.0
        """
        self.volatility_floor = volatility_floor
        self.normalize = normalize
        self.params = kwargs

    def allocate(self, assets: Dict[str, float]) -> Dict[str, float]:
        """
        Alloue dynamiquement le capital en fonction de la volatilité inverse.

        Args:
            assets (dict): dictionnaire {symbole: volatilité_ou_variance}
                           

        Returns:
            dict: {symbole: poids_normalisé}
        """
        if not assets:
            return {}

        # Sécurité contre valeurs nulles ou négatives
        vols = {s: max(abs(v), self.volatility_floor) for s, v in assets.items()}

        # Calcul inverse de la volatilité (actif moins risqué → plus de poids)
        inv_vols = {s: 1.0 / v for s, v in vols.items()}

        # Normalisation à 1.0
        if self.normalize:
            total = sum(inv_vols.values())
            weights = {s: inv_vols[s] / total for s in inv_vols}
        else:
            weights = inv_vols

        return weights

    def optimize(self, historical_data: Dict[str, list]) -> bool:
        """
        Placeholder pour compatibilité moteur.
        Peut être étendu pour calculer la volatilité historique ici.
        """
        # Ici tu pourrais ajouter :
        # - Calcul de volatilité par actif via np.std(historical_data[sym])
        # - Mise à jour des poids avant allocation
        return True
