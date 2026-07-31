"""Mock camera capture: synthetic BGR + JPEG encode + RECORD/SYNC."""

from __future__ import annotations

from typing import Any, Optional

from adapters import context


def capture_bgr(tag_id: str, *, exposure_s: Optional[float] = None) -> Any:
    """Return HxWx3 uint8 BGR — from mock preview decode or a synthetic gradient."""
    _ = exposure_s
    import numpy as np

    lab = context.get_lab()
    try:
        if hasattr(lab, "fetch_table_cam_preview_jpeg"):
            jpeg = lab.fetch_table_cam_preview_jpeg(0, 0.2)
            if isinstance(jpeg, (bytes, bytearray)) and len(jpeg) > 2:
                from lab_model.execution.optimization.metrics.image_features import (
                    decode_png_bytes_to_bgr,
                )

                # JPEG path: try cv2/PIL via encode round-trip helpers if available.
                try:
                    import cv2

                    buf = np.frombuffer(bytes(jpeg), dtype=np.uint8)
                    decoded = cv2.imdecode(buf, cv2.IMREAD_COLOR)
                    if decoded is not None:
                        return decoded
                except Exception:  # noqa: BLE001
                    pass
                _ = decode_png_bytes_to_bgr  # may not apply to jpeg
    except Exception:  # noqa: BLE001
        pass

    # Deterministic teaching pattern keyed by tag id.
    h, w = 48, 64
    yy, xx = np.mgrid[0:h, 0:w]
    seed = sum(ord(c) for c in tag_id) % 200
    b = ((xx + seed) % 256).astype(np.uint8)
    g = ((yy + seed) % 256).astype(np.uint8)
    r = np.full((h, w), seed, dtype=np.uint8)
    return np.stack([b, g, r], axis=-1)


def encode_jpeg(bgr: Any, *, quality: int = 80, scale: float = 1.0) -> bytes:
    import numpy as np

    arr = np.asarray(bgr)
    if scale != 1.0 and arr.ndim == 3:
        try:
            import cv2

            arr = cv2.resize(
                arr,
                (max(1, int(arr.shape[1] * scale)), max(1, int(arr.shape[0] * scale))),
            )
        except Exception:  # noqa: BLE001
            pass
    try:
        import cv2

        ok, buf = cv2.imencode(
            ".jpg", arr, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)]
        )
        if ok:
            return bytes(buf)
    except Exception:  # noqa: BLE001
        pass
    from cloudlabs_edge_dev.stub_server import _STUB_JPEG

    return _STUB_JPEG


def capture_jpeg(tag_id: str, *, profile: str | None = None) -> bytes:
    _ = profile
    return encode_jpeg(capture_bgr(tag_id))


async def localize_components(args: dict[str, Any]) -> dict[str, Any]:
    """LOCALIZE_COMPONENTS → RECORD_TUNABLES(nominal_pose)."""
    return await record_tunables(
        {
            "tag_ids": args.get("tag_ids"),
            "tunable_paths": ["nominal_pose"],
            "force_rescan": args.get("force_rescan", True),
        }
    )


async def record_tunables(args: dict[str, Any]) -> dict[str, Any]:
    """RECORD_TUNABLES via host ``record_tunables`` (mock world → tunables)."""
    lab = context.get_lab()
    fn = getattr(lab, "record_tunables", None)
    if not callable(fn):
        raise RuntimeError("lab.record_tunables is not implemented")
    raw_ids = args.get("tag_ids")
    tag_ids = (
        [str(t).strip() for t in raw_ids if str(t).strip()]
        if isinstance(raw_ids, list)
        else None
    )
    raw_paths = args.get("tunable_paths")
    paths = (
        [str(p).strip() for p in raw_paths if str(p).strip()]
        if isinstance(raw_paths, list)
        else None
    )
    result = fn(
        tag_ids=tag_ids,
        tunable_paths=paths,
        force_rescan=bool(args.get("force_rescan", True)),
    )
    if hasattr(result, "__await__"):
        result = await result
    return result if isinstance(result, dict) else {"status": "ok"}


async def sync_runtime(args: dict[str, Any]) -> dict[str, Any]:
    """SYNC_RUNTIME — SET(set_at_init) + RECORD(recordable); stamps READY."""
    from lab_model.language.primitives.macros.sync_runtime import run_sync_runtime
    from lab_model.language.primitives.schemas import SyncRuntimeBody

    lab = context.get_lab()
    if hasattr(lab, "set_runtime_sync_status"):
        lab.set_runtime_sync_status("running")
    raw_ids = args.get("tag_ids")
    tag_ids = (
        [str(t).strip() for t in raw_ids if str(t).strip()]
        if isinstance(raw_ids, list)
        else None
    )
    params: dict[str, Any] = {}
    if tag_ids:
        params["tag_ids"] = tag_ids
    body = SyncRuntimeBody(action="SYNC_RUNTIME", parameters=params)
    try:
        result = await run_sync_runtime(lab, body)
    except Exception as exc:  # noqa: BLE001
        if hasattr(lab, "set_runtime_sync_status"):
            lab.set_runtime_sync_status("failed", errors=[str(exc)])
        raise
    errors = []
    status = "ready"
    if isinstance(result, dict):
        errors = list(result.get("errors") or [])
        if errors or result.get("status") == "failed":
            status = "failed"
    if hasattr(lab, "set_runtime_sync_status"):
        lab.set_runtime_sync_status(status, errors=errors)
    out = dict(result) if isinstance(result, dict) else {"status": status}
    out.setdefault("runtime_sync", {"status": status})
    return out
