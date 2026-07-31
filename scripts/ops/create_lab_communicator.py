#!/usr/bin/env python3
"""Retired — in-tree lab_communicator backends are gone.

Use ``cloudlabs-edge init`` (packages/cloudlabs_edge_dev) to scaffold a lab
``cloudlabs_edge/`` package, or extend ``mock_backend/`` for teaching physics.
"""
from __future__ import annotations

import sys


def main() -> int:
    print(
        "create_lab_communicator.py retired.\n"
        "  Teaching edge: mock_backend/ (python -m mock_backend)\n"
        "  Lab edge scaffold: cloudlabs-edge init\n"
        "  See backend/lab_communicator/README.md",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
