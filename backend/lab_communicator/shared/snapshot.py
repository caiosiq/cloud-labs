"""Snapshot load (``set_lab_state``) shared scaffolding.

When the user clicks "Load State" in the UI, both backends do the same
thing structurally:

1. Validate the input is a dict.
2. Build a ``merged`` components block: catalog tags currently in
   ``current_state`` that are missing from the snapshot keep their
   existing entries (preserves camera-estimated poses for parts added
   after the save); snapshot entries overwrite matching tags.
3. Wrap the merged components in a ``new_state`` dict that has a
   normalized top: ``system_status = "IDLE"``, ``holding`` reset to
   empty, ``last_updated`` refreshed, ``optimization_step`` coerced to
   int.
4. Swap ``self.current_state = new_state`` under the lock.
5. For each loaded component, call a per-backend hook to push the pose
   to hardware (real transforms XY/Z/yaw and writes
   ``OpticalComponent.current_location``; mock no-op).
6. Run a per-backend post-step (real rebuilds the persisted stored-
   intent file; mock no-op).
7. Persist (mock writes the JSON file; real no-op).

This module owns steps 1, 2, 3 and provides the ``LabPose`` dataclass
that step 5's hook receives. Steps 4-7 live in
:meth:`lab_communicator.base.LabCommunicator.set_lab_state`, which
calls into this module and then drives the per-backend hooks.

Architectural rule (``communicator_refactor.md`` §5.1): pure stdlib +
``lab_model``. No ``lab_automation``, no :mod:`lab_communicator.base`
import (would be circular -- see Trap 3 in §7.3 of the refactor doc).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Set, Tuple

from lab_model.holding import empty_holding


@dataclass(frozen=True)
class LabPose:
    """Lab-frame pose passed to ``_apply_loaded_pose_to_hardware``.

    Always in **lab frame** (the UI / cloud-labs convention):

    - ``x``, ``y`` -- millimeters on the breadboard, lab axes.
    - ``z`` -- millimeters above the breadboard surface (z_lab = 0
      means resting on the table).
    - ``rotation`` -- degrees, lab top-down rotation.

    Backends with a robot frame (real) transform these into robot frame
    inside their hook implementation; backends without (mock) leave
    them alone.
    """

    x: float
    y: float
    z: float
    rotation: float

    @classmethod
    def from_entry(cls, entry: Dict[str, Any]) -> "LabPose":
        """Extract a ``LabPose`` from a component entry's ``measurables.pose``.

        Defaults all four fields to 0.0 when missing -- snapshot files
        from before the z-convention landed don't have ``z``, and a
        z_lab of 0 means "on the breadboard" which is almost always
        what those older snapshots intend.
        """
        pose = ((entry or {}).get("measurables") or {}).get("pose") or {}
        return cls(
            x=float(pose.get("x", 0.0)),
            y=float(pose.get("y", 0.0)),
            z=float(pose.get("z", 0.0)),
            rotation=float(pose.get("rotation", 0.0)),
        )


def merge_snapshot_components(
    snapshot_components: Dict[str, Any],
    prev_components: Dict[str, Any],
    catalog_ids: Set[str],
    *,
    log_prefix: str = "[LAB]",
) -> Tuple[Dict[str, Any], List[str]]:
    """Build the merged components block following the load-merge rule.

    Returns ``(merged_components, kept_tag_ids)``. ``kept_tag_ids``
    lists the tags that were preserved from ``prev_components`` because
    they're in the catalog but absent from the snapshot. Caller logs
    that list when non-empty.

    Pure function -- caller decides when to acquire/release the state
    lock around reading ``prev_components``.
    """
    merged: Dict[str, Any] = {}
    kept: List[str] = []
    for tag_id, prev_entry in prev_components.items():
        if tag_id in catalog_ids and tag_id not in snapshot_components:
            # Deep-copy so a later in-place mutation of ``current_state``
            # doesn't leak back into the previous-snapshot view some
            # caller may still hold.
            merged[tag_id] = json.loads(json.dumps(prev_entry))
            kept.append(tag_id)
    for tag_id, loaded_entry in snapshot_components.items():
        merged[tag_id] = loaded_entry
    return merged, kept


def normalize_loaded_state(
    snapshot: Dict[str, Any], merged_components: Dict[str, Any]
) -> Dict[str, Any]:
    """Wrap ``merged_components`` in a top-level state dict ready to swap.

    The returned dict is safe to assign to ``self.current_state``:

    - ``components`` set to ``merged_components``.
    - ``system_status`` forced to ``"IDLE"`` (loading clears any
      transient BUSY/HOLDING/OPTIMIZING state).
    - ``holding`` forced to :func:`lab_model.holding.empty_holding` --
      loading a snapshot must never inherit a HOLDING claim. The real
      backend's gripper reconciliation on boot is the single source of
      truth for "is something in the gripper?". Mock has no gripper at
      all.
    - ``optimization_step`` coerced to int (older snapshots stored it
      as a string).
    - ``optimization_run_dir`` defaulted to ``None`` when absent.
    - ``optimization_target_id`` defaulted to ``None`` when absent.
    - ``last_updated`` refreshed.

    All other top-level keys from the snapshot are preserved verbatim.
    """
    out = dict(snapshot)
    out["components"] = merged_components
    out["system_status"] = "IDLE"
    out["optimization_step"] = int(out.get("optimization_step", 0) or 0)
    if "optimization_run_dir" not in out:
        out["optimization_run_dir"] = None
    if "optimization_target_id" not in out:
        out["optimization_target_id"] = None
    out["holding"] = empty_holding()
    out["last_updated"] = datetime.now().isoformat()
    return out
