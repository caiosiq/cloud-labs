"""Real-backend gripper introspection + boot-time HOLDING reconcile.

Two responsibilities, both real-only:

1. :func:`get_gripper_status` -- probe the lab_automation
   ``OpticalExperiment`` for a gripper-closed signal. Multiple probe
   shapes are tried in priority order so the helper survives different
   versions of ``lab_automation`` (some expose ``get_gripper_status``,
   others ``is_gripper_closed``, others a plain ``robot.gripper_closed``
   attribute). Returns a uniform dict regardless of which probe fired.

2. :func:`reconcile_holding_on_boot` -- if the gripper is reporting
   closed at startup but the in-memory snapshot does NOT already
   declare a confirmed HOLDING state, force HOLDING_UNCONFIRMED (with
   ``requires_operator_confirm: true``). The UI then blocks all cross-
   part commands until ``CONFIRM_HOLDING_TAG`` arrives. Mirrors the
   contract documented in ``new_primitives.md`` §6.3.

Both helpers are written as free functions taking the ``RealLabCommunicator``
instance explicitly. The class keeps a thin ``def`` wrapper so external
call sites (Cloud-Labs HTTP layer, ``main.py`` startup) keep their
existing API. Phase 2 may collapse the wrappers into base.py orchestrators
calling these as ``_do_*`` hooks (see ``communicator_refactor.md`` §6).

Architectural rule (``communicator_refactor.md`` §5.1): this module is
real-only -- it can import ``lab_automation``-shaped objects via duck
typing on ``communicator.experiment`` but it must NOT import
:mod:`lab_communicator.base`, the mock backend, or ``shared/`` modules
that violate the layered rules. ``lab_model`` is fine.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict

from lab_model.domain.holding import (
    DEFAULT_HOVER_Z_MM,
    SYSTEM_STATUS_HOLDING,
    held_tag,
    set_holding,
)


if TYPE_CHECKING:
    from lab_communicator.real.communicator import RealLabCommunicator


def get_gripper_status(communicator: "RealLabCommunicator") -> Dict[str, Any]:
    """Real-lab gripper introspection (see module docstring).

    Returns the uniform shape ``{"closed": bool, "confidence": float | None,
    "source": str}``. ``source`` is informational -- it tags which probe
    in the priority list returned the signal so debugging logs can tell
    "we never had a sensor" from "we had one and it said open".

    Probe order (cheap + side-effect-free calls first):

    1. ``experiment.get_gripper_status()``  (preferred when available).
    2. ``experiment.robot.get_gripper_status()``.
    3. ``experiment.is_gripper_closed()`` (bool accessor).
    4. ``experiment.robot.gripper_closed`` (plain attribute).

    When none of the probes return a value we report
    ``closed=False, source="unavailable..."`` -- the safest default for
    boot reconcile (no spurious HOLDING_UNCONFIRMED).
    """
    experiment = communicator.experiment
    probes = [
        (
            "experiment.get_gripper_status",
            getattr(experiment, "get_gripper_status", None),
        ),
        (
            "experiment.robot.get_gripper_status",
            getattr(getattr(experiment, "robot", None), "get_gripper_status", None),
        ),
        (
            "experiment.is_gripper_closed",
            getattr(experiment, "is_gripper_closed", None),
        ),
    ]
    for source, fn in probes:
        if callable(fn):
            try:
                res = fn()
            except Exception as e:
                print(f"[REAL LAB] {source}() raised {e!r}; skipping")
                continue
            if isinstance(res, dict):
                return {
                    "closed": bool(res.get("closed", False)),
                    "confidence": res.get("confidence"),
                    "source": source,
                }
            if isinstance(res, bool):
                return {"closed": res, "confidence": None, "source": source}

    # Plain attribute fallback.
    robot = getattr(experiment, "robot", None)
    attr_val = getattr(robot, "gripper_closed", None) if robot is not None else None
    if isinstance(attr_val, bool):
        return {
            "closed": attr_val,
            "confidence": None,
            "source": "experiment.robot.gripper_closed",
        }

    return {
        "closed": False,
        "confidence": None,
        "source": (
            "unavailable (no probe returned a value; check "
            "OpticalExperiment.get_gripper_status / robot)"
        ),
    }


def reconcile_holding_on_boot(communicator: "RealLabCommunicator") -> None:
    """Boot-time HOLDING reconciliation (see ``new_primitives.md`` §6.3).

    Called once during ``RealLabCommunicator.__init__`` (after ``current_state``
    has been hydrated). If the gripper reports CLOSED but the snapshot is
    not already a confirmed HOLDING, force ``system_status = "HOLDING"``
    with ``holding.requires_operator_confirm = true``. The UI must then
    block all cross-part commands until the operator sends
    ``CONFIRM_HOLDING_TAG``.

    A best-effort held pose (table center, default safe Z) is written so
    downstream code doesn't trip on a missing ``holding.nominal_pose``;
    the actual held tag is left as ``None`` because we explicitly do NOT
    guess it here -- vision-based identification is deferred to a future
    Stage. This function never raises: any probe failure logs and
    returns without mutating state.
    """
    try:
        gripper = get_gripper_status(communicator)
    except Exception as e:
        print(
            f"[REAL LAB] get_gripper_status raised {e!r}; "
            f"skipping holding reconcile"
        )
        return
    if not bool((gripper or {}).get("closed")):
        return

    with communicator._state_lock:
        status = communicator.current_state.get("system_status")
        if status == SYSTEM_STATUS_HOLDING and held_tag(communicator.current_state):
            # Already a confirmed HOLDING in the snapshot -- trust it.
            return

        print(
            "[REAL LAB] Gripper reports CLOSED on boot but no confirmed "
            "HOLDING in snapshot -> forcing HOLDING_UNCONFIRMED. "
            "UI must prompt operator to confirm which tag is held."
        )
        set_holding(
            communicator.current_state,
            tag_id=None,
            x=0.0,
            y=0.0,
            rotation=0.0,
            z=DEFAULT_HOVER_Z_MM,
            requires_operator_confirm_flag=True,
        )
        communicator.current_state["last_updated"] = datetime.now().isoformat()
