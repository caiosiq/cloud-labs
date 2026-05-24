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
``SCAN_TABLE`` route in ``main.py``) keep their
existing API unchanged. Phase 2 will lift the BUSY/IDLE transition into
the base orchestrator and turn the scan body into a ``_do_*`` hook
(see ``communicator_refactor.md`` §6).

Architectural rule (``communicator_refactor.md`` §5.1): real-only --
free to import via duck typing on ``communicator.experiment``. Must
NOT import the mock backend or :mod:`lab_communicator.base`.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, Iterable, Optional

from lab_model.domain.component import (
    PRESENCE_BREADBOARD,
    PRESENCE_OFF_TABLE,
    PRESENCE_STORAGE,
    is_on_table,
    new_component_entry,
)
from lab_model.domain.storage_region import is_storage_region

from lab_model.state.pose_refresh_merge import merge_scan_into_components
from lab_model.state.snapshot import LabPose


if TYPE_CHECKING:
    from lab_communicator.real.communicator import RealLabCommunicator


def initialize_state(
    communicator: "RealLabCommunicator",
    preserve_component_ids: Optional[Iterable[str]] = None,
) -> None:
    """Boot-time scan: load catalog, scan the table, populate components.

    Mutates ``communicator``:

    - ``catalog_map`` -- ``{tag_id_str: catalog_row_dict}``, used by every
      Z-frame transform and motor-id validation downstream.
    - ``component_map`` -- ``{tag_id_str: ManipulableOptic}``, synced from
      ``experiment.registry`` after each scan (Phase 8).
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

    ``preserve_component_ids`` (optional): after the scan builds ``candidate``
    components, merge with the pre-scan ``components`` block so preserved tag
    rows are deep-copied unchanged (tunables + measurables), then reconcile
    ``OpticalComponent.current_location`` for those tags from the preserved
    lab-frame pose — keeping the planner aligned with digital twin snapshots.

    No-ops with a logged warning if the catalog file is missing -- the
    UI then shows an empty inventory rather than crashing the backend.
    """
    print("[REAL LAB] Scanning components...")
    communicator._load_stored_intent_from_disk()

    with communicator._state_lock:
        previous_components: Dict[str, Any] = json.loads(
            json.dumps(communicator.current_state.get("components") or {})
        )

    preserve_frozen = frozenset(
        str(x).strip() for x in (preserve_component_ids or ()) if isinstance(x, str) and x.strip()
    )

    from lab_model.catalog.bundle import merged_catalog_rows

    try:
        catalog = merged_catalog_rows()
    except Exception as e:
        print(f"[REAL LAB] Error loading lab_view catalog bundle: {e}. Cannot scan.")
        communicator._ensure_fixture_components()
        return

    communicator.catalog_map = {
        item.get("tag_id"): item for item in catalog if isinstance(item.get("tag_id"), str)
    }

    # Scan registry manipulables (Phase 8 — no parallel OpticalComponent dict).
    manipulables = list(communicator.experiment.list_manipulables())
    if not manipulables:
        print("[REAL LAB] Warning: registry has no manipulables; check catalog passthrough.")
    else:
        communicator.experiment.scan_components_cloudlab(
            manipulables, force_rescan=True
        )
    communicator._sync_component_map_from_registry()

    # Build the new components block off-lock, then swap.
    new_components: Dict[str, Any] = {}

    for item in catalog:
        tag_id = item.get("tag_id")
        if not tag_id:
            continue

        from lab_model.catalog.schema import catalog_is_fixed_instrument  # noqa: PLC0415

        if catalog_is_fixed_instrument(item):
            continue

        comp = communicator.get_manipulable(tag_id)
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

        nominal_pose = (
            dict(pose)
            if presence != PRESENCE_OFF_TABLE
            else {"x": 0.0, "y": 0.0, "rotation": 0.0}
        )
        entry = new_component_entry(
            tag_id,
            item.get("type", "OPTICAL_MIRROR"),
            presence=presence,
            nominal_pose=nominal_pose,
            meas_pose=dict(pose),
            placement_mode=placement_mode,
            in_storage=(presence == PRESENCE_STORAGE),
            slot=slot,
        )
        new_components[tag_id] = entry
        tun = entry["statecontrol"]["tunables"]
        print(
            f"  entry keys: {list(entry.keys())}, "
            f"tunables.nominal_pose: {tun.get('nominal_pose')}"
        )

    merged_components = merge_scan_into_components(
        previous_components,
        new_components,
        preserve_frozen,
    )

    from lab_model.state.fixture_seed import merge_fixture_components  # noqa: PLC0415

    merged_components = merge_fixture_components(merged_components, catalog)

    with communicator._state_lock:
        communicator.current_state["components"] = merged_components
        communicator.current_state["last_updated"] = datetime.now().isoformat()

    # Digital twin snapshots for preserved rows win over fresh camera poses for
    # those tags — push the preserved lab-frame pose back into OpticalComponent.
    if preserve_frozen:
        for tid in preserve_frozen:
            ent = merged_components.get(tid)
            if not isinstance(ent, dict):
                continue
            communicator._apply_loaded_pose_to_hardware(
                tid,
                LabPose.from_entry(ent),
                is_placed=is_on_table(ent),
            )

    n_bb = len(
        [
            c
            for c in merged_components.values()
            if (c.get("statecontrol") or {}).get("tunables", c.get("tunables") or {}).get("presence")
            == PRESENCE_BREADBOARD
        ]
    )
    n_st = len(
        [
            c
            for c in merged_components.values()
            if (c.get("statecontrol") or {}).get("tunables", c.get("tunables") or {}).get("presence")
            == PRESENCE_STORAGE
        ]
    )
    print(f"[REAL LAB] Scan complete. breadboard={n_bb}, storage={n_st}.")


def refresh_pose_from_camera(
    communicator: "RealLabCommunicator",
    preserve_component_ids: Optional[Iterable[str]] = None,
) -> None:
    """User-triggered re-scan (UI "Refresh poses" button / SCAN_TABLE primitive).

    Wraps :func:`initialize_state` in a BUSY/IDLE status transition and
    clears any in-flight optimization tracking. Always restores
    ``system_status = "IDLE"`` even if the underlying scan raises -- the
    UI is left in a usable state instead of stuck on BUSY.
    """
    with communicator._state_lock:
        communicator.current_state["system_status"] = "BUSY"
    try:
        initialize_state(
            communicator, preserve_component_ids=preserve_component_ids
        )
    finally:
        with communicator._state_lock:
            communicator.current_state["system_status"] = "IDLE"
            communicator.current_state["optimization_step"] = 0
            communicator.current_state["optimization_run_dir"] = None
            communicator.current_state["last_updated"] = datetime.now().isoformat()
