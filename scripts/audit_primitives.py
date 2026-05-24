#!/usr/bin/env python3
"""Print primitive handler coverage (Phase 0 audit). Run from repo root:

    python scripts/audit_primitives.py
"""
from __future__ import annotations

import os
import sys

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, os.path.abspath(_BACKEND))

from lab_communicator.base import LabCommunicator  # noqa: E402
from lab_communicator.real.communicator import RealLabCommunicator  # noqa: E402
from lab_model import measurables as _measurables  # noqa: F401, E402
from lab_model import tunables as _tunables  # noqa: F401, E402
from lab_model.platform import (  # noqa: E402
    audit_primitive_handlers,
    audit_real_hardware_hooks,
)


def _print_table(title: str, rows: list[dict]) -> None:
    print(f"\n=== {title} ===")
    if not rows:
        print("(empty)")
        return
    cols = list(rows[0].keys())
    widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in cols}
    hdr = "  ".join(c.ljust(widths[c]) for c in cols)
    print(hdr)
    print("  ".join("-" * widths[c] for c in cols))
    for r in rows:
        print("  ".join(str(r.get(c, "")).ljust(widths[c]) for c in cols))


def main() -> int:
    _print_table("LabCommunicator handlers", audit_primitive_handlers(LabCommunicator))
    _print_table("RealLabCommunicator handlers", audit_primitive_handlers(RealLabCommunicator))
    _print_table("Real hardware _primitive_* hooks", audit_real_hardware_hooks(RealLabCommunicator))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
