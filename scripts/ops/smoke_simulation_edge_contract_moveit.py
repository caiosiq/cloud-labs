#!/usr/bin/env python3
"""Smoke-test Edge Contract /execute with simulation_edge MuJoCo + MoveIt."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _ensure_paths() -> None:
    root = _repo_root()
    for rel in (
        "simulation_edge/cloudlabs_edge",
        "backend",
        "simulation_edge/src",
        "packages/cloudlabs_edge_dev/src",
    ):
        path = str(root / rel)
        if path not in sys.path:
            sys.path.insert(0, path)


def _configure_env(args: argparse.Namespace) -> None:
    os.environ.setdefault("SIMULATION_EDGE_MUJOCO", "1")
    os.environ.setdefault("SIMULATION_EDGE_PLANNER", "moveit")
    os.environ.setdefault("CLOUDLAB_MOVEIT_URL", "http://127.0.0.1:8765")
    os.environ.setdefault("CLOUDLAB_MUJOCO_VIEWER", "0")
    os.environ.setdefault("CLOUDLAB_MUJOCO_REALTIME", "0")
    os.environ.setdefault("CLOUDLAB_SIM_PROFILE", args.profile)
    os.environ.setdefault("CLOUDLAB_MOVEIT_MAX_VELOCITY_SCALE", "0.35")
    os.environ.setdefault("CLOUDLAB_MOVEIT_MAX_ACCELERATION_SCALE", "0.35")
    os.environ.setdefault("CLOUDLAB_MOVEIT_TRAJECTORY_TIME_SCALE", "0.5")
    os.environ.setdefault("CLOUDLAB_MOVEIT_PREPICK_FEASIBILITY_SEEDS", "10")
    os.environ.setdefault("CLOUDLAB_MOVEIT_PREPICK_FEASIBILITY_TIMEOUT_S", "150")


def _route_endpoint(app, path: str, method: str):
    wanted = method.upper()
    for route in app.routes:
        if getattr(route, "path", None) != path:
            continue
        methods = getattr(route, "methods", None) or set()
        if wanted in methods:
            return route.endpoint
    raise RuntimeError(f"route {method} {path} not found")


async def _maybe_await(value):
    if hasattr(value, "__await__"):
        return await value
    return value


async def _run_execute(app, payload: dict) -> tuple[int, dict]:
    endpoint = _route_endpoint(app, "/execute", "POST")
    response = await _maybe_await(endpoint(payload))
    status_code = int(getattr(response, "status_code", 200))
    body = getattr(response, "body", None)
    if body is not None:
        return status_code, json.loads(body.decode("utf-8"))
    return status_code, dict(response)


async def _run_get(app, path: str) -> dict:
    endpoint = _route_endpoint(app, path, "GET")
    response = await _maybe_await(endpoint())
    body = getattr(response, "body", None)
    if body is not None:
        return json.loads(body.decode("utf-8"))
    return dict(response)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="optical_housings")
    parser.add_argument("--tag", default="tag_21")
    parser.add_argument("--x-mm", type=float, default=420.0)
    parser.add_argument("--y-mm", type=float, default=260.0)
    parser.add_argument("--rotation-deg", type=float, default=-90.0)
    args = parser.parse_args()

    _configure_env(args)
    _ensure_paths()

    from adapters import context
    from server.app import build_default_app

    app = build_default_app()
    payload = {
        "primitive": "MOVE_COMPONENT",
        "args": {
            "tag_id": args.tag,
            "x": args.x_mm,
            "y": args.y_mm,
            "rotation": args.rotation_deg,
        },
    }
    try:
        health = asyncio.run(_run_get(app, "/health"))
        status_code, body = asyncio.run(_run_execute(app, payload))
        state = asyncio.run(_run_get(app, "/lab-state"))
        ok = status_code == 200 and str(body.get("status") or "").lower() == "completed"
        result = {
            "ok": ok,
            "health": health,
            "status_code": status_code,
            "execute": body,
            "simulator": state.get("simulator"),
            "last_runtime_error": state.get("last_runtime_error"),
        }
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
        return 0 if ok else 1
    finally:
        lab = context.get_lab()
        if hasattr(lab, "shutdown_lab_processes"):
            lab.shutdown_lab_processes()


if __name__ == "__main__":
    raise SystemExit(main())
