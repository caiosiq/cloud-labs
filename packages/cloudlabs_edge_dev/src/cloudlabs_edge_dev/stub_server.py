"""Minimal Edge Contract v1 stub (FastAPI) for local conformance and scaffolding."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response, StreamingResponse

from cloudlabs_edge_dev import CONTRACT_VERSION

# Tiny JPEG SOI…EOI (enough for “JPEG-ish” conformance).
_STUB_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c"
    b"\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c"
    b"\x1c $,('/%++-14444\x1f'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01"
    b"\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b"
    b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00\x7f\xff\xd9"
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def default_capabilities(backend_id: str = "stub.default") -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "backend_id": backend_id,
        "features": {
            "torchscript_execution": False,
            "hardware_reconciliation": False,
            "hardware_triggered_latch": False,
            "lan_direct_streams": True,
        },
        "supported_primitives": [
            "START_LIVE_FEED",
            "END_LIVE_FEED",
            "START_TELEOP",
            "END_TELEOP",
            "TELEOP_JOG",
            "RECORD_MEASURABLES",
            "EVAL_KERNEL",
            "LOCALIZE_COMPONENTS",
        ],
        "measurables": {
            "tag_22.camera_image": {
                "analysis": {
                    "layout": "bgr_hwc_uint8",
                    "dtype": "uint8",
                    "shape": ["H", "W", 3],
                    "domain": "spatial",
                },
                "wire": {"encoding": "jpeg", "profiles": ["default"]},
                "live_channel": "tag_22.camera_image",
                "capture_latency_ms": 0,
            }
        },
        "telemetry_channels": {
            "tag_22.camera_image": {
                "transport": "jpeg_poll",
                "path": "/stream/tag_22/camera_image.jpg",
                "requires_primitive": "START_LIVE_FEED",
                "binds_measurable": "tag_22.camera_image",
                "default_fps": 10,
                "default_profile": "default",
            },
            "tag_22.camera_image.mjpeg": {
                "transport": "mjpeg_http",
                "path": "/stream/tag_22/camera_image.mjpg",
                "requires_primitive": "START_LIVE_FEED",
                "binds_measurable": "tag_22.camera_image",
                "default_fps": 10,
                "default_profile": "default",
            },
            "teleop": {
                "transport": "websocket",
                "path": "/ws/teleop",
                "requires_primitive": "START_TELEOP",
                "binds_tunables_live": True,
            },
        },
        "wire_profiles": {
            "default": {"scale": 1.0, "jpeg_quality": 80, "fps": 10}
        },
    }


def default_bench(backend_id: str = "stub.default") -> dict[str, Any]:
    return {
        "backend_id": backend_id,
        "layout": {
            "version": 1,
            "lab_bounds_mm": {
                "x_min": 0.0,
                "x_max": 450.0,
                "y_min": 0.0,
                "y_max": 300.0,
            },
            "danger_zone": {"radius_mm": 40.0, "padding_mm": 5.0},
            "storage": {"rule": "rect", "grid_nx": 4, "grid_ny": 2},
            "breadboard": {
                "grid_spacing_mm": 25.0,
                "origin_offset_mm": {"x": 0.0, "y": 0.0},
            },
        },
        "laser_lines": [],
    }


def _default_library(backend_id: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "backend_id": backend_id,
        "components": {
            "tag_22": {
                "id": "cam_gripper_1",
                "type": "OPTICAL_CAMERA",
                "tag_id": "tag_22",
                "name": "Gripper Camera 1",
                "size": {"width": 40, "height": 40},
                "parameters": {},
                "capabilities": {
                    "primitives": [
                        "RECORD_MEASURABLES",
                        "SET_EXPOSURE",
                        "START_LIVE_FEED",
                        "END_LIVE_FEED",
                        "LOCALIZE_COMPONENTS",
                    ],
                    "statecontrol": {"tunables": {}, "measurables": {}},
                    "telemetry": {},
                },
            }
        },
    }


def _default_inventory(backend_id: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "backend_id": backend_id,
        "entries": {
            "tag_22": {
                "placement": "table",
                "storage_slot": None,
                "localize": True,
            }
        },
    }


def create_app(
    backend_id: str = "stub.default",
    *,
    capabilities: dict[str, Any] | None = None,
    bench: dict[str, Any] | None = None,
    edge_root: Any | None = None,
) -> FastAPI:
    """Reference Edge Contract app.

    Optional ``capabilities`` / ``bench`` let a lab skeleton declare the full
    primitive list while this stub still completes the live-plane subset and
    refuses the rest as ``NOT_IMPLEMENTED``.

    When ``edge_root`` points at a ``cloudlabs_edge`` folder with
    ``data/library.json`` / ``data/inventory.json``, those are served on
    ``GET /library`` and ``GET /inventory``.
    """
    from pathlib import Path

    from cloudlabs_edge_dev.edge_data import (
        default_localize_tag_ids,
        load_inventory,
        load_library,
        stamp_backend,
    )

    app = FastAPI(title="cloudlabs-edge-stub", version=CONTRACT_VERSION)
    caps = dict(capabilities) if capabilities is not None else default_capabilities(backend_id)
    caps.setdefault("backend_id", backend_id)
    caps.setdefault("contract_version", CONTRACT_VERSION)
    bench_body = dict(bench) if bench is not None else default_bench(backend_id)
    bench_body.setdefault("backend_id", backend_id)

    root = Path(edge_root).resolve() if edge_root is not None else None
    library_body = _default_library(backend_id)
    inventory_body = _default_inventory(backend_id)
    if root is not None:
        try:
            library_body = stamp_backend(load_library(root), backend_id)
        except Exception:  # noqa: BLE001
            pass
        try:
            inventory_body = stamp_backend(load_inventory(root), backend_id)
        except Exception:  # noqa: BLE001
            pass

    state: dict[str, Any] = {
        "capabilities": caps,
        "bench": bench_body,
        "library": library_body,
        "inventory": inventory_body,
        "live_active": set(),  # channel or measurable keys
        "teleop_active": set(),  # tag ids
        "pose": {"x": 0.0, "y": 0.0, "rotation": 0.0},
        "epoch_ms": _now_ms(),
    }

    def bump_epoch() -> int:
        prev = int(state["epoch_ms"])
        nxt = max(_now_ms(), prev + 1)
        state["epoch_ms"] = nxt
        return nxt

    def _channel_for_live(args: dict[str, Any]) -> str:
        return str(
            args.get("channel")
            or args.get("measurable_id")
            or args.get("live_channel")
            or "tag_22.camera_image"
        )

    def _live_armed(channel: str) -> bool:
        if channel in state["live_active"]:
            return True
        # Allow measurable id or mjpeg alias once base channel is armed.
        if "tag_22.camera_image" in state["live_active"]:
            return channel.startswith("tag_22.camera_image")
        return False

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "contract_version": CONTRACT_VERSION}

    @app.get("/capabilities")
    async def capabilities() -> dict[str, Any]:
        return state["capabilities"]

    @app.get("/bench")
    async def bench() -> dict[str, Any]:
        return state["bench"]

    @app.get("/library")
    async def library() -> dict[str, Any]:
        return state["library"]

    @app.get("/inventory")
    async def inventory() -> dict[str, Any]:
        return state["inventory"]

    @app.post("/execute")
    async def execute(body: dict[str, Any]) -> JSONResponse:
        # Accept only contract shape; refuse legacy {op, params}.
        if "op" in body and "primitive" not in body:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "refused",
                    "error": {
                        "code": "BAD_REQUEST",
                        "message": "use {primitive, args}; op/params is not v1",
                    },
                },
            )

        primitive = body.get("primitive")
        args = body.get("args") or {}
        if not isinstance(primitive, str) or not primitive:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "failed",
                    "error": {"code": "BAD_REQUEST", "message": "primitive required"},
                },
            )
        if not isinstance(args, dict):
            return JSONResponse(
                status_code=400,
                content={
                    "status": "failed",
                    "error": {"code": "BAD_REQUEST", "message": "args must be object"},
                },
            )

        supported = set(state["capabilities"]["supported_primitives"])
        if primitive not in supported:
            return JSONResponse(
                {
                    "status": "refused",
                    "error": {
                        "code": "UNKNOWN_PRIMITIVE",
                        "message": f"unsupported primitive: {primitive}",
                    },
                }
            )

        if primitive == "START_LIVE_FEED":
            ch = _channel_for_live(args)
            state["live_active"].add(ch)
            epoch = bump_epoch()
            return JSONResponse(
                {
                    "status": "completed",
                    "result": {"channel": ch, "active": True},
                    "epoch_ms": epoch,
                }
            )

        if primitive == "END_LIVE_FEED":
            ch = _channel_for_live(args)
            state["live_active"].discard(ch)
            if ch == "tag_22.camera_image":
                state["live_active"].discard("tag_22.camera_image.mjpeg")
            epoch = bump_epoch()
            return JSONResponse(
                {
                    "status": "completed",
                    "result": {"channel": ch, "active": False},
                    "epoch_ms": epoch,
                }
            )

        if primitive == "START_TELEOP":
            tag = str(args.get("tag_id") or args.get("target_id") or "tag_22")
            state["teleop_active"].add(tag)
            epoch = bump_epoch()
            return JSONResponse(
                {
                    "status": "completed",
                    "result": {"tag_id": tag, "active": True, "ws_path": "/ws/teleop"},
                    "epoch_ms": epoch,
                }
            )

        if primitive == "END_TELEOP":
            tag = str(args.get("tag_id") or args.get("target_id") or "tag_22")
            state["teleop_active"].discard(tag)
            epoch = bump_epoch()
            return JSONResponse(
                {
                    "status": "completed",
                    "result": {"tag_id": tag, "active": False},
                    "epoch_ms": epoch,
                }
            )

        if primitive == "TELEOP_JOG":
            tag = str(args.get("tag_id") or "tag_22")
            if tag not in state["teleop_active"]:
                return JSONResponse(
                    {
                        "status": "refused",
                        "error": {
                            "code": "TELEOP_NOT_STARTED",
                            "message": "START_TELEOP required",
                        },
                    }
                )
            axis = str(args.get("axis") or "x")
            val = float(args.get("val") or args.get("delta") or 0.0)
            if axis in state["pose"]:
                state["pose"][axis] = float(state["pose"][axis]) + val
            epoch = bump_epoch()
            return JSONResponse(
                {
                    "status": "completed",
                    "result": {"tag_id": tag, "pose": dict(state["pose"])},
                    "epoch_ms": epoch,
                }
            )

        if primitive == "RECORD_MEASURABLES":
            epoch = bump_epoch()
            return JSONResponse(
                {
                    "status": "completed",
                    "result": {
                        "tag_id": args.get("tag_id") or "tag_22",
                        "measurables": {
                            "tag_22.camera_image": {
                                "encoding": "jpeg",
                                "bytes_b64": None,
                                "stub": True,
                            }
                        },
                    },
                    "epoch_ms": epoch,
                    "latch_quality": "software_approx",
                }
            )

        if primitive == "EVAL_KERNEL":
            epoch = bump_epoch()
            return JSONResponse(
                {
                    "status": "completed",
                    "result": {
                        "passed": True,
                        "score": 1.0,
                        "detail": "stub eval always passes",
                        "kernel_id": args.get("kernel_id"),
                    },
                    "epoch_ms": epoch,
                    "latch_quality": "software_approx",
                }
            )

        if primitive == "LOCALIZE_COMPONENTS":
            raw_ids = args.get("tag_ids")
            if isinstance(raw_ids, list) and raw_ids:
                tag_ids = [str(t).strip() for t in raw_ids if str(t).strip()]
            else:
                tag_ids = default_localize_tag_ids(state["inventory"])
            poses = {
                tid: {
                    "x": float(state["pose"]["x"]),
                    "y": float(state["pose"]["y"]),
                    "rotation": float(state["pose"]["rotation"]),
                    "stub": True,
                }
                for tid in tag_ids
            }
            epoch = bump_epoch()
            return JSONResponse(
                {
                    "status": "completed",
                    "result": {"tag_ids": tag_ids, "poses": poses},
                    "epoch_ms": epoch,
                    "latch_quality": "software_approx",
                }
            )

        # Declared in capabilities but not implemented by this reference stub
        # (lab skeleton lists the full Phase-6 surface; stub only runs live-plane).
        return JSONResponse(
            {
                "status": "refused",
                "error": {
                    "code": "NOT_IMPLEMENTED",
                    "message": (
                        f"primitive {primitive!r} is declared but not implemented "
                        "by the Phase-5 reference stub; fill adapters/ for Phase 6"
                    ),
                },
            }
        )

    @app.get("/stream/tag_22/camera_image.jpg")
    async def live_jpeg() -> Response:
        if not _live_armed("tag_22.camera_image"):
            return JSONResponse(
                status_code=409,
                content={
                    "status": "refused",
                    "error": {
                        "code": "LIVE_NOT_STARTED",
                        "message": "START_LIVE_FEED required before frame access",
                    },
                },
            )
        return Response(content=_STUB_JPEG, media_type="image/jpeg")

    @app.get("/stream/tag_22/camera_image.mjpg")
    async def live_mjpeg() -> StreamingResponse:
        if not _live_armed("tag_22.camera_image"):
            return JSONResponse(
                status_code=409,
                content={
                    "status": "refused",
                    "error": {
                        "code": "LIVE_NOT_STARTED",
                        "message": "START_LIVE_FEED required before stream access",
                    },
                },
            )

        boundary = "frame"

        async def gen():
            for _ in range(3):
                yield (
                    f"--{boundary}\r\n"
                    f"Content-Type: image/jpeg\r\n"
                    f"Content-Length: {len(_STUB_JPEG)}\r\n\r\n"
                ).encode("utf-8") + _STUB_JPEG + b"\r\n"
                await asyncio.sleep(0.05)

        return StreamingResponse(
            gen(),
            media_type=f"multipart/x-mixed-replace; boundary={boundary}",
        )

    @app.websocket("/ws/teleop")
    async def teleop_ws(ws: WebSocket) -> None:
        await ws.accept()
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await ws.send_json(
                        {
                            "epoch_ms": state["epoch_ms"],
                            "tag_id": "tag_22",
                            "kind": "error",
                            "error_code": "BAD_JSON",
                        }
                    )
                    continue

                if not isinstance(msg, dict):
                    await ws.send_json(
                        {
                            "epoch_ms": state["epoch_ms"],
                            "tag_id": "tag_22",
                            "kind": "error",
                            "error_code": "BAD_MESSAGE",
                        }
                    )
                    continue

                # Reject nested hot-path envelopes (old drafts / accidental trees).
                nested = False
                for k, v in msg.items():
                    if isinstance(v, (dict, list)) and k not in ():
                        nested = True
                        break
                if nested or "payload" in msg or "tunables" in msg:
                    await ws.send_json(
                        {
                            "epoch_ms": state["epoch_ms"],
                            "tag_id": str(msg.get("tag_id") or "tag_22"),
                            "kind": "error",
                            "error_code": "NESTED_ENVELOPE",
                        }
                    )
                    continue

                tag = str(msg.get("tag_id") or "tag_22")
                cmd = msg.get("cmd")

                if cmd == "PING":
                    await ws.send_json(
                        {
                            "epoch_ms": state["epoch_ms"],
                            "tag_id": tag,
                            "kind": "pong",
                        }
                    )
                    continue

                if tag not in state["teleop_active"]:
                    await ws.send_json(
                        {
                            "epoch_ms": state["epoch_ms"],
                            "tag_id": tag,
                            "kind": "error",
                            "error_code": "TELEOP_NOT_STARTED",
                        }
                    )
                    continue

                if cmd in ("JOG", "GOTO"):
                    axis = msg.get("axis")
                    motor_id = msg.get("motor_id")
                    val = float(msg.get("val") or 0.0)
                    if axis in ("x", "y", "rotation"):
                        if cmd == "JOG":
                            state["pose"][axis] = float(state["pose"][axis]) + val
                        else:
                            state["pose"][axis] = val
                    epoch = bump_epoch()
                    out: dict[str, Any] = {
                        "epoch_ms": epoch,
                        "tag_id": tag,
                        "kind": "pose_sample",
                        "x": float(state["pose"]["x"]),
                        "y": float(state["pose"]["y"]),
                        "rotation": float(state["pose"]["rotation"]),
                    }
                    if motor_id:
                        # Flat numeric sample key for the motor id (schema allows extra numbers).
                        out[str(motor_id)] = val
                    await ws.send_json(out)
                    continue

                await ws.send_json(
                    {
                        "epoch_ms": state["epoch_ms"],
                        "tag_id": tag,
                        "kind": "error",
                        "error_code": "UNKNOWN_CMD",
                    }
                )
        except WebSocketDisconnect:
            pass

    return app


def run(host: str = "127.0.0.1", port: int = 8765, backend_id: str = "stub.default") -> None:
    import uvicorn

    uvicorn.run(create_app(backend_id=backend_id), host=host, port=port, log_level="info")
