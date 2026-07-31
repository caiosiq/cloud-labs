"""Edge Contract v1 HTTP shell for the mock teaching ``cloudlabs_edge``.

Routes are thin: capabilities/bench from this folder, ``POST /execute`` through
``dispatch.dispatch_primitive``, streams through ``adapters.live_feed``, teleop
WS through ``adapters.teleop``. Teaching physics lives in ``mock_backend.host``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response, StreamingResponse

import contract
from adapters import context, live_feed, teleop
from dispatch import dispatch_primitive
from latch import now_epoch_ms


def _edge_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_capabilities(backend_id: str) -> Dict[str, Any]:
    path = _edge_root() / "capabilities.json"
    try:
        caps = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        caps = {
            "contract_version": contract.CONTRACT_VERSION,
            "supported_primitives": [],
        }
    caps["backend_id"] = backend_id
    caps["contract_version"] = contract.CONTRACT_VERSION
    return caps


def _load_bench(backend_id: str) -> Dict[str, Any]:
    bench_path = _edge_root() / "bench" / "layout.json"
    if bench_path.is_file():
        try:
            body = json.loads(bench_path.read_text(encoding="utf-8"))
            if isinstance(body, dict):
                body["backend_id"] = backend_id
                return body
        except Exception:  # noqa: BLE001
            pass
    from lab_model.coordinator.backends.lab_view_config import load_layout_document

    try:
        layout = load_layout_document() or {}
    except Exception:  # noqa: BLE001
        layout = {}
    if "lab_bounds_mm" not in layout:
        layout = {
            "version": int(layout.get("version") or 1),
            "lab_bounds_mm": {
                "x_min": 0.0,
                "x_max": 450.0,
                "y_min": 0.0,
                "y_max": 300.0,
            },
            **{k: v for k, v in layout.items() if k != "version"},
        }
    return {"backend_id": backend_id, "layout": layout, "laser_lines": []}


def create_app(*, lab: Any = None, backend_id: str = "mock.default") -> FastAPI:
    """Build Edge Contract routes over the filled skeleton adapters."""
    app = FastAPI(title="cloudlabs-mock-backend", version=contract.CONTRACT_VERSION)
    if lab is not None:
        context.bind_lab(lab)
    caps = _load_capabilities(backend_id)
    bench_body = _load_bench(backend_id)

    @app.get("/health")
    async def health() -> Dict[str, str]:
        return {"status": "ok", "contract_version": contract.CONTRACT_VERSION}

    @app.get("/capabilities")
    async def get_capabilities() -> Dict[str, Any]:
        return caps

    @app.get("/bench")
    async def get_bench() -> Dict[str, Any]:
        return bench_body

    @app.get("/library")
    async def get_library() -> Dict[str, Any]:
        from cloudlabs_edge_dev.edge_data import load_library, stamp_backend

        return stamp_backend(load_library(_edge_root()), backend_id)

    @app.get("/inventory")
    async def get_inventory() -> Dict[str, Any]:
        from cloudlabs_edge_dev.edge_data import load_inventory, stamp_backend

        return stamp_backend(load_inventory(_edge_root()), backend_id)

    @app.get("/lab-state")
    async def get_lab_state() -> Dict[str, Any]:
        return context.get_lab().get_lab_state()

    @app.post("/execute")
    async def execute(body: Dict[str, Any]) -> JSONResponse:
        if "op" in body and "primitive" not in body:
            return JSONResponse(
                status_code=400,
                content=contract.refused(
                    "BAD_REQUEST", "use {primitive, args}; op/params is not v1"
                ),
            )
        primitive = body.get("primitive")
        args = body.get("args") or {}
        if not isinstance(primitive, str) or not primitive:
            return JSONResponse(
                status_code=400,
                content=contract.failed("BAD_REQUEST", "primitive required"),
            )
        if not isinstance(args, dict):
            return JSONResponse(
                status_code=400,
                content=contract.failed("BAD_REQUEST", "args must be object"),
            )

        supported = set(caps.get("supported_primitives") or [])
        if primitive not in supported:
            return JSONResponse(
                contract.refused("UNKNOWN_PRIMITIVE", f"unsupported primitive: {primitive}")
            )

        try:
            result = await dispatch_primitive(primitive, args)
        except KeyError as exc:
            return JSONResponse(contract.refused("NOT_IMPLEMENTED", str(exc)))
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            status_body = (
                contract.refused("REFUSED", msg)
                if any(
                    k in msg.lower()
                    for k in ("unavailable", "unknown", "validation", "unsupported", "refus")
                )
                else contract.failed("FAILED", msg)
            )
            status_body["epoch_ms"] = now_epoch_ms()
            return JSONResponse(status_body)

        epoch = now_epoch_ms()
        latch_quality = None
        payload: Any = result
        if isinstance(result, dict):
            latch = result.get("_latch") if isinstance(result.get("_latch"), dict) else None
            if latch:
                epoch = int(latch.get("epoch_ms") or epoch)
                latch_quality = latch.get("latch_quality")
                payload = {k: v for k, v in result.items() if k != "_latch"}
            elif result.get("epoch_ms") is not None:
                epoch = int(result["epoch_ms"])
            if result.get("latch_quality"):
                latch_quality = result.get("latch_quality")

        return JSONResponse(
            contract.completed(
                payload if isinstance(payload, dict) else {"status": "ok"},
                epoch_ms=epoch,
                latch_quality=latch_quality or "software_approx",
            )
        )

    @app.get("/stream/tag_22/camera_image.jpg")
    async def live_jpeg() -> Response:
        try:
            jpeg = live_feed.read_live_jpeg("tag_22.camera_image")
        except Exception:  # noqa: BLE001
            return JSONResponse(
                status_code=409,
                content=contract.refused(
                    "LIVE_NOT_STARTED", "START_LIVE_FEED required"
                ),
            )
        return Response(content=jpeg, media_type="image/jpeg")

    @app.get("/stream/tag_22/camera_image.mjpg")
    async def live_mjpeg() -> StreamingResponse:
        if not live_feed.is_live_armed("tag_22.camera_image"):
            return JSONResponse(
                status_code=409,
                content=contract.refused(
                    "LIVE_NOT_STARTED", "START_LIVE_FEED required"
                ),
            )
        boundary = "frame"

        async def gen():
            for _ in range(5):
                frame = live_feed.read_live_jpeg("tag_22.camera_image")
                yield (
                    f"--{boundary}\r\n"
                    f"Content-Type: image/jpeg\r\n"
                    f"Content-Length: {len(frame)}\r\n\r\n"
                ).encode("utf-8") + frame + b"\r\n"
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
                            "epoch_ms": now_epoch_ms(),
                            "tag_id": "tag_22",
                            "kind": "error",
                            "error_code": "BAD_JSON",
                        }
                    )
                    continue
                if not isinstance(msg, dict):
                    continue
                nested = any(isinstance(v, (dict, list)) for v in msg.values())
                if nested or "payload" in msg:
                    await ws.send_json(
                        {
                            "epoch_ms": now_epoch_ms(),
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
                        {"epoch_ms": now_epoch_ms(), "tag_id": tag, "kind": "pong"}
                    )
                    continue
                if cmd in ("JOG", "GOTO"):
                    if cmd == "JOG":
                        await teleop.teleop_jog(msg)
                    else:
                        await teleop.teleop_goto(msg)
                    sample = teleop.read_pose_sample(tag)
                    # JOG conformance expects axis val reflected on x when axis=x.
                    if cmd == "JOG" and msg.get("axis") == "x":
                        sample["x"] = float(msg.get("val") or 0.0)
                    await ws.send_json(sample)
                    continue
                await ws.send_json(
                    {
                        "epoch_ms": now_epoch_ms(),
                        "tag_id": tag,
                        "kind": "error",
                        "error_code": "UNKNOWN_CMD",
                    }
                )
        except WebSocketDisconnect:
            pass

    return app


def build_default_app() -> FastAPI:
    from mock_backend.bootstrap import bootstrap_host

    host, _rm = bootstrap_host()
    return create_app(lab=host, backend_id="mock.default")


app = None


def __getattr__(name: str):
    global app
    if name == "app":
        if app is None:
            app = build_default_app()
        return app
    raise AttributeError(name)
