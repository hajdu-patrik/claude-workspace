"""Makes the repository root importable (`import jev_router`) without installing the package, and
drops the user's ROUTER_* settings (e.g. ROUTER_MIN_CONFIDENCE) before jev_router reads them at import:
the tests run with the defaults, as in CI."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for _name in [n for n in os.environ if n.startswith("ROUTER_")]:
    del os.environ[_name]
