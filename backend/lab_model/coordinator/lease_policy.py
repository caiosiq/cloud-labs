"""Session-lease requirement policy for coordinator mutations.

Normative: every hardware-affecting path (Twin, SDK, jobs) holds an exclusive
session lease unless ``CLOUDLABS_SOLO=1`` explicitly opts out.
"""

from __future__ import annotations

import os
from typing import Any, Dict


def solo_mode() -> bool:
    """Local single-operator escape: mutations do not require a session lease."""
    flag = (os.environ.get("CLOUDLABS_SOLO") or "").strip().lower()
    return flag in ("1", "true", "yes")


def command_lease_required(backend_id: str = "") -> bool:
    """Whether mutating commands must present a matching session lease.

    Always required for mock / sim / real unless ``CLOUDLABS_SOLO=1``.
    ``backend_id`` is accepted for call-site symmetry; policy is coordinator-wide.
    """
    del backend_id
    return not solo_mode()


def coordinator_policy() -> Dict[str, Any]:
    required = command_lease_required()
    return {
        "solo": solo_mode(),
        "command_lease_required": required,
        # Deprecated alias: older Twin clients read strict_lease_mock.
        "strict_lease_mock": required,
    }
