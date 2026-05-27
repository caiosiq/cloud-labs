"""Real-lab coordinate-frame calibration and transforms.

This module is the single home for everything that distinguishes the
*real* optical lab's geometry from cloud-labs / UI geometry: the lab vs
robot table XY rotation, the lab vs robot Z origin, the lab vs robot
yaw convention, and the safety bounds we apply to user-supplied poses.

A future "Robot B" backend would have its **own** ``coordinate_frames.py``
inside its own backend folder with different constants and possibly
different transform shapes -- exactly the model that motivated splitting
the communicator into per-backend folders (see ``communicator_refactor.md``
§3.2). The mock backend has its own (``mock/coordinate_frames.py``) which
is an identity transform: mock state is already in the lab frame, so no
conversion is needed before "talking to the robot".

What lives here:

* Calibration **constants** (``LAB_ROBOT_TABLE_ROTATION_RAD``,
  ``TABLE_Z0_ROBOT_MM``, ``BREADBOARD_HEIGHT_ABOVE_FLOOR_MM``,
  ``GRASP_OFFSET_MM``, ``DEFAULT_COMPONENT_HEIGHT_MM``,
  ``MAX_SAFE_HOVER_Z_LAB_MM``). All env-overridable via
  :func:`lab_communicator.shared.util.env_float`.
* XY transforms ``lab_table_xy_to_robot_xy`` / ``robot_table_xy_to_lab_xy``.
* Yaw / rotation transforms ``lab_rotation_to_robot_yaw`` /
  ``robot_yaw_to_lab_rotation`` (identity today; one-line edit when
  calibrated).
* Z transforms ``z_lab_to_robot`` / ``z_robot_to_lab`` as pure functions
  taking the per-component ``height_mm`` explicitly. The communicator's
  bound methods (``_z_lab_to_robot`` / ``_z_robot_to_lab``) are thin
  wrappers that look up ``height_mm`` from the catalog and delegate here.

Architectural rule (``communicator_refactor.md`` §5.1): this module
must NOT import ``lab_automation`` or :mod:`lab_communicator.base` or
the ``mock`` backend. It depends only on stdlib + ``shared/``.
"""

from __future__ import annotations

import math
from typing import Tuple

from lab_communicator.shared.util import env_float


# ---------------------------------------------------------------------------
# Lab vs robot table XY (see coordinate_rotation.md in repo root)
# ---------------------------------------------------------------------------
# UI and overhead-camera geometry use "lab" axes. The robot table frame
# is rotated by a small angle. Calibrated: motion that is a straight
# line in the lab (e.g. +100 mm along lab Y) decomposes in robot
# coordinates as approximately Δx_robot = +2.5 mm and Δy_robot = +100 mm
# (same sign convention as your robot axes). That implies
# sin(θ) ≈ −2.5/100 for the lab→robot rotation below
# → θ = atan2(-2.5, 100). Refine by changing this constant after
# re-measurement.
LAB_ROBOT_TABLE_ROTATION_RAD: float = math.atan2(-2.66, 100.0)


def lab_table_xy_to_robot_xy(x_lab: float, y_lab: float) -> Tuple[float, float]:
    """Map UI / lab table mm to robot controller table mm before place/move calls."""
    t = LAB_ROBOT_TABLE_ROTATION_RAD
    c, s = math.cos(t), math.sin(t)
    return (c * x_lab - s * y_lab, s * x_lab + c * y_lab)


def robot_table_xy_to_lab_xy(x_robot: float, y_robot: float) -> Tuple[float, float]:
    """Map robot-reported table mm to lab / UI mm (inverse of :func:`lab_table_xy_to_robot_xy`)."""
    t = LAB_ROBOT_TABLE_ROTATION_RAD
    c, s = math.cos(t), math.sin(t)
    return (c * x_robot + s * y_robot, -s * x_robot + c * y_robot)


# ---------------------------------------------------------------------------
# Lab vs robot yaw / rotation (see fixing.md: "Coordinate convention recap")
# ---------------------------------------------------------------------------
# UI / cloud-labs state stores rotation in the lab frame as ``theta_lab``
# (deg). lab_automation expects robot-frame yaw as ``yaw_robot`` (deg).
# In general the two differ by the same calibration that relates the lab
# and robot table XY frames (plus possibly a sign flip). Today, by
# convention and empirical evidence, the transform is identity:
# ``yaw_robot = theta_lab``. These helpers exist so every cross-wall
# rotation write goes through one place -- when the lab calibrates a
# real offset, it's a one-line fix here, not a hunt across every
# primitive.
#
# Known convention disagreement (tracked in fixing.md §3.1 / Stage A4):
# ``RealLabCommunicator.move_component`` today dispatches
# ``angle=[-180, 0, -rot]`` -- the ``-rot`` is an inline, ad-hoc
# negation that has been empirically correct for this hardware setup.
# We have NOT yet folded that negation into ``lab_rotation_to_robot_yaw``
# because we can't physically test whether ``set_lab_state`` and
# ``hover_component`` should also negate (they use the identity today).
# Resolving this is deferred until the next time someone is in front of
# the robot and can verify experimentally. For now:
#   - ``set_lab_state`` and ``hover_component`` route through this
#     helper (identity), matching their current behavior.
#   - ``move_component`` keeps its inline ``-rot`` with a comment
#     pointing back here. When calibration is done, either the negation
#     moves into this helper (and ``move_component`` loses the ``-``)
#     or it stays out for a documented reason.
def lab_rotation_to_robot_yaw(theta_lab: float) -> float:
    """Map UI / lab table rotation (deg) to robot-frame yaw (deg).

    Identity today. See module-level comment above for the convention
    and the known open item (``move_component``'s inline ``-rot``).
    """
    return float(theta_lab)


def robot_yaw_to_lab_rotation(yaw_robot: float) -> float:
    """Inverse of :func:`lab_rotation_to_robot_yaw`. Identity today."""
    return float(yaw_robot)


# ---------------------------------------------------------------------------
# Lab vs robot Z (see new_primitives.md: "Z / coordinate convention")
# ---------------------------------------------------------------------------
# Cloud-labs, the UI, the HTTP API, and lab_model all speak ``z_lab``:
#     Height of a component's *base* above the **lab floor** (mm).
#     z_lab = 0   -> part base is on the floor.
#     z_lab = 215 -> part base is on the breadboard surface (May 2026 bench:
#                    breadboard ~21.5 cm above floor).
#     z_lab = 245 -> typical post-pick hover (~30 mm above breadboard).
#
# lab_automation speaks ``z_robot``: the robot-frame z command of
# whatever reference point the robot controller uses (flange / gripper
# tip). We absorb the flange-to-gripper-tip offset into
# ``TABLE_Z0_ROBOT_MM`` below so that ``TABLE_Z0_ROBOT_MM`` is always
# the robot z reading when the *gripper fingers* (empty) are touching
# the breadboard.
#
# RealLabCommunicator is the ONLY place these two frames meet. Every
# outgoing call to lab_automation forward-transforms z_lab -> z_robot;
# every incoming read inverse-transforms z_robot -> z_lab.
#
# Transform (outgoing):
#     z_robot = TABLE_Z0_ROBOT_MM + height_mm - GRASP_OFFSET_MM
#               + z_lab - BREADBOARD_HEIGHT_ABOVE_FLOOR_MM
# Inverse (incoming):
#     z_lab   = z_robot - TABLE_Z0_ROBOT_MM - height_mm
#               + GRASP_OFFSET_MM + BREADBOARD_HEIGHT_ABOVE_FLOOR_MM
#
# May 2026 calibration (measured on bench):
#   - Beam-splitter class optics ~210 mm tall; gripper camera (tag_22)
#     ~175 mm.
#   - With the legacy transform (breadboard-referenced z_lab, height_mm
#     = 60), commanding z_lab=0 placed the base ~215 mm above the floor.
#   - ``BREADBOARD_HEIGHT_ABOVE_FLOOR_MM`` re-zeroes z_lab on the floor
#     while ``TABLE_Z0_ROBOT_MM`` + catalog ``height_mm`` keep the same
#     robot commands for breadboard contact and post-pick hover.
#
# The four inputs are each owned by exactly one thing so they don't
# drift:
# - ``TABLE_Z0_ROBOT_MM`` (per-setup calibration): robot-frame z reading
#   when the empty gripper fingers touch the **breadboard** surface,
#   adjusted together with ``height_mm`` and
#   ``BREADBOARD_HEIGHT_ABOVE_FLOOR_MM`` so breadboard contact still
#   commands the same ``z_robot`` as before recalibration (~560 mm with
#   the May 2026 defaults). Override with env ``TABLE_Z0_ROBOT_MM``.
# - ``BREADBOARD_HEIGHT_ABOVE_FLOOR_MM`` (bench geometry): vertical
#   distance from lab floor to breadboard surface (mm). Override with env
#   ``BREADBOARD_HEIGHT_ABOVE_FLOOR_MM``.
# - ``GRASP_OFFSET_MM`` (gripper design constant): distance from the
#   *top* of the component housing DOWN to the point where the gripper
#   fingers close. 0 means "closes at the very top of the housing"; a
#   positive N means "closes N mm below the top". Override with env
#   ``GRASP_OFFSET_MM``.
# - ``height_mm`` (per-component catalog): total physical height of the
#   part, base-to-top, in mm. Lives in **component_library.json** under
#   ``LAB_VIEW_PATH`` and is looked up by the communicator before calling
#   ``z_lab_to_robot`` here.
#
# Plus two cloud-labs-only safety knobs:
# - ``MAX_SAFE_HOVER_Z_LAB_MM``: hard upper bound on user-requested
#   HOVER z (z_lab) so a runaway HTTP payload cannot drive the gripper
#   to the ceiling.
# - ``DEFAULT_COMPONENT_HEIGHT_MM``: fallback when a catalog entry is
#   missing ``height_mm`` (logs a warning). Used by the communicator's
#   ``_component_height_mm`` lookup, not by the transform itself.

TABLE_Z0_ROBOT_MM: float = env_float(
    "TABLE_Z0_ROBOT_MM", 350.0, log_prefix="[REAL LAB]"
)
BREADBOARD_HEIGHT_ABOVE_FLOOR_MM: float = env_float(
    "BREADBOARD_HEIGHT_ABOVE_FLOOR_MM", 215.0, log_prefix="[REAL LAB]"
)
GRASP_OFFSET_MM: float = env_float(
    "GRASP_OFFSET_MM", 0.0, log_prefix="[REAL LAB]"
)
DEFAULT_COMPONENT_HEIGHT_MM: float = env_float(
    "DEFAULT_COMPONENT_HEIGHT_MM", 210.0, log_prefix="[REAL LAB]"
)
MAX_SAFE_HOVER_Z_LAB_MM: float = env_float(
    "MAX_SAFE_HOVER_Z_LAB_MM", 500.0, log_prefix="[REAL LAB]"
)


def z_lab_to_robot(z_lab: float, height_mm: float) -> float:
    """Forward transform: cloud-labs z_lab -> lab_automation z_robot.

    Pure function -- ``height_mm`` is passed in explicitly (the
    communicator looks it up from the catalog before calling here). See
    module-level comment for the formula and the per-input ownership.
    """
    return (
        TABLE_Z0_ROBOT_MM
        + float(height_mm)
        - GRASP_OFFSET_MM
        + float(z_lab)
        - BREADBOARD_HEIGHT_ABOVE_FLOOR_MM
    )


def z_robot_to_lab(z_robot: float, height_mm: float) -> float:
    """Inverse transform: lab_automation z_robot -> cloud-labs z_lab.

    Pure function -- ``height_mm`` is passed in explicitly. Inverse of
    :func:`z_lab_to_robot` for the same ``height_mm``.
    """
    return (
        float(z_robot)
        - TABLE_Z0_ROBOT_MM
        - float(height_mm)
        + GRASP_OFFSET_MM
        + BREADBOARD_HEIGHT_ABOVE_FLOOR_MM
    )
