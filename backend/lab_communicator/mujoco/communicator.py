"""Cloud-labs communicator backed by the dedicated MuJoCo process."""

from __future__ import annotations

import asyncio
import copy
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, Mapping, Optional

from lab_communicator.base import LabCommunicator
from lab_communicator.mujoco.client import MuJoCoProcessClient
from lab_communicator.mujoco.runtime import MUJOCO_PLANNER_CUSTOM_IK
from lab_communicator.mujoco.scene import SceneSpec, build_scene_spec
from lab_model.state.snapshot import LabPose


def _simulator_catalog_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    """Expose only the v1 controls the simulator actually implements."""
    out = copy.deepcopy(dict(row))
    capabilities = out.get("capabilities")
    statecontrol = {}
    if isinstance(capabilities, dict):
        original_statecontrol = capabilities.get("statecontrol")
        if isinstance(original_statecontrol, dict):
            tunables = original_statecontrol.get("tunables")
            nominal_pose = (
                copy.deepcopy(tunables.get("nominal_pose"))
                if isinstance(tunables, dict)
                else None
            )
            statecontrol = {
                "tunables": (
                    {"nominal_pose": nominal_pose}
                    if nominal_pose is not None
                    else {}
                ),
                "measurables": {},
            }
    out["capabilities"] = {
        "statecontrol": statecontrol,
        "telemetry": {"teleop": {}, "live_feed": {}},
        "primitives": ["MOVE_COMPONENT"],
    }
    return out


class MujocoLabCommunicator(LabCommunicator):
    """Reuse shared orchestration while replacing only the hardware hook."""

    log_prefix = "[MUJOCO LAB]"
    supported_primitives = frozenset({"MOVE_COMPONENT"})

    def __init__(
        self,
        state: Mapping[str, Any],
        catalog_rows: Iterable[Mapping[str, Any]],
        layout: Mapping[str, Any],
        *,
        client_factory: Callable[..., MuJoCoProcessClient] = MuJoCoProcessClient,
        show_viewer: Optional[bool] = None,
        realtime: Optional[bool] = None,
        planner_backend: str = MUJOCO_PLANNER_CUSTOM_IK,
    ) -> None:
        super().__init__()
        self.planner_backend = str(planner_backend or MUJOCO_PLANNER_CUSTOM_IK)
        rows = [_simulator_catalog_row(row) for row in catalog_rows]
        self.catalog = rows
        self.catalog_map = {
            str(row["tag_id"]): row for row in rows if row.get("tag_id")
        }
        self.current_state = copy.deepcopy(dict(state))
        self._last_runtime_error: Optional[Dict[str, Any]] = None
        self.scene: SceneSpec = build_scene_spec(
            layout,
            rows,
            self.current_state,
        )
        self.client = client_factory(
            self.scene,
            show_viewer=show_viewer,
            realtime=realtime,
            planner_backend=self.planner_backend,
        )
        try:
            self.client.start()
        except Exception:
            self.client.stop()
            raise

    def get_catalog(self) -> list[Dict[str, Any]]:
        return copy.deepcopy(self.catalog)

    def get_lab_state(self) -> Dict[str, Any]:
        state = super().get_lab_state()
        with self._state_lock:
            state["last_runtime_error"] = copy.deepcopy(self._last_runtime_error)
        state["simulator"] = self.simulator_status()
        return state

    def supports_primitive(self, action: str) -> bool:
        return str(action).upper() in self.supported_primitives

    def simulator_status(self) -> Dict[str, Any]:
        return self.client.status()

    def session_checkpoint_enabled(self) -> bool:
        return False

    async def _primitive_move_component(
        self,
        target_id: str,
        commanded: LabPose,
    ) -> Optional[LabPose]:
        with self._state_lock:
            self._last_runtime_error = None
        try:
            result = await asyncio.to_thread(
                self.client.move_component,
                target_id,
                target_x_mm=commanded.x,
                target_y_mm=commanded.y,
                target_rotation_deg=commanded.rotation,
            )
        except Exception as exc:
            details = getattr(exc, "details", None)
            with self._state_lock:
                self._last_runtime_error = {
                    "target_id": target_id,
                    "message": str(exc),
                    "timestamp": datetime.now().isoformat(),
                }
                if isinstance(details, Mapping):
                    self._last_runtime_error["details"] = copy.deepcopy(dict(details))
            raise
        return LabPose(
            x=float(result["x_mm"]),
            y=float(result["y_mm"]),
            z=commanded.z,
            rotation=float(result["rotation_deg"]),
        )

    def set_lab_state(self, state: Dict[str, Any]) -> None:
        del state
        raise RuntimeError("Loading snapshots is unavailable in MuJoCo v1")

    def shutdown_lab_processes(self) -> None:
        self.client.stop()

    def stop_teleop_sweeper(self, *, join_timeout_s: float = 1.0) -> None:
        super().stop_teleop_sweeper(join_timeout_s=join_timeout_s)
        self.client.stop()
