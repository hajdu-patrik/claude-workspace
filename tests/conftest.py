"""Makes the repository root importable (`import jev_router`) without installing the package."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
