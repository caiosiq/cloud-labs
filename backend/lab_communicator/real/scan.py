"""Real-backend table-scan + camera-pose-refresh routines.

Two real-only entry points, both flowing through ``OpticalExperiment.scan_components_cloudlab``:

* :func:`initialize_state` -- the boot-time scan. Loads the catalog,
  builds an ``OpticalComponent`` for every catalog row, asks the
  experiment manager to scan the table (camera-driven), and rebuilds
  ``current_state['components']`` from the resulting poses. Also
  reconciles each component's ``presence`` (BREADBOARD vs STORAGE vs
  OFF_TABLE) using the persisted stored-intent manifest.

* :func:`refresh_pose_from_camera` -- the user-triggered re-scan. Same
  path as the boot scan, wrapped in a BUSY/IDLE status transition and
  an optimization-state reset.

Both helpers take the ``RealLabCommunicator`` instance explicitly. The
class keeps ``def`` wrappers so external call sites
(``RealLabCommunicator.__init__``, the dispatch layer, the
``LabPrimitiveId.SCAN_TABLE`` route in ``lab_primitives``) keep their
existing API unchanged. Phase 2 will lift the BUSY/IDLE transition into
the base orchestrator and turn the scan body into a ``_do_*`` hook
(see ``communicator_refactor.md`` §6).

Architectural rule (``communicator_refactor.md`` §5.1): real-only --
free to import via duck typing on ``communicator.experiment``. Must
NOT import the mock backend or :mod:`lab_communicator.base`.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict

from lab_model.component_model import (
    PRESENCE_BREADBOARD,
    PRESENCE_OFF_TABLE,
    PRESENCE_STORAGE,
    default_measurables,
    default_tunables,
)
from lab_model.storage_region import is_storage_region


if TYPE_CHECKING:
    from lab_communicator.real.communicator import RealLabCommunicator


def initialize_state(communicator: "RealLabCommunicator") -> None:
    """Boot-time scan: load catalog, scan the table, populate components.

    Mutates ``communicator``:

    - ``catalog_map`` -- ``{tag_id_str: catalog_row_dict}``, used by every
      Z-frame transform and motor-id validation downstream.
    - ``component_map`` -- ``{tag_id_str: OpticalComponent}``, the
      lab_automation-side handle for every part the robot might touch.
    - ``current_state['components']`` -- the cloud-labs snapshot. For
      every catalog row we emit a component entry with ``tunables``
      (presence, nominal_pose, storage flag, placement mode) and
      ``measurables`` (observed pose). The ``presence`` field is
      determined by:

      1. ``stored_intent`` says this tag is stowed -> ``STORAGE`` at
         the persisted slot.
      2. ``OpticalComponent.current_location`` is set (camera saw it
         on the table) -> ``BREADBOARD``.
      3. neither -> ``OFF_TABLE``.

    No-ops with a logged warning if the catalog file is missing -- the
    UI then shows an empty inventory rather than crashing the backend.
    """
    print("[REAL LAB] Scanning components...")
    communicator._load_stored_intent_from_disk()

    catalog_path = communicator.catalog_file
    if not catalog_path or not os.path.exists(catalog_path):
        print(f"[REAL LAB] Error: Catalog not found at {catalog_path}. Cannot scan.")
        return

    with open(catalog_path, "r") as f:
        catalog = json.load(f)

    communicator.catalog_map = {
        item.get("tag_id"): item for item in catalog if item.get("tag_id")
    }

    # Build OpticalComponent objects for everything in catalog. The
    # OpticalComponent class lives in ``lab_automation`` -- imported via
    # the communicator module so this file doesn't have a hard dep on
    # the optional ``lab_automation`` install.
    from lab_communicator.real import communicator as _real_comm  # noqa: PLC0415
    OpticalComponent = _real_comm.OpticalComponent

    components_to_scan = []
    for item in catalog:
        tag_id_str = item.get("tag_id")  # e.g. "tag_22"
        if not tag_id_str:
            continue

        # Extract numeric ID from "tag_22" -> 22.
        try:
            numeric_id = int(tag_id_str.replace("tag_", ""))
        except ValueError:
            print(f"[REAL LAB] Warning: Invalid tag format {tag_id_str}")
            continue

        # height_mm is optional in the catalog (older rows may not have it).
        # Only forward it to OpticalComponent when it's well-formed; the
        # Z-frame transform falls back to DEFAULT_COMPONENT_HEIGHT_MM
        # otherwise (see ``real/coordinate_frames.py``).
        hkw: Dict[str, Any] = {}
        raw_h = item.get("height_mm")
        if raw_h is not None:
            try:
                hkw["height_mm"] = float(raw_h)
            except (TypeError, ValueError):
                pass
        comp = OpticalComponent(
            name=item.get("name", tag_id_str), tag_id=numeric_id, **hkw
        )
        components_to_scan.append(comp)
        communicator.component_map[tag_id_str] = comp

    # Physical scan -- camera-driven via lab_automation.
    communicator.experiment.scan_components_cloudlab(
        components_to_scan, force_rescan=True
    )

    # Build the new components block off-lock, then swap.
    new_components: Dict[str, Any] = {}

    for item in catalog:
        tag_id = item.get("tag_id")
        comp = communicator.component_map.get(tag_id)
        # ``current_location`` is the canonical "where is this part now"
        # field after Stage C (fixing.md §5, §7 item 2).
        # ``scan_components_cloudlab`` populates it directly; we do not
        # fall back to ``inventory_location``.
        loc = getattr(comp, "current_location", None) if comp else None

        # --- Debug: what we have for this component ---
        print(f"[REAL LAB] --- {tag_id} ---")
        print(
            f"  comp exists: {comp is not None}, "
            f"current_location exists: {loc is not None}"
        )
        if loc is not None:
            attrs = {}
            for a in ("x", "y", "z", "roll", "pitch", "yaw", "angle", "rx", "ry", "rz"):
                if hasattr(loc, a):
                    attrs[a] = getattr(loc, a)
            print(f"  current_location attrs: {attrs}")
        else:
            print("  (no current_location)")

        if comp and comp.current_location:
            # Found on table (robot frame, written by scan_components_cloudlab).
            loc = comp.current_location
            calc_rotation = getattr(loc, "yaw", None) or 0
            print(
                f"  fallback yaw (deg): {getattr(loc, 'yaw', None)} "
                f"-> rotation: {calc_rotation:.2f}"
            )

            pose = {"x": loc.x, "y": loc.y, "rotation": calc_rotation}
            # Include roll/pitch/yaw so the UI can derive display rz
            # (e.g. from yaw for a top-down view).
            for key in ("roll", "pitch", "yaw"):
                val = getattr(loc, key, None)
                if val is not None:
                    pose[key] = val
            in_q3 = is_storage_region(float(loc.x), float(loc.y))
            stored_slot = communicator._stored_intent.get(tag_id)
            if stored_slot is not None:
                # Intent file says this tag belongs in inventory; do not
                # infer storage from Q3 geometry alone.
                presence = PRESENCE_STORAGE
                placement_mode = "STORAGE"
                slot = {"i": int(stored_slot["i"]), "j": int(stored_slot["j"])}
            else:
                presence = PRESENCE_BREADBOARD
                placement_mode = "MANUAL"
                slot = None
            print(
                f"  pose written: {pose} in_q3={in_q3} "
                f"-> presence={presence} slot={slot}"
            )
        else:
            pose = {"x": 0, "y": 0, "rotation": 0}
            presence = PRESENCE_OFF_TABLE
            placement_mode = "MANUAL"
            slot = None
            print(f"  presence: off_table (pose {pose})")

        tun = default_tunables()
        tun["presence"] = presence
        tun["nominal_pose"] = (
            dict(pose)
            if presence != PRESENCE_OFF_TABLE
            else {"x": 0.0, "y": 0.0, "rotation": 0.0}
        )
        tun["storage"] = {
            "in_storage": presence == PRESENCE_STORAGE,
            "slot": slot,
        }
        tun["placement"] = {"mode": placement_mode}
        meas = default_measurables()
        meas["pose"] = dict(pose)

        entry = {
            "id": tag_id,
            "type": item.get("type", "OPTICAL_MIRROR"),
            "tunables": tun,
            "measurables": meas,
        }
        new_components[tag_id] = entry
        print(
            f"  entry keys: {list(entry.keys())}, "
            f"tunables.nominal_pose: {tun.get('nominal_pose')}"
        )

    with communicator._state_lock:
        communicator.current_state["components"] = new_components
        communicator.current_state["last_updated"] = datetime.now().isoformat()
    n_bb = len(
        [
            c
            for c in new_components.values()
            if (c.get("tunables") or {}).get("presence") == PRESENCE_BREADBOARD
        ]
    )
    n_st = len(
        [
            c
            for c in new_components.values()
            if (c.get("tunables") or {}).get("presence") == PRESENCE_STORAGE
        ]
    )
    print(f"[REAL LAB] Scan complete. breadboard={n_bb}, storage={n_st}.")


def refresh_pose_from_camera(communicator: "RealLabCommunicator") -> None:
    """User-triggered re-scan (UI "Refresh poses" button / SCAN_TABLE primitive).

    Wraps :func:`initialize_state` in a BUSY/IDLE status transition and
    clears any in-flight optimization tracking. Always restores
    ``system_status = "IDLE"`` even if the underlying scan raises -- the
    UI is left in a usable state instead of stuck on BUSY.
    """
    with communicator._state_lock:
        communicator.current_state["system_status"] = "BUSY"
    try:
        initialize_state(communicator)
    finally:
        with communicator._state_lock:
            communicator.current_state["system_status"] = "IDLE"
            communicator.current_state["optimization_step"] = 0
            communicator.current_state["optimization_run_dir"] = None
            communicator.current_state["last_updated"] = datetime.now().isoformat()
