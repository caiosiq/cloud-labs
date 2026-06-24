"""Small xArm SDK-compatible facade backed by a MuJoCo robot runtime."""

from __future__ import annotations

from typing import Any, Optional, Protocol, Sequence, Tuple


class SimRobotRuntime(Protocol):
    def set_cartesian_pose(
        self,
        pose_mm_axis_angle_deg: Sequence[float],
        *,
        speed_mm_s: float,
    ) -> None: ...

    def get_cartesian_pose(self) -> list[float]: ...

    def set_gripper_position(self, position: float, *, speed: float) -> None: ...

    def get_gripper_position(self) -> float: ...


class XArmAPI:
    """Subset of ``xarm.wrapper.XArmAPI`` used by the MuJoCo controller.

    This package is never imported by the physical ``XArmDriver``. It exists so
    simulator control code can use the same units and return-code conventions:
    Cartesian millimeters, axis-angle degrees, and ``0`` for success.
    """

    def __init__(
        self,
        ip: Optional[str] = None,
        *,
        runtime: Optional[SimRobotRuntime] = None,
    ) -> None:
        del ip
        if runtime is None:
            raise ValueError("sim_xarm.XArmAPI requires a MuJoCo runtime")
        self._runtime = runtime
        self._gripper_speed = 2000.0
        self._mode = 0
        self._state = 0
        self.connected = True

    def motion_enable(self, enable: bool = True) -> int:
        self.connected = bool(enable)
        return 0

    def set_mode(self, mode: int) -> int:
        self._mode = int(mode)
        return 0

    def set_state(self, state: int = 0) -> int:
        self._state = int(state)
        return 0

    def set_position_aa(
        self,
        pose: Sequence[float],
        *,
        speed: float = 100.0,
        mvacc: float = 100.0,
        wait: bool = True,
        is_radian: bool = False,
        **_: Any,
    ) -> int:
        del mvacc, wait
        if is_radian:
            raise ValueError("sim_xarm v1 accepts axis-angle orientation in degrees")
        if len(pose) != 6:
            raise ValueError("set_position_aa expects [x, y, z, rx, ry, rz]")
        self._runtime.set_cartesian_pose(pose, speed_mm_s=float(speed))
        return 0

    def get_position_aa(self, *, is_radian: bool = False) -> Tuple[int, list[float]]:
        if is_radian:
            raise ValueError("sim_xarm v1 returns axis-angle orientation in degrees")
        return 0, self._runtime.get_cartesian_pose()

    def set_gripper_enable(self, enable: bool = True) -> int:
        del enable
        return 0

    def set_gripper_speed(self, speed: float) -> int:
        self._gripper_speed = float(speed)
        return 0

    def set_gripper_position(self, position: float, *, wait: bool = True) -> int:
        del wait
        self._runtime.set_gripper_position(
            float(position),
            speed=self._gripper_speed,
        )
        return 0

    def get_gripper_position(self) -> Tuple[int, float]:
        return 0, self._runtime.get_gripper_position()

    def set_tcp_load(self, *_: Any, **__: Any) -> int:
        return 0

    def set_counter_reset(self) -> int:
        return 0

    def disconnect(self) -> None:
        self.connected = False
