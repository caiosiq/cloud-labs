"""
One-time migration: legacy component shape (state, pose, intent, metadata)
-> tunables + measurables (see refactor.md).

Usage (from repo root):
  python scripts/migrate_to_tunables.py schemas/mock_lab_state.json
  python scripts/migrate_to_tunables.py backend/lab_communicator/real/lab_view/default/states/*.json
  python scripts/migrate_to_tunables.py backend/lab_communicator/mock/lab_view/recipes/*.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict


def migrate_component(c: Dict[str, Any]) -> Dict[str, Any]:
    if "tunables" in c and "measurables" in c:
        return c
    pose = dict(c.get("pose") or {})
    intent = dict(c.get("intent") or {})
    md = dict(c.get("metadata") or {})
    st = c.get("state", "PLACED")
    nominal = intent.get("nominal_pose")
    if not isinstance(nominal, dict):
        nominal = dict(pose) if pose else {"x": 0.0, "y": 0.0, "rotation": 0.0}
    slot = md.get("storage_slot")
    if isinstance(slot, dict) and "i" in slot and "j" in slot:
        slot = {"i": int(slot["i"]), "j": int(slot["j"])}
    else:
        slot = None

    if st == "STORED":
        presence = "storage"
        in_storage = True
    elif st == "INVENTORY":
        presence = "off_table"
        in_storage = False
        slot = None
    else:
        presence = "breadboard"
        in_storage = False
        slot = None

    strat = intent.get("placement_strategy") or "MANUAL"
    if not isinstance(strat, str):
        strat = "MANUAL"

    meas: Dict[str, Any] = {
        "pose": dict(pose),
        "last_optimization_score": md.get("last_optimization_score"),
        "last_optimized_pose": intent.get("last_optimized_pose"),
        "camera_image": None,
    }
    if meas["last_optimization_score"] is None and intent.get("is_optimized"):
        meas["last_optimization_score"] = 1.0

    nm = intent.get("nominal_motor_rotations") or {}
    if isinstance(nm, dict):
        nominal_motor = {str(k): float(v) for k, v in nm.items()}
    else:
        nominal_motor = {}

    tun: Dict[str, Any] = {
        "presence": presence,
        "nominal_pose": dict(nominal),
        "nominal_motor_positions": nominal_motor,
        "storage": {"in_storage": in_storage, "slot": slot},
        "placement": {"mode": strat.upper() if strat else "MANUAL"},
    }

    return {
        "id": c.get("id"),
        "type": c.get("type"),
        "tunables": tun,
        "measurables": meas,
    }


def migrate_obj(data: Any) -> Any:
    if isinstance(data, dict):
        if "components" in data and isinstance(data["components"], dict):
            out = dict(data)
            out["components"] = {k: migrate_component(dict(v)) for k, v in data["components"].items()}
            return out
        return {k: migrate_obj(v) for k, v in data.items()}
    if isinstance(data, list):
        return [migrate_obj(x) for x in data]
    return data


def main() -> None:
    paths = [Path(p) for p in sys.argv[1:]]
    if not paths:
        print("Usage: python migrate_to_tunables.py <file.json> [...]", file=sys.stderr)
        sys.exit(1)
    for path in paths:
        if not path.is_file():
            print(f"Skip (not a file): {path}", file=sys.stderr)
            continue
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        new_data = migrate_obj(data)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(new_data, f, indent=2)
            f.write("\n")
        print(f"Migrated {path}")


if __name__ == "__main__":
    main()
