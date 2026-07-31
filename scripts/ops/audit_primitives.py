#!/usr/bin/env python3
"""Print primitive handler coverage. Run from repo root:

    $env:PYTHONPATH="backend;mock_backend/src"
    python scripts/ops/audit_primitives.py
"""
from __future__ import annotations

import os
import sys

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_BACKEND = os.path.join(_REPO, "backend")
_MOCK_SRC = os.path.join(_REPO, "mock_backend", "src")
for p in (_BACKEND, _MOCK_SRC):
    if p not in sys.path:
        sys.path.insert(0, p)

from mock_backend.host.base import LabCommunicator  # noqa: E402
from mock_backend.host.communicator import MockLabCommunicator  # noqa: E402
from lab_model.language import measurables as _measurables  # noqa: F401, E402
from lab_model.language import tunables as _tunables  # noqa: F401, E402
from lab_model.platform import audit_primitive_handlers  # noqa: E402


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
    _print_table(
        "MockLabCommunicator handlers",
        audit_primitive_handlers(MockLabCommunicator, label="MockLabCommunicator"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
