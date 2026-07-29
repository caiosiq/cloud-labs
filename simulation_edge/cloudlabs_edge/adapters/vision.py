"""Synthetic camera capture for sim observe / kernel eval."""

from __future__ import annotations

from typing import Any, Optional


def capture_bgr(tag_id: str, *, exposure_s: Optional[float] = None) -> Any:
    _ = exposure_s
    import numpy as np

    h, w = 48, 64
    yy, xx = np.mgrid[0:h, 0:w]
    seed = sum(ord(c) for c in (tag_id or "tag")) % 200
    b = ((xx + seed) % 256).astype(np.uint8)
    g = ((yy + seed) % 256).astype(np.uint8)
    r = np.full((h, w), seed, dtype=np.uint8)
    return np.stack([b, g, r], axis=-1)


def encode_jpeg(bgr: Any, *, quality: int = 80, scale: float = 1.0) -> bytes:
    _ = scale
    try:
        import cv2
        import numpy as np

        arr = np.asarray(bgr)
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


def localize_components(args: dict[str, Any]) -> dict[str, Any]:
    """LOCALIZE_COMPONENTS for simulation — inventory-scoped; soft if no scan helper."""
    from pathlib import Path

    from cloudlabs_edge_dev.edge_data import default_localize_tag_ids, load_inventory

    from adapters import context

    edge_root = Path(__file__).resolve().parent.parent
    inventory = load_inventory(edge_root)
    raw_ids = args.get("tag_ids")
    if isinstance(raw_ids, list) and raw_ids:
        tag_ids = [str(t).strip() for t in raw_ids if str(t).strip()]
    else:
        tag_ids = default_localize_tag_ids(inventory)

    lab = context.get_lab()
    fn = getattr(lab, "refresh_pose_from_camera", None)
    if callable(fn):
        fn(tag_ids=tag_ids)

    poses: dict[str, Any] = {}
    state = lab.get_lab_state() if hasattr(lab, "get_lab_state") else {}
    components = (state or {}).get("components") or {}
    for tid in tag_ids:
        comp = components.get(tid) if isinstance(components, dict) else None
        pose = None
        if isinstance(comp, dict):
            tun = ((comp.get("statecontrol") or {}).get("tunables") or {})
            pose = tun.get("reported_pose") or tun.get("nominal_pose")
        poses[tid] = dict(pose) if isinstance(pose, dict) else None
    return {"tag_ids": tag_ids, "poses": poses}
