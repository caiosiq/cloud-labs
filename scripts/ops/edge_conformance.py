#!/usr/bin/env python3
"""CLI wrapper for Edge Contract conformance (Phase 1).

Usage:
  python scripts/ops/edge_conformance.py http://127.0.0.1:8100 --profile stub
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _ensure_package() -> None:
    """Allow running from repo without install when src is on path."""
    try:
        import cloudlabs_edge_dev  # noqa: F401
        return
    except ImportError:
        pass
    root = Path(__file__).resolve().parents[2]
    src = root / "packages" / "cloudlabs_edge_dev" / "src"
    if src.is_dir():
        sys.path.insert(0, str(src))


def main(argv: list[str] | None = None) -> int:
    _ensure_package()
    parser = argparse.ArgumentParser(description="Edge Contract v1 conformance")
    parser.add_argument("url", help="Edge base URL, e.g. http://127.0.0.1:8100")
    parser.add_argument(
        "--profile",
        choices=("stub", "skeleton", "hardware"),
        default="stub",
    )
    args = parser.parse_args(argv)

    from cloudlabs_edge_dev.conformance import main_check

    return main_check(args.url, profile=args.profile)


if __name__ == "__main__":
    raise SystemExit(main())
