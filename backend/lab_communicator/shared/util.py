"""Tiny pure-Python utility helpers used across communicator backends.

Architectural rule (``communicator_refactor.md`` §5.1): this module
must NOT import :mod:`lab_communicator.base`, :mod:`lab_communicator.real`,
:mod:`lab_communicator.mock`, or :mod:`lab_automation`. Pure stdlib only.

Helpers here are kept deliberately small. Anything bigger gets its own
``shared/<concept>.py`` (storage_intent, motor_state, catalog_lookup, ...).
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional


def env_float(name: str, default: float, *, log_prefix: str = "[ENV]") -> float:
    """Read an environment variable as a float, with logged fallback.

    Used for setup-specific calibration constants that can be overridden
    at deploy time without editing source. ``log_prefix`` controls which
    backend tag appears in the warning (``"[REAL LAB]"`` for the real
    coordinate-frames module, etc.) -- keeps existing log lines stable
    after the Phase 1 extraction.
    """
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        print(
            f"{log_prefix} Warning: env {name}={raw!r} is not a valid float; "
            f"using default {default}."
        )
        return float(default)


def optional_float(params: Optional[Dict[str, Any]], key: str) -> Optional[float]:
    """Parse an optional numeric field from an HTTP-shaped ``params`` dict.

    Returns ``None`` if ``params`` is falsy, the key is missing, the value
    is ``None``, or the value cannot be coerced to ``float``. The HTTP
    surface (``lab_primitives``) already validates required fields; this
    helper is for *optional* fields where "not provided" and "provided
    but bad" should both fall back gracefully to the primitive's default.
    """
    if not params or params.get(key) is None:
        return None
    try:
        return float(params[key])
    except (TypeError, ValueError):
        return None


def tag_id_for_component(component_map: Dict[str, Any], comp: Any) -> Optional[str]:
    """Reverse-lookup of a component object to its tag id.

    The communicator keeps ``component_map: Dict[tag_id, OpticalComponent]``
    as the canonical mapping. Some lab_automation callbacks hand us back
    the component object alone (e.g. a strategy callback fires with the
    component it just placed); we need to recover the tag id to update
    cloud-labs state. Today this is an O(n) identity scan -- the
    component map is small (≲ 30 items) so the cost is negligible. If
    that ever becomes a bottleneck, the communicator can build an
    ``id(c) -> tag`` shadow map at registration time.

    Returns ``None`` if no entry in ``component_map`` is the same object
    as ``comp`` (intentionally identity-based, not equality-based: two
    different OpticalComponent instances may compare equal but represent
    distinct catalog rows).
    """
    for tid, c in component_map.items():
        if c is comp:
            return tid
    return None
