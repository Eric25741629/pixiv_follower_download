import sys
import os

# Ensure the repo root is on sys.path so ``from app.core...`` imports work
# regardless of whether pytest is invoked as ``pytest`` or ``python -m pytest``.
_root = os.path.dirname(os.path.abspath(__file__))
if _root not in sys.path:
    sys.path.insert(0, _root)
