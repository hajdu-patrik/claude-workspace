"""Repository root on sys.path, and the user's ROUTER_* settings dropped before jev_router reads
them at import: the tests run with the defaults, as in CI."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for _name in [n for n in os.environ if n.startswith("ROUTER_")]:
    del os.environ[_name]
