#!/usr/bin/env python3
"""Start the top-level mock_backend Edge Contract server."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "backend"))
sys.path.insert(0, str(_ROOT / "mock_backend" / "src"))

from mock_backend.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
