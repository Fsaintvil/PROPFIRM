# Shim non invasif pour rendre importable `advanced_decision_engine`.
try:
    from scripts.advanced_decision_engine import *  # noqa: F401,F403
except Exception:
    # Fallback minimal stub
    class AdvancedDecisionEngine:
        def __init__(self, *args, **kwargs):
            raise ImportError("advanced_decision_engine unavailable")

    __all__ = ["AdvancedDecisionEngine"]
