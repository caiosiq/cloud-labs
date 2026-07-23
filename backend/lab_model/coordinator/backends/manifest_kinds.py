"""Known lab_manifest `communicator` field values (mock | real).

In-tree LabCommunicator backends were retired. Manifests may still declare
`mock` (teaching host in `mock_edge`) or `real` (external Edge Contract).
"""
from __future__ import annotations

from typing import FrozenSet


def known_communicator_ids() -> FrozenSet[str]:
    return frozenset({"mock", "real", "simulation"})


# Prefer this name in new code.
known_manifest_kinds = known_communicator_ids
