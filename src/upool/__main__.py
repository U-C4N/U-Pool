from __future__ import annotations

import sys

# Absolute, not relative: PyInstaller uses this file as the bundle's entry point
# and runs it as a top-level ``__main__`` with no package around it, where a
# relative import raises "attempted relative import with no known parent
# package". ``python -m upool`` is happy either way.
from upool.app import main

if __name__ == "__main__":
    sys.exit(main())
