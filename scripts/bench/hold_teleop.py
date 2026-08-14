#!/usr/bin/env python3
"""Interactive hold + teleop smoke for a table tag (default ``tag_9``, real backend).

Walk through the same in-air / TeleOp flow Twin uses:

  table MOVE → PICK → (optional CONFIRM) → HOVER → TeleOp goto → PLACE

Requires::

    # coordinator up; real edge reachable
    python scripts/bench/hold_teleop.py
    python scripts/bench/hold_teleop.py --tag tag_9 --backend real.default
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "packages" / "cloudlabs" / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from cloudlabs import configure_logging, connect

_LOG = logging.getLogger("bench.hold_teleop")

DEFAULT_BACKEND = "real.default"
DEFAULT_TAG = "tag_9"
DEFAULT_HOVER_Z_MM = 40.0


def _prompt(msg: str, default: Optional[str] = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    raw = input(f"{msg}{suffix}: ").strip()
    if not raw and default is not None:
        return default
    return raw


def _prompt_float(msg: str, default: Optional[float] = None) -> float:
    while True:
        raw = _prompt(msg, None if default is None else str(default))
        try:
            return float(raw)
        except ValueError:
            print("  need a number")


def _pp(obj: Any) -> None:
    print(json.dumps(obj, indent=2, default=str))


def _cmd(lab: Any, action: str, tag: str, parameters: Optional[Dict[str, Any]] = None) -> Any:
    body = {
        "action": action,
        "target_id": tag,
        "parameters": parameters or {},
    }
    print(f"  → {action} {tag} params={parameters or {}}")
    out = lab._post_command(body)  # noqa: SLF001 — no first-class SDK helpers yet
    _pp(out)
    return out


def _component(lab: Any, tag: str) -> Dict[str, Any]:
    state = lab.get_lab_state()
    comps = state.get("components") or {}
    entry = comps.get(tag)
    return entry if isinstance(entry, dict) else {}


def _holding(lab: Any) -> Dict[str, Any]:
    state = lab.get_lab_state()
    h = state.get("holding")
    return h if isinstance(h, dict) else {}


def _nominal_pose(lab: Any, tag: str) -> Dict[str, float]:
    tun = ((_component(lab, tag).get("statecontrol") or {}).get("tunables") or {})
    pose = tun.get("nominal_pose") or {}
    return {
        "x": float(pose.get("x", 0.0)),
        "y": float(pose.get("y", 0.0)),
        "rotation": float(pose.get("rotation", 0.0)),
    }


def _holding_z(lab: Any, default: float = DEFAULT_HOVER_Z_MM) -> float:
    h = _holding(lab)
    pose = h.get("nominal_pose") if isinstance(h.get("nominal_pose"), dict) else {}
    z = pose.get("z")
    try:
        return float(z) if z is not None else default
    except (TypeError, ValueError):
        return default


def cmd_status(lab: Any, tag: str) -> None:
    state = lab.get_lab_state()
    print(f"system_status={state.get('system_status')!r}")
    entry = _component(lab, tag)
    if not entry:
        print(f"  {tag} not in lab_state.components")
        return
    tun = ((entry.get("statecontrol") or {}).get("tunables") or {})
    tel = entry.get("telemetry") or {}
    teleop = tel.get("teleop") or {}
    print(f"  {tag}")
    print(f"    presence={tun.get('presence')!r}")
    print(f"    nominal_pose={tun.get('nominal_pose')}")
    print(f"    placement={tun.get('placement')}")
    print(f"    teleop active={teleop.get('active')} ready={teleop.get('ready')}")
    h = _holding(lab)
    print(
        f"  holding tag_id={h.get('tag_id')!r} "
        f"requires_confirm={h.get('requires_operator_confirm')!r} "
        f"pose={h.get('nominal_pose')}"
    )


def cmd_move_table(lab: Any, tag: str) -> None:
    pose = _nominal_pose(lab, tag)
    print(f"  current pose={pose}")
    print("  blank axis = leave unchanged")
    xs = _prompt("x (mm)", "")
    ys = _prompt("y (mm)", "")
    rs = _prompt("rotation (deg)", "")
    kwargs: Dict[str, float] = {}
    if xs:
        kwargs["x"] = float(xs)
    if ys:
        kwargs["y"] = float(ys)
    if rs:
        kwargs["rotation"] = float(rs)
    if not kwargs:
        print("  nothing to move")
        return
    lab.components[tag].move(**kwargs).wait_until_idle()
    print("  moved + idle")
    cmd_status(lab, tag)


def cmd_pick(lab: Any, tag: str) -> None:
    _cmd(lab, "PICK_COMPONENT", tag, {})
    try:
        lab.wait_until_idle(timeout_s=120.0)
    except Exception as exc:  # noqa: BLE001
        print(f"  wait_until_idle: {exc}")
    cmd_status(lab, tag)


def cmd_confirm(lab: Any, tag: str) -> None:
    _cmd(lab, "CONFIRM_HOLDING_TAG", tag, {})
    cmd_status(lab, tag)


def cmd_hover(lab: Any, tag: str) -> None:
    pose = _nominal_pose(lab, tag)
    hpose = _holding(lab).get("nominal_pose")
    if isinstance(hpose, dict) and hpose.get("x") is not None:
        pose = {
            "x": float(hpose.get("x", pose["x"])),
            "y": float(hpose.get("y", pose["y"])),
            "rotation": float(hpose.get("rotation", pose["rotation"])),
        }
    z0 = _holding_z(lab)
    x = _prompt_float("hover target_x (mm)", pose["x"])
    y = _prompt_float("hover target_y (mm)", pose["y"])
    r = _prompt_float("hover rotation (deg)", pose["rotation"])
    z = _prompt_float("hover z (mm)", z0)
    _cmd(
        lab,
        "HOVER",
        tag,
        {"target_x": x, "target_y": y, "rotation": r, "z": z},
    )
    try:
        lab.wait_until_idle(timeout_s=120.0)
    except Exception as exc:  # noqa: BLE001
        print(f"  wait_until_idle: {exc}")
    cmd_status(lab, tag)


def cmd_place(lab: Any, tag: str) -> None:
    pose = _nominal_pose(lab, tag)
    hpose = _holding(lab).get("nominal_pose")
    if isinstance(hpose, dict) and hpose.get("x") is not None:
        pose = {
            "x": float(hpose.get("x", pose["x"])),
            "y": float(hpose.get("y", pose["y"])),
            "rotation": float(hpose.get("rotation", pose["rotation"])),
        }
    x = _prompt_float("place target_x (mm)", pose["x"])
    y = _prompt_float("place target_y (mm)", pose["y"])
    r = _prompt_float("place rotation (deg)", pose["rotation"])
    _cmd(
        lab,
        "PLACE_FROM_HOVER",
        tag,
        {"target_x": x, "target_y": y, "rotation": r},
    )
    try:
        lab.wait_until_idle(timeout_s=120.0)
    except Exception as exc:  # noqa: BLE001
        print(f"  wait_until_idle: {exc}")
    cmd_status(lab, tag)


def cmd_teleop_start(lab: Any, tag: str) -> None:
    _pp(lab.start_teleop(tag))
    cmd_status(lab, tag)


def cmd_teleop_goto(lab: Any, tag: str) -> None:
    pose = _nominal_pose(lab, tag)
    hpose = _holding(lab).get("nominal_pose")
    if isinstance(hpose, dict) and hpose.get("x") is not None:
        pose = {
            "x": float(hpose.get("x", pose["x"])),
            "y": float(hpose.get("y", pose["y"])),
            "rotation": float(hpose.get("rotation", pose["rotation"])),
        }
    x = _prompt_float("teleop target x (mm)", pose["x"])
    y = _prompt_float("teleop target y (mm)", pose["y"])
    r = _prompt_float("teleop target rotation (deg)", pose["rotation"])
    # Held TeleOp often also cares about z — include if user wants.
    include_z = _prompt("include z in target_pose? (y/n)", "y").lower() not in (
        "n",
        "no",
        "0",
    )
    target: Dict[str, float] = {"x": x, "y": y, "rotation": r}
    if include_z:
        target["z"] = _prompt_float("teleop target z (mm)", _holding_z(lab))
    _pp(lab.teleop_goto(tag, target_pose=target))
    cmd_status(lab, tag)


def cmd_teleop_end(lab: Any, tag: str) -> None:
    _pp(lab.end_teleop(tag))
    cmd_status(lab, tag)


def cmd_guided(lab: Any, tag: str) -> None:
    """Prompted happy-path: move → pick → hover → teleop nudge → place."""
    print(
        "\nGuided sequence for {tag}.\n"
        "  You confirm each step. Abort with Ctrl+C.\n".format(tag=tag)
    )
    if _prompt("1) show status?", "y").lower() not in ("n", "no"):
        cmd_status(lab, tag)
    if _prompt("2) table MOVE?", "y").lower() not in ("n", "no"):
        cmd_move_table(lab, tag)
    if _prompt("3) PICK_COMPONENT?", "y").lower() not in ("n", "no"):
        cmd_pick(lab, tag)
    h = _holding(lab)
    if h.get("requires_operator_confirm"):
        if _prompt("4) CONFIRM_HOLDING_TAG?", "y").lower() not in ("n", "no"):
            cmd_confirm(lab, tag)
    if _prompt("5) HOVER?", "y").lower() not in ("n", "no"):
        cmd_hover(lab, tag)
    if _prompt("6) START_TELEOP + goto?", "y").lower() not in ("n", "no"):
        cmd_teleop_start(lab, tag)
        cmd_teleop_goto(lab, tag)
        if _prompt("   END_TELEOP now?", "y").lower() not in ("n", "no"):
            cmd_teleop_end(lab, tag)
    if _prompt("7) PLACE_FROM_HOVER?", "y").lower() not in ("n", "no"):
        # Ensure teleop is off before place (Twin blocks some writers while teleop active).
        tel = ((_component(lab, tag).get("telemetry") or {}).get("teleop") or {})
        if tel.get("active"):
            print("  ending teleop before place…")
            cmd_teleop_end(lab, tag)
        cmd_place(lab, tag)
    print("  guided sequence done")
    cmd_status(lab, tag)


def _menu(tag: str) -> None:
    print(
        f"""
======== hold + teleop  ({tag}) ========
  1  status (presence / holding / teleop)
  2  MOVE on table (pose x/y/rot)
  3  PICK_COMPONENT
  4  CONFIRM_HOLDING_TAG
  5  HOVER (in air, needs z)
  6  PLACE_FROM_HOVER
  7  START_TELEOP
  8  TELEOP_GOTO
  9  END_TELEOP
  g  guided sequence (move→pick→hover→teleop→place)
  q  quit / release lease
========================================
""".rstrip()
    )


def run_menu(lab: Any, tag: str) -> int:
    print(f"lease_id={lab.lease_id}  backend={lab.backend_id}  holder={lab.holder}")
    print(f"focus tag={tag}")
    while True:
        _menu(tag)
        choice = input("choice> ").strip().lower()
        try:
            if choice in ("q", "quit", "exit"):
                return 0
            if choice == "1":
                cmd_status(lab, tag)
            elif choice == "2":
                cmd_move_table(lab, tag)
            elif choice == "3":
                cmd_pick(lab, tag)
            elif choice == "4":
                cmd_confirm(lab, tag)
            elif choice == "5":
                cmd_hover(lab, tag)
            elif choice == "6":
                cmd_place(lab, tag)
            elif choice == "7":
                cmd_teleop_start(lab, tag)
            elif choice == "8":
                cmd_teleop_goto(lab, tag)
            elif choice == "9":
                cmd_teleop_end(lab, tag)
            elif choice == "g":
                cmd_guided(lab, tag)
            else:
                print("  unknown choice")
        except KeyboardInterrupt:
            print("\n  (interrupted — back to menu)")
        except Exception as exc:  # noqa: BLE001
            _LOG.exception("action failed")
            print(f"  ERROR: {exc}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Hold + TeleOp smoke for a table tag (default tag_9 / real.default)",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--backend", default=DEFAULT_BACKEND)
    parser.add_argument("--tag", default=DEFAULT_TAG, help="component tag to exercise")
    parser.add_argument(
        "--once",
        choices=["status", "pick", "guided"],
        default=None,
        help="run one action then exit",
    )
    args = parser.parse_args(argv)

    configure_logging()
    base_url = args.base_url.rstrip("/")
    backend_id = (args.backend or DEFAULT_BACKEND).strip()
    tag = (args.tag or DEFAULT_TAG).strip()
    _LOG.info("connecting backend=%s tag=%s base_url=%s", backend_id, tag, base_url)

    with connect(backend_id, base_url=base_url, verbose=True) as lab:
        if args.once == "status":
            cmd_status(lab, tag)
            return 0
        if args.once == "pick":
            cmd_pick(lab, tag)
            return 0
        if args.once == "guided":
            cmd_guided(lab, tag)
            return 0
        return run_menu(lab, tag)


if __name__ == "__main__":
    sys.exit(main())
