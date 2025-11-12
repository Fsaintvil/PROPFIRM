"""Compatibility shim for legacy imports of package ``MT5_FTMO_IA``.

This shim is intentionally conservative and minimal. Its goals:

- Allow legacy imports such as ``MT5_FTMO_IA.scripts.foo`` to resolve
    to top-level ``scripts/`` and ``tools/`` directories while the
    repository migrates to a canonical package layout.
- Expose a small diagnostic object ``__shim_info__`` for tests and
    operators to inspect.
- Avoid modifying other source files or changing behaviour beyond
    adjusting package search paths.

Implementation notes:
- We compute a short list of candidate directories and set ``__path__``
    to the subset that actually exists. This makes subimports like
    ``MT5_FTMO_IA.scripts`` resolve via the filesystem.
- A one-time ``UserWarning`` is emitted on first import to remind
    maintainers this is a temporary compatibility shim.
"""

from __future__ import annotations

from pathlib import Path
import sys
import warnings
from typing import List, Dict, Any

# Determine the repository root as the parent directory of this package
_REPO_ROOT = Path(__file__).resolve().parents[1]

# Candidate lookup locations (ordered). Keep conservative and explicit.
_CANDIDATES: List[str] = [
        str(_REPO_ROOT),
        str(_REPO_ROOT / "scripts"),
        str(_REPO_ROOT / "tools"),
        str(_REPO_ROOT / "MT5_FTMO_IA"),
]

# Only include candidates that exist on disk. This avoids adding
# non-existent entries to the import machinery.
__path__: List[str] = [p for p in _CANDIDATES if Path(p).exists()]

# Diagnostic information usable by tests and CI.
__shim_info__: Dict[str, Any] = {
        "shim_active": True,
        "repo_root": str(_REPO_ROOT),
        "candidates": _CANDIDATES,
        "resolved_path": list(__path__),
}


def shim_summary() -> str:
        """Return a short human-readable summary of what the shim configured."""
        lines = [f"MT5_FTMO_IA shim active (repo_root={_REPO_ROOT})"]
        lines.append("Resolved __path__ entries:")
        for p in __path__:
                lines.append(f" - {p}")
        return "\n".join(lines)


__all__ = ["__shim_info__", "shim_summary"]

# Emit a one-time informational warning to aid operators and reviewers.
_WARN_FLAG = "_mt5_ftmo_ia_shim_warned"
if not getattr(sys.modules.get(__name__), _WARN_FLAG, False):
    warnings.warn(
        "MT5_FTMO_IA compatibility shim is active. Prefer migrating imports to the "
        "canonical layout.",
        UserWarning,
    )
    # mark warned on the module object
    if __name__ in sys.modules:
        setattr(sys.modules[__name__], _WARN_FLAG, True)
