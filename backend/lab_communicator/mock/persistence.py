"""File-backed state + catalog persistence for the mock backend.

The mock communicator's authoritative source of truth is a JSON file on
disk (the path from ``lab_view/lab_state.json``). Every primitive's pattern is
"read-modify-write" -- read the file, mutate the dict, write it back.
This is *deliberately* slower than the real backend's in-memory
snapshot (which can stream updates to a UI WebSocket without ever
hitting disk). The point of mock is to be a faithful, debuggable
reference: any test can inspect the file at any moment to see exactly
what the system thinks is true, and a stale cache can never lie about
the lab's state because there is no cache.

Per ``communicator_refactor.md`` Q3 (resolved 2026-04-28), Phase 1
keeps that file-backed design as-is. Phase 2 may revisit (lock-and-load
into memory at startup, hand-off to the base orchestrator). For now
this module just consolidates the I/O primitives so they can be unit
tested in isolation.

Architectural rule (``communicator_refactor.md`` §5.1): mock-only --
must NOT import the real backend or :mod:`lab_communicator.base`.
Pure stdlib + ``shared/`` only.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List


def ensure_state_file(state_file: str) -> None:
    """Validate that the mock-lab JSON exists and is a well-formed snapshot.

    Mock refuses to boot without an authoritative state file -- there is
    no "auto-create empty state" path because the file is the spec for
    what components / catalog the demo session shows. Raises
    ``FileNotFoundError`` (file missing), ``ValueError`` (file present
    but missing the ``components`` key, or unparseable JSON), or
    ``RuntimeError`` for any other I/O failure. All three error types
    bubble up to :class:`MockLabCommunicator.__init__` which converts
    them to a CRITICAL log + process exit.
    """
    if not os.path.exists(state_file):
        raise FileNotFoundError(
            f"CRITICAL: Lab State file not found at: {state_file}"
        )

    try:
        with open(state_file, "r") as f:
            data = json.load(f)
            if "components" not in data:
                raise ValueError("Lab State file is missing 'components' key")
    except json.JSONDecodeError:
        raise ValueError(
            f"CRITICAL: Invalid JSON in Lab State file: {state_file}"
        )
    except FileNotFoundError:
        raise
    except ValueError:
        raise
    except Exception as e:
        raise RuntimeError(f"CRITICAL: Failed to load Lab State: {str(e)}")


def read_state(state_file: str) -> Dict[str, Any]:
    """Read and parse the current snapshot from disk.

    Always reads fresh -- there is no cache. Caller is expected to have
    already gone through :func:`ensure_state_file` at startup so the
    file is known to exist and parse. A read after that point that
    fails (file deleted, JSON corrupted by an external editor) raises
    naturally; mock primitives let that propagate so the bug is visible
    rather than silently coercing to an empty state.
    """
    with open(state_file, "r") as f:
        return json.load(f)


def write_state(state_file: str, state: Dict[str, Any]) -> None:
    """Persist a snapshot to disk.

    Pretty-printed (``indent=2``) so a human can diff successive runs
    by hand. Not atomic (no temp-file + rename); a crash mid-write can
    leave a half-truncated file. Mock isn't expected to survive crashes
    cleanly; if you need that, run the real backend or add atomic
    write here in Phase 2.
    """
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)


def load_catalog(catalog_file: str) -> List[Dict[str, Any]]:
    """Load the mock component catalog (a JSON array of catalog rows).

    Returns ``[]`` if the file is missing OR unreadable -- mock with
    no catalog still boots, just with an empty inventory (the UI then
    shows nothing). Logs the failure reason so config bugs aren't
    silent.
    """
    if not os.path.exists(catalog_file):
        return []
    try:
        with open(catalog_file, "r") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        print(
            f"[MOCK LAB] Catalog file at {catalog_file} is not a JSON list; "
            f"using empty catalog."
        )
        return []
    except Exception as e:
        print(f"[MOCK LAB] Failed to load catalog: {e}")
        return []
