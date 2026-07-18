"""Mock-backend primitive hardware actions.

Mirror of :mod:`lab_communicator.real.primitives` for the mock
backend. Each ``primitive_<name>`` function is what would call the
robot in real -- in mock, "calling the robot" reduces to an
``asyncio.sleep`` (to simulate hardware latency) plus a
domain-appropriate return value (e.g. a noisy ``LabPose`` for the
move primitive, the canonical hover height for pick).

The mock backend is the canonical *UI-exercise* environment: it has
to faithfully animate every state transition the real backend
produces, so a few primitives (``primitive_scan_rotate_in_place``,
``primitive_optimize_component``) drive a stepwise loop with the
orchestrator-provided callback (``on_rotation_update`` /
``progress_callback``) instead of just sleeping.

Architectural rules (``lab_communicator/README.md``, ``lab_model.platform``):

- ``self.current_state`` access is forbidden in ``_primitive_*`` hook
  bodies; the orchestrator passes data via arguments. This module
  imports nothing from :mod:`lab_communicator.base` (avoid the
  circular-import trap from Â§5.1 rule 5) and nothing from
  :mod:`lab_communicator.real` (cross-backend isolation).
- The synthetic-PNG branch in :func:`primitive_record_measurables`
  reads ``communicator.capture_table_cam(...)``, which itself doesn't
  touch state -- it's purely an image-rendering helper.
"""

from __future__ import annotations

import asyncio
import os
import random
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional

from lab_model.language.domain.component import (
    PRESENCE_BREADBOARD,
    PRESENCE_STORAGE,
    new_component_entry,
)
from lab_model.language.domain.holding import DEFAULT_HOVER_Z_MM
from lab_model.language.domain.storage_region import (
    find_storage_slot_and_center,
    random_placed_position,
)

from lab_model.coordinator.state.snapshot import LabPose

if TYPE_CHECKING:
    from mock_edge.host.communicator import MockLabCommunicator


def _mock_breadboard_position(
    communicator: "MockLabCommunicator",
    existing_components: Dict[str, Any],
    tag_id: str,
    width_mm: float,
    height_mm: float,
) -> Optional[tuple[float, float]]:
    """Random valid breadboard pose (placed region, no overlaps)."""
    return random_placed_position(
        existing_components,
        tag_id,
        width_mm,
        height_mm,
        lambda tid: communicator._get_component_wh(tid),
    )


# ---------------------------------------------------------------------------
# Motor primitives
# ---------------------------------------------------------------------------

async def primitive_move_motor(
    communicator: "MockLabCommunicator",  # noqa: ARG001
    target_id: str,  # noqa: ARG001
    motor_id: int,  # noqa: ARG001
    distance: float,  # noqa: ARG001
) -> None:
    """Mock hardware step for ``move_motor``.

    No real motor: just sleep so the UI can observe the BUSY
    transition. The orchestrator owns refusals, the catalog gate, the
    BUSY/IDLE flip, and the ``motor_rotation_store`` bookkeeping.
    """
    await asyncio.sleep(1)


# ---------------------------------------------------------------------------
# In-air primitives (PICK / HOVER / PLACE_FROM_HOVER / SCAN_ROTATE)
# ---------------------------------------------------------------------------

async def primitive_pick_component(
    communicator: "MockLabCommunicator",  # noqa: ARG001
    target_id: str,  # noqa: ARG001
    commanded: LabPose,  # noqa: ARG001
    params: Dict[str, Any],  # noqa: ARG001
) -> float:
    """Mock hardware step for ``pick_component``.

    Sleeps to simulate the dive + grip + retract cycle and reports the
    canonical hover height. The return value lands in
    ``tunables.nominal_pose.z``, ``measurables.pose.z``, and
    ``state["holding"].nominal_pose.z`` via :func:`commit_pick`.
    """
    await asyncio.sleep(1.2)
    return float(DEFAULT_HOVER_Z_MM)


async def primitive_hover_component(
    communicator: "MockLabCommunicator",  # noqa: ARG001
    target_id: str,  # noqa: ARG001
    commanded: LabPose,
    speed: int,  # noqa: ARG001
) -> Optional[LabPose]:
    """Mock hardware step for ``hover_component``.

    Sleeps to simulate the move, then returns the commanded pose plus
    a small Gaussian XY noise so the UI sees a realistic
    "actually-achieved" jitter on ``measurables.pose``. Tunables and
    the holding record use the commanded pose verbatim -- that's the
    orchestrator's responsibility, not the primitive's.
    """
    await asyncio.sleep(1.2)
    return LabPose(
        x=commanded.x + random.uniform(-0.3, 0.3),
        y=commanded.y + random.uniform(-0.3, 0.3),
        z=commanded.z,
        rotation=commanded.rotation,
    )


async def primitive_place_from_hover(
    communicator: "MockLabCommunicator",  # noqa: ARG001
    target_id: str,  # noqa: ARG001
    commanded: LabPose,  # noqa: ARG001
    params: Dict[str, Any],  # noqa: ARG001
) -> None:
    """Mock hardware step for ``place_from_hover`` -- pure delay.

    Per the Phase 2B contract (``communicator_refactor.md`` Â§6.1), the
    place-from-hover primitive returns ``None`` and the orchestrator
    commits the commanded pose verbatim. Earlier mock versions added
    Gaussian noise to ``measurables.pose`` here; that visual flavor
    was incidental and not relied on by tests, so it was removed for
    parity with real (which writes commanded pose because robot
    precision beats top-camera-through-gripper).
    """
    await asyncio.sleep(1.5)


async def primitive_scan_rotate_in_place(
    communicator: "MockLabCommunicator",  # noqa: ARG001
    *,
    target_id: str,  # noqa: ARG001
    mode: str,  # noqa: ARG001
    theta_min: float,
    theta_max: float,
    speed: float,
    axis: str,  # noqa: ARG001
    base_x: float,  # noqa: ARG001
    base_y: float,  # noqa: ARG001
    base_z: Optional[float],  # noqa: ARG001
    params: Dict[str, Any],  # noqa: ARG001
    on_rotation_update: Callable[[float], None],
) -> None:
    """Mock hardware step for ``scan_rotate_in_place``.

    Drives a stepwise sweep so the UI sees rotation animate. Every
    step calls ``on_rotation_update(rotation)``, the closure the
    orchestrator constructed -- this is the architectural escape
    hatch that lets the primitive publish state updates without ever
    touching ``self.current_state`` directly (CI lint enforces that
    primitives are state-clean).

    The final ``theta_max`` commit is the orchestrator's job; we stop
    one step short so the orchestrator's terminal commit isn't
    double-counted.
    """
    total_deg = abs(theta_max - theta_min)
    duration_s = total_deg / speed if speed > 0 else 0.0
    duration_s = min(duration_s, 10.0)  # cap for UI responsiveness
    steps = max(1, min(20, int(duration_s * 4)))
    step_sleep = duration_s / steps if steps > 0 else 0.0

    for i in range(1, steps):
        frac = i / steps
        cur_rot = theta_min + (theta_max - theta_min) * frac
        on_rotation_update(cur_rot)
        await asyncio.sleep(step_sleep)


# ---------------------------------------------------------------------------
# On-table primitives (move / store / place_from_storage / repack / recenter)
# ---------------------------------------------------------------------------

async def primitive_move_component(
    communicator: "MockLabCommunicator",  # noqa: ARG001
    target_id: str,  # noqa: ARG001
    commanded: LabPose,
) -> Optional[LabPose]:
    """Mock hardware step for the move-on-table primitive family.

    All five move-family primitives (``move_component``,
    ``store_component``, ``place_from_storage``,
    ``repack_storage_slot``, ``recenter_stored_in_inventory``) share
    this single primitive. Mock simulates the hardware delay and
    surfaces a noisy pose so the UI can render measurement noise on
    ``measurables.pose``.
    """
    await asyncio.sleep(2)
    return LabPose(
        x=commanded.x + random.uniform(-0.5, 0.5),
        y=commanded.y + random.uniform(-0.5, 0.5),
        z=commanded.z,
        rotation=commanded.rotation,
    )


# ---------------------------------------------------------------------------
# Camera measurement primitive (OPTICAL_CAMERA only)
# ---------------------------------------------------------------------------

async def primitive_record_measurables(
    communicator: "MockLabCommunicator",
    tag_id: str,
    catalog_meta: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Mock hardware step â€” delegates to :mod:`lab_model.language.measurables`."""
    from lab_model.language import measurables  # noqa: F401 â€” register plugins
    from lab_model.language.measurables.record import observe_for_tag

    return await observe_for_tag(communicator, tag_id, catalog_meta)


# ---------------------------------------------------------------------------
# Optimization primitive (mock simulates a multi-step run)
# ---------------------------------------------------------------------------

async def primitive_optimize_component(
    communicator: "MockLabCommunicator",
    *,
    target_id: str,
    strategy_name: str,  # noqa: ARG001 -- mock ignores the strategy name
    params: Dict[str, Any],  # noqa: ARG001
    progress_callback: Callable[..., None],
) -> Optional[Dict[str, Any]]:
    """Mock hardware step for ``optimize_component``.

    Simulates a multi-step optimization run, ticking
    ``progress_callback(step=k)`` between sleeps so the UI's progress
    bar animates. Returns ``{"score": 0.99, "final_pose": ...}`` where
    ``final_pose`` keeps the existing XY but adds a small Gaussian
    rotation drift -- mock's stand-in for "the optimizer nudged the
    rotation". The orchestrator hands this dict to
    :func:`commit_optimization_complete`.

    Snapshots the part's current pose under
    :meth:`return_measurables_for_tag` (lock-aware accessor), keeping
    this primitive state-clean (no direct ``self.current_state``
    reads).
    """
    cur = (communicator.return_measurables_for_tag(target_id) or {}).get("pose") or {}
    base_x = float(cur.get("x", 0.0))
    base_y = float(cur.get("y", 0.0))
    base_rot = float(cur.get("rotation", 0.0))

    steps = 6  # arbitrary -- enough for the UI to animate
    per_step = 0.5
    for k in range(1, steps + 1):
        await asyncio.sleep(per_step)
        progress_callback(step=k)

    return {
        "score": 0.99,
        "final_pose": {
            "x": base_x,
            "y": base_y,
            "rotation": base_rot + random.uniform(-1.0, 1.0),
        },
    }


# ---------------------------------------------------------------------------
# Add-component primitive (mock builds a catalog-aware UI demo entry)
# ---------------------------------------------------------------------------

async def primitive_add_component_to_state(
    communicator: "MockLabCommunicator",
    component_data: Dict[str, Any],
    existing_components: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Mock-side override: build a catalog-aware UI demo entry.

    Mock supports a richer ``add_component_to_state`` surface than
    real because the mock backend doubles as the UI / E2E demo
    environment. The base orchestrator handles the duplicate-tag
    guard and the actual state insertion; this primitive just builds
    the entry dict, picking either a free storage slot or a free
    breadboard pose depending on ``placement_mode``.
    """
    tag_id = component_data.get("tag_id")
    comp_type = component_data.get("type", "OPTICAL_MIRROR")
    placement_mode = (component_data.get("placement_mode") or "breadboard").lower()
    w, h = communicator._get_component_wh(tag_id)

    if placement_mode == "storage":
        slot = find_storage_slot_and_center(
            existing_components,
            tag_id,
            w,
            h,
            lambda tid: communicator._get_component_wh(tid),
        )
        if not slot:
            print("[MOCK LAB] FAILED to find free storage slot.")
            return None
        x, y, si, sj = slot
        return new_component_entry(
            tag_id,
            comp_type,
            presence=PRESENCE_STORAGE,
            nominal_pose={"x": x, "y": y, "rotation": 0.0},
            meas_pose={"x": x, "y": y, "rotation": 0.0},
            placement_mode="STORAGE",
            in_storage=True,
            slot={"i": si, "j": sj},
        )

    pos = _mock_breadboard_position(communicator, existing_components, tag_id, w, h)
    if not pos:
        print("[MOCK LAB] FAILED to find free pose for component.")
        return None
    x, y = pos
    entry = new_component_entry(
        tag_id,
        comp_type,
        presence=PRESENCE_BREADBOARD,
        nominal_pose={"x": x, "y": y, "rotation": 0.0},
        meas_pose={"x": x, "y": y, "rotation": 0.0},
        placement_mode="MANUAL",
        in_storage=False,
        slot=None,
    )
    return _enrich_from_catalog(tag_id, entry)


def _enrich_from_catalog(tag_id: str, entry: Dict[str, Any]) -> Dict[str, Any]:
    from lab_model.coordinator.catalog.bundle import library_by_tag
    from lab_model.coordinator.state.fixture_seed import enrich_runtime_entry_from_catalog

    lib_row = library_by_tag().get(tag_id)
    if isinstance(lib_row, dict):
        enrich_runtime_entry_from_catalog(entry, lib_row)
    return entry


async def primitive_reactivate_off_table_component(
    communicator: "MockLabCommunicator",
    tag_id: str,
    existing_entry: Dict[str, Any],
    component_data: Dict[str, Any],
    existing_components: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Re-place an OFF_TABLE runtime entry on the breadboard or in storage."""
    import copy

    from lab_model.language.domain.component import (
        PRESENCE_BREADBOARD,
        PRESENCE_STORAGE,
        measurables_bucket,
        set_reported_pose,
        tunables_bucket,
    )

    placement_mode = (component_data.get("placement_mode") or "breadboard").lower()
    entry = copy.deepcopy(existing_entry)
    if component_data.get("type"):
        entry["type"] = component_data["type"]
    w, h = communicator._get_component_wh(tag_id)
    tun = tunables_bucket(entry)
    measurables_bucket(entry)  # ensure shape

    if placement_mode == "storage":
        slot = find_storage_slot_and_center(
            existing_components,
            tag_id,
            w,
            h,
            lambda tid: communicator._get_component_wh(tid),
        )
        if not slot:
            print("[MOCK LAB] FAILED to find free storage slot.")
            return None
        x, y, si, sj = slot
        tun["presence"] = PRESENCE_STORAGE
        tun["nominal_pose"] = {"x": x, "y": y, "rotation": 0.0}
        tun["storage"] = {"in_storage": True, "slot": {"i": si, "j": sj}}
        tun["placement"] = {"mode": "STORAGE"}
        set_reported_pose(entry, {"x": x, "y": y, "rotation": 0.0})
        return _enrich_from_catalog(tag_id, entry)

    pos = _mock_breadboard_position(communicator, existing_components, tag_id, w, h)
    if not pos:
        print("[MOCK LAB] FAILED to find free pose for component.")
        return None
    x, y = pos
    tun["presence"] = PRESENCE_BREADBOARD
    tun["nominal_pose"] = {"x": x, "y": y, "rotation": 0.0}
    tun["storage"] = {"in_storage": False, "slot": None}
    tun["placement"] = {"mode": "MANUAL"}
    set_reported_pose(entry, {"x": x, "y": y, "rotation": 0.0})
    return _enrich_from_catalog(tag_id, entry)


__all__ = [
    "primitive_move_motor",
    "primitive_pick_component",
    "primitive_hover_component",
    "primitive_place_from_hover",
    "primitive_scan_rotate_in_place",
    "primitive_move_component",
    "primitive_record_measurables",
    "primitive_optimize_component",
    "primitive_add_component_to_state",
    "primitive_reactivate_off_table_component",
]
