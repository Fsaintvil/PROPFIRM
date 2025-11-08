"""Package initialiser for tools utilities.

This file ensures that `import tools` works reliably in the project
and provides a lightweight version marker for runtime checks.
"""
__all__ = []

__version__ = "0.1.0"

def is_tools_package():
    return True
