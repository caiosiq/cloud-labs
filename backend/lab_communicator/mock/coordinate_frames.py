"""Mock-backend coordinate-frame transforms.

Mock state lives natively in the **lab frame** (the same frame the UI
and HTTP API use). There is no robot to talk to and therefore nothing
to transform between -- every helper here is the identity. The module
exists at all for two reasons:

1. **Per-backend coordinate_frames is the architectural marker** for a
   communicator backend (see ``communicator_refactor.md`` §3.2 / §5.1).
   Having an explicit identity stub makes the convention obvious: a
   future "Robot B" backend creates its own folder with its own
   ``coordinate_frames.py``, full stop.
2. It gives mock tests a place to inject deliberate noise or fake
   calibration errors when we want to exercise the transform code
   paths without hardware. Today none of the helpers do that; they're
   pure pass-throughs.

Architectural rule (``communicator_refactor.md`` §5.1): no
``lab_automation``, no :mod:`lab_communicator.base`, no real-backend
imports. Pure stdlib + ``shared/``.
"""

from __future__ import annotations

from typing import Tuple


def lab_table_xy_to_robot_xy(x_lab: float, y_lab: float) -> Tuple[float, float]:
    """Identity in mock mode -- mock state IS in the lab frame."""
    return float(x_lab), float(y_lab)


def robot_table_xy_to_lab_xy(x_robot: float, y_robot: float) -> Tuple[float, float]:
    """Identity (inverse of :func:`lab_table_xy_to_robot_xy`)."""
    return float(x_robot), float(y_robot)


def lab_rotation_to_robot_yaw(theta_lab: float) -> float:
    """Identity in mock mode."""
    return float(theta_lab)


def robot_yaw_to_lab_rotation(yaw_robot: float) -> float:
    """Identity in mock mode."""
    return float(yaw_robot)


def z_lab_to_robot(z_lab: float, height_mm: float) -> float:  # noqa: ARG001 (unused on purpose)
    """Identity in mock mode -- mock has no robot Z frame."""
    return float(z_lab)


def z_robot_to_lab(z_robot: float, height_mm: float) -> float:  # noqa: ARG001
    """Identity in mock mode."""
    return float(z_robot)
