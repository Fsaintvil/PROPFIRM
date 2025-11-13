#!/usr/bin/env python3
"""Vérification simple et non invasive de la disponibilité de MetaTrader5.

Ce script importe MetaTrader5, tente d'initialiser la connexion, affiche
quelques informations et se ferme proprement. Il ne réalise aucune opération
de trading ni envoi d'ordres.
"""
from __future__ import annotations

import sys


def main() -> int:
    try:
        import MetaTrader5 as mt5
    except ImportError:
        print("MetaTrader5 package: NOT INSTALLED")
        return 2

    try:
        ok = False
        try:
            ok = mt5.initialize()
            print("mt5.initialize() ->", ok)
        except Exception as e:
            print("mt5.initialize() -> ERROR", e)

        try:
            ver = getattr(mt5, "__version__", None)
            if not ver and hasattr(mt5, "version"):
                try:
                    ver = mt5.version()
                except Exception:
                    ver = None
            if ver:
                print("mt5.version():", ver)
        except Exception:
            pass

        # Ne pas effectuer d'actions de trading. Shutdown propre.
        try:
            mt5.shutdown()
            print("mt5.shutdown() -> done")
        except Exception as e:
            print("mt5.shutdown() -> ERROR", e)

        return 0 if ok else 1

    except Exception as e:
        print("Unexpected error while checking MT5:", e)
        return 3


if __name__ == "__main__":
    code = main()
    sys.exit(code or 0)
