"""Persistence helpers for the storage-shelf intent manifest.

The "stored intent" is a small persisted map ``tag_id -> {i, j}``: which
catalog tags are *intended* to be in the inventory storage shelf, and
which grid cell each occupies. It survives restarts so that after a
power outage / reboot the system remembers where each part should be
and can reconcile against camera observations.

The on-disk format is shared across communicator backends -- a future
"Robot B" implementation with its own storage shelf would point at a
different file but use the same JSON shape::

    {
        "version": 1,
        "updated_at": "<ISO timestamp>",
        "stored": {
            "tag_id_a": {"i": 0, "j": 1},
            "tag_id_b": {"i": 2, "j": 3},
            ...
        }
    }

This module owns reading/writing that file and nothing else. The
in-memory map and the threading lock that guards it stay on the
communicator instance for now (Phase 2 may promote them to a
:class:`StorageIntentStore` class -- see ``communicator_refactor.md``
§6 / Open question Q11). The bound methods on the communicator (e.g.
``_load_stored_intent_from_disk``) become thin wrappers around the
free functions here.

Architectural rule (``communicator_refactor.md`` §5.1): pure stdlib --
no ``lab_automation``, no :mod:`lab_communicator.base`, no real/mock
backends.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict


StoredIntent = Dict[str, Dict[str, int]]
"""Type alias: ``tag_id -> {"i": int, "j": int}``."""


def load_stored_intent(path: str, *, log_prefix: str = "[STORAGE]") -> StoredIntent:
    """Read and parse the stored-intent manifest from disk.

    Returns ``{}`` (empty manifest) when:
    - the file does not exist (first boot, expected),
    - the file exists but cannot be parsed (logs a warning -- usually
      means hand-edit gone wrong),
    - the file's ``stored`` field is missing or malformed.

    Bad rows inside the ``stored`` block are silently skipped (rather
    than poisoning the whole load), so a single corrupted entry doesn't
    take out the rest of the manifest. Returned value is a new dict --
    safe to mutate.
    """
    out: StoredIntent = {}
    if not os.path.isfile(path):
        return out
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        raw = data.get("stored") or {}
        for tid, slot in raw.items():
            if not isinstance(tid, str) or not isinstance(slot, dict):
                continue
            if "i" in slot and "j" in slot:
                out[tid] = {"i": int(slot["i"]), "j": int(slot["j"])}
    except Exception as e:
        print(f"{log_prefix} Warning: could not load {path}: {e}")
    return out


def save_stored_intent(
    path: str,
    intent: StoredIntent,
    *,
    log_prefix: str = "[STORAGE]",
) -> None:
    """Atomically(ish) write the manifest to disk.

    Creates the parent directory if needed and writes a versioned JSON
    payload sorted by tag id (so diffs are stable across runs). Errors
    are logged but never raised -- the in-memory state remains the
    authoritative copy for this session, and the next mutation will
    retry the write. Today this is *not* truly atomic (no temp-file +
    rename); add that if persistence loss becomes a real concern.
    """
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = {
            "version": 1,
            "updated_at": datetime.now().isoformat(),
            "stored": {
                k: {"i": int(v["i"]), "j": int(v["j"])}
                for k, v in sorted(intent.items())
            },
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except Exception as e:
        print(f"{log_prefix} Warning: could not save stored intent: {e}")


def rebuild_intent_from_components(components: Dict[str, Any]) -> StoredIntent:
    """Derive a fresh manifest from the components block of a snapshot.

    Used after loading a state JSON: the snapshot is the authoritative
    record of which components are STORED and at which slot, and we
    align the manifest to match. Components that are not STORED, or
    that lack a ``storage_slot``, are excluded.

    Pure function -- caller decides when to acquire the state lock and
    when to persist via :func:`save_stored_intent`.
    """
    # Imported lazily so this module remains importable in environments
    # that don't have lab_model on the path (test isolation).
    from lab_model.domain.component import is_stored, storage_slot

    new_m: StoredIntent = {}
    for tid, ent in (components or {}).items():
        if not isinstance(ent, dict):
            continue
        if not is_stored(ent):
            continue
        sl = storage_slot(ent)
        if sl is not None:
            new_m[str(tid)] = {"i": int(sl["i"]), "j": int(sl["j"])}
    return new_m
