"""`python -m jev_router <command>` - same as `python install.py <command>`."""
import sys

from .cli import main

sys.exit(main())
