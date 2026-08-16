#!/usr/bin/env python3
"""Moniteur de fermeture des positions EURGBP + restart automatique.

⚠️ DÉSACTIVÉ 16 Août 2026 (Audit M-S1 — Robot Manager).
Ce script réinitialisait FAILED_DD → ACTIVE dans robot_state.json pour
re-tenter un challenge FTMO. Depuis le 13 Août 2026 :
- EURGBP est retiré des symboles actifs (perdant structurel, PF 0.64)
- Le challenge FTMO est PERDU (0 jour restant) — la RÈGLE D'OR impose un
  STOP définitif tant que 100 trades propres ne sont pas validés
- Réactiver FAILED_DD contredit directement la décision utilisateur

L'ancien code complet est conservé dans l'historique git (fichier avant
le commit du 16 Août 2026) pour référence.
"""

import sys


def main() -> int:
    print(
        "[MONITOR_RESTART] DÉSACTIVÉ (16 Août 2026) — RÈGLE D'OR active, "
        "challenge FTMO perdu, EURGBP retiré. Arrêt immédiat."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())