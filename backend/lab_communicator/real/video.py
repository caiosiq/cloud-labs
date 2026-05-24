"""Real-backend camera + recorder helpers.

Three sub-areas, all real-only:

* **Recorder subprocess lifecycle** -- the table cameras (cam1=9999,
  cam2=10000) are driven by a separate Python script in
  ``lab_automation`` that we spawn at boot and terminate on shutdown.
  Functions: :func:`start_recorder_processes`,
  :func:`shutdown_recorders`, :func:`send_recorder_cmd`.

* **Video streams** -- async generators yielding MJPEG frames for the
  Cloud-Labs HTTP ``/video_feed`` and ``/optimization_feed`` routes.
  Functions: :func:`get_video_stream`, :func:`get_optimization_stream`.
  Heavy on ``cv2``; we import it lazily inside the generators so the
  module is importable on machines without OpenCV (the streams just
  fail at first frame in that case).

* **Single-shot capture** -- :func:`capture_table_cam` grabs one frame
  from a table recorder camera and returns PNG bytes. Used by the
  ``OPTICAL_CAMERA``-typed components for ``record_measurables_for_tag``.

All helpers take the ``RealLabCommunicator`` instance explicitly. The
class keeps thin ``def`` wrappers so the dispatch layer
(``lab_model.primitives``) and Cloud-Labs HTTP routes keep their existing
API. ``atexit.register`` is wired to the bound thin wrapper so
shutdown still works after Phase 1.

Architectural rule (``communicator_refactor.md`` §5.1): real-only --
free to spawn subprocesses, talk to ``cv2``, and depend on
``LAB_AUTOMATION_PATH``. Must NOT import the mock backend or
:mod:`lab_communicator.base`. May import sibling real-only modules
(``real.optimization``) where the call graph requires it.
"""

from __future__ import annotations

import atexit
import os
import subprocess
import sys
import threading
import time
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple


if TYPE_CHECKING:
    from lab_communicator.real.communicator import RealLabCommunicator


def _experiment(communicator: "RealLabCommunicator"):
    return getattr(communicator, "experiment", None)


def _registry_camera_for_tag(communicator: "RealLabCommunicator", tag_id: str):
    exp = _experiment(communicator)
    if exp is not None and hasattr(exp, "get_camera_component"):
        return exp.get_camera_component(str(tag_id))
    return None


def _registry_camera_for_recorder_cam(communicator: "RealLabCommunicator", cam_id: int):
    exp = _experiment(communicator)
    if exp is not None and hasattr(exp, "find_tag_id_for_recorder_cam"):
        tag_id = exp.find_tag_id_for_recorder_cam(int(cam_id))
        if tag_id:
            return exp.get_camera_component(tag_id)
    return None


def _sync_table_cam_flags_from_component(
    communicator: "RealLabCommunicator",
    cam_id: int,
    comp: Any,
) -> None:
    if comp is None:
        return
    if hasattr(comp, "is_connected"):
        communicator._table_cam_connected[cam_id] = bool(comp.is_connected)  # noqa: SLF001
    if hasattr(comp, "is_streaming"):
        communicator._table_cam_streaming[cam_id] = bool(comp.is_streaming)  # noqa: SLF001


def _table_cam_use_mock_env() -> bool:
    return os.getenv("TABLE_CAM_USE_MOCK", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _parse_status_kv(message: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for token in (message or "").split():
        if "=" not in token:
            continue
        k, v = token.split("=", 1)
        if v in ("0", "1"):
            out[k] = v == "1"
        else:
            out[k] = v
    return out


def _recorder_port_for_cam(cam_id: int) -> int:
    return 9999 if int(cam_id) == 1 else 10000


def _drain_recorder_stderr(proc: subprocess.Popen, label: str) -> None:
    """Log recorder subprocess stderr so mvsdk / import errors are visible."""

    def _run() -> None:
        if proc.stderr is None:
            return
        try:
            for raw in iter(proc.stderr.readline, b""):
                if not raw:
                    break
                line = raw.decode(errors="replace").rstrip()
                if line:
                    print(f"[REAL LAB][recorder {label}] {line}", flush=True)
        except Exception as e:
            print(f"[REAL LAB][recorder {label}] stderr drain ended: {e}", flush=True)

    threading.Thread(target=_run, daemon=True, name=f"recorder-stderr-{label}").start()


def _recorder_procs_alive(communicator: "RealLabCommunicator") -> bool:
    procs = getattr(communicator, "_recorder_procs", None) or []
    if not procs:
        return False
    for p in procs:
        if p.poll() is not None:
            return False
    return True


def _probe_recorder_port(port: int) -> bool:
    try:
        import socket

        s = socket.create_connection(("localhost", port), timeout=0.4)
        s.close()
        return True
    except Exception:
        return False


def table_cam_status_snapshot(
    communicator: "RealLabCommunicator",
    only_cam_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Aggregate table-cam state for API / UI."""
    cloud = bool(getattr(communicator, "_use_cloudlab_table_recorder", False))
    mock_mode = bool(getattr(communicator, "_table_cam_recorder_mock", False))
    alive = _recorder_procs_alive(communicator)
    try:
        from lab_communicator.shared.lab_view_config import (  # noqa: PLC0415
            load_table_cam_preview_config,
        )

        preview_config = load_table_cam_preview_config().as_dict()
    except Exception:
        preview_config = {
            "scale": 0.75,
            "jpeg_quality": 72,
            "target_fps": 144,
            "max_inflight_requests": 3,
        }

    out: Dict[str, Any] = {
        "recorder_variant": "cloudlab" if cloud else "legacy",
        "recorder_alive": alive,
        "recorder_mock": mock_mode,
        "ports": {"cam1": 9999, "cam2": 10000},
        "preview_config": preview_config,
        "cameras": {},
    }
    cam_ids = (only_cam_id,) if only_cam_id in (1, 2) else (1, 2)
    for cam_id in cam_ids:
        port = _recorder_port_for_cam(cam_id)
        entry: Dict[str, Any] = {
            "connected": bool(communicator._table_cam_connected.get(cam_id)),  # noqa: SLF001
            "streaming": bool(communicator._table_cam_streaming.get(cam_id)),  # noqa: SLF001
            "hardware": communicator._table_cam_hardware.get(cam_id, "none"),  # noqa: SLF001
            "port": port,
            "port_open": _probe_recorder_port(port) if alive else False,
            "last_error": communicator._table_cam_last_error.get(cam_id),  # noqa: SLF001
        }
        if cloud and alive and _probe_recorder_port(port):
            try:
                from lab_automation.managers.recorder_capture_helpers_cloudlab import (  # noqa: PLC0415
                    query_status_cloudlab,
                )

                ok, msg = query_status_cloudlab(cam_id, timeout_s=1.2)
                if ok:
                    entry["recorder_status"] = _parse_status_kv(msg)
                    # HTTP handlers own connected/streaming; recorder STATUS is diagnostic only.
                    hw = entry["recorder_status"].get("hardware")
                    if hw:
                        entry["hardware"] = str(hw)
            except ImportError:
                entry["last_error"] = "recorder_capture_helpers_cloudlab unavailable"
        out["cameras"][str(cam_id)] = entry
    return out


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def camera_images_base_dir() -> str:
    """Canonical ``Camera_Images`` root for new optimization run folders.

    Anchored at ``$LAB_AUTOMATION_PATH/Camera_Images`` when the env var is
    set; falls back to a ``./Camera_Images`` next to the backend CWD
    otherwise (handy for local dev). Always creates the directory --
    optimization strategies dump PNGs into per-run subdirs and we want
    that parent to exist no matter what.
    """
    try:
        from lab_communicator.shared.lab_view_config import get_lab_automation_path  # noqa: PLC0415

        lab_path = get_lab_automation_path()
    except Exception:
        lab_path = os.getenv("LAB_AUTOMATION_PATH")
    if lab_path:
        base = os.path.join(os.path.abspath(lab_path), "Camera_Images")
    else:
        base = os.path.abspath("Camera_Images")
    os.makedirs(base, exist_ok=True)
    return base


# ---------------------------------------------------------------------------
# Recorder subprocesses (cam1=9999, cam2=10000)
# ---------------------------------------------------------------------------

def _recorder_subprocess_env(lab_path: str) -> Dict[str, str]:
    """Ensure MindVision ``mvsdk.py`` in lab root wins over PyPI ``mvsdk`` (MediaValet).

    Recorder scripts live under ``lab_automation/scripts/``, so Python puts ``scripts/``
    on ``sys.path`` first. Without ``PYTHONPATH``, ``pip install mvsdk`` shadows the
    real camera SDK and CONNECT fails with
    ``module 'mvsdk' has no attribute 'CameraEnumerateDevice'``.
    """
    env = os.environ.copy()
    lab_abs = os.path.abspath(lab_path)
    prev = env.get("PYTHONPATH", "").strip()
    env["PYTHONPATH"] = lab_abs if not prev else f"{lab_abs}{os.pathsep}{prev}"
    return env


def _recorder_python_executable(lab_path: str) -> str:
    """Prefer ``lab_automation/.venv`` when present so recorder deps match the lab stack."""
    override = os.getenv("TABLE_CAM_RECORDER_PYTHON", "").strip()
    if override:
        return override
    venv_py = os.path.join(lab_path, ".venv", "Scripts", "python.exe")
    if os.name == "nt" and os.path.isfile(venv_py):
        return venv_py
    venv_py_unix = os.path.join(lab_path, ".venv", "bin", "python")
    if os.path.isfile(venv_py_unix):
        return venv_py_unix
    return sys.executable


def send_recorder_cmd(port: int, cmd: str) -> None:
    """Send a one-line text command to a recorder process on ``port``.

    Used to deliver ``REC_OFF`` / ``EXIT`` during shutdown. Best-effort
    -- swallows all socket errors with a logged warning. The recorder
    protocol is line-oriented; we append ``\\n`` if the caller didn't.
    """
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        s.connect(("localhost", port))
        s.sendall((cmd.strip() + "\n").encode())
        s.close()
    except Exception as e:
        print(f"[REAL LAB] Recorder cmd (port {port}): {e}")


def start_recorder_processes(communicator: "RealLabCommunicator") -> None:
    """Spawn the cam1/cam2 recorder subprocesses (lazy camera open for cloudlabs).

    When ``scripts/recorder_cam_laser_align_cloudlab.py`` exists under
    ``$LAB_AUTOMATION_PATH``, it is used (lazy ``CONNECT``, ``DISCONNECT``,
    ``STREAM_ON``); otherwise the legacy ``recorder_cam_laser_align_simplified.py``
    is spawned (immediate camera attach + ``REC_ON`` protocol).

    Set ``TABLE_CAM_RECORDER_VARIANT=legacy`` to force the legacy script when both
    exist. ``LAB_TABLE_RECORDER_SCRIPT`` overrides the chosen path outright when
    set to an existing ``.py`` file.

    Honors ``$TABLE_CAM_USE_MOCK``. Registers ``shutdown_recorders`` with
    ``atexit``.
    """
    try:
        from lab_communicator.shared.lab_view_config import get_lab_automation_path  # noqa: PLC0415

        lab_path = get_lab_automation_path()
    except Exception:
        lab_path = os.getenv("LAB_AUTOMATION_PATH")
    if not lab_path or not os.path.isdir(lab_path):
        print(
            "[REAL LAB] LAB_AUTOMATION_PATH not set or invalid; "
            "skipping recorder warm-up."
        )
        return

    force_legacy = (
        os.getenv("TABLE_CAM_RECORDER_VARIANT", "").strip().lower() == "legacy"
    )
    forced_script = os.getenv("LAB_TABLE_RECORDER_SCRIPT", "").strip()

    recorder_script = ""
    cloudlab_candidates = (
        os.path.join(lab_path, "scripts", "recorder_cam_laser_align_cloudlab.py"),
        os.path.join(lab_path, "recorder_cam_laser_align_cloudlab.py"),
    )
    legacy_candidates = (
        os.path.join(lab_path, "scripts", "recorder_cam_laser_align_simplified.py"),
        os.path.join(lab_path, "recorder_cam_laser_align_simplified.py"),
    )

    if forced_script and os.path.isfile(forced_script):
        recorder_script = forced_script
    elif not force_legacy and any(os.path.isfile(p) for p in cloudlab_candidates):
        recorder_script = next(p for p in cloudlab_candidates if os.path.isfile(p))
    else:
        for p in legacy_candidates:
            if os.path.isfile(p):
                recorder_script = p
                break

    communicator._use_cloudlab_table_recorder = bool(
        recorder_script
        and os.path.isfile(recorder_script)
        and ("cloudlab" in os.path.basename(recorder_script).lower())
    )

    if not recorder_script or not os.path.isfile(recorder_script):
        print(
            "[REAL LAB] Recorder script not found; "
            "table cam capture may fail (ports 9999/10000)."
        )
        return

    use_mock = _table_cam_use_mock_env()
    use_real_camera = not use_mock
    communicator._table_cam_recorder_mock = use_mock  # noqa: SLF001
    extra = ["--real-camera"] if use_real_camera else []
    if communicator._use_cloudlab_table_recorder:
        try:
            from lab_communicator.shared.lab_view_config import (  # noqa: PLC0415
                load_table_cam_preview_config,
            )

            preview_cfg = load_table_cam_preview_config()
            extra.extend(
                [
                    "--scale",
                    str(preview_cfg.scale),
                    "--jpeg-quality",
                    str(preview_cfg.jpeg_quality),
                    "--teleop-scale",
                    str(preview_cfg.teleop_scale),
                    "--teleop-jpeg-quality",
                    str(preview_cfg.teleop_jpeg_quality),
                    "--teleop-stream-drain",
                    str(preview_cfg.teleop_stream_drain_frames),
                ]
            )
        except Exception as e:
            print(
                f"[REAL LAB] table_cam_preview.json not loaded ({e}); "
                "recorder using script defaults",
                flush=True,
            )

    cloudlab_spawn = communicator._use_cloudlab_table_recorder  # noqa: SLF001
    recorder_py = _recorder_python_executable(lab_path)
    recorder_env = _recorder_subprocess_env(lab_path)
    try:
        p1 = subprocess.Popen(
            [
                recorder_py,
                recorder_script,
                "--cam",
                "0",
                "--port",
                "9999",
                "--prefix",
                "cam1",
            ]
            + extra,
            cwd=lab_path,
            env=recorder_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        p2 = subprocess.Popen(
            [
                recorder_py,
                recorder_script,
                "--cam",
                "1",
                "--port",
                "10000",
                "--prefix",
                "cam2",
            ]
            + extra,
            cwd=lab_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        communicator._recorder_procs = [p1, p2]
        _drain_recorder_stderr(p1, "cam1")
        _drain_recorder_stderr(p2, "cam2")
        time.sleep(0.5 if cloudlab_spawn else 1.2)

        dead = []
        for label, proc in (("cam1", p1), ("cam2", p2)):
            code = proc.poll()
            if code is not None:
                dead.append(f"{label} exit={code}")
        if dead:
            print(
                "[REAL LAB] Recorder subprocess died on startup: "
                + "; ".join(dead)
                + " (see [REAL LAB][recorder …] lines above; often wrong PyPI mvsdk or missing deps)",
                flush=True,
            )
            communicator._recorder_procs = []
            return

        mode = (
            "cloudlabs (lazy CONNECT)"
            if cloudlab_spawn
            else "legacy (camera open at recorder start)"
        )
        print(
            f"[REAL LAB] Recorder processes started ({mode}) cam1=9999, cam2=10000 "
            f"script={os.path.basename(recorder_script)} "
            f"python={sys.executable} real_camera={use_real_camera} "
            f"TABLE_CAM_USE_MOCK={use_mock}",
            flush=True,
        )
        if cloudlab_spawn and use_real_camera:
            for port in (9999, 10000):
                if not _probe_recorder_port(port):
                    print(
                        f"[REAL LAB] Warning: recorder port {port} not accepting "
                        "connections after spawn",
                        flush=True,
                    )
        atexit.register(communicator._shutdown_recorders)
    except Exception as e:
        print(f"[REAL LAB] Failed to start recorders: {e}", flush=True)
        communicator._recorder_procs = []


def shutdown_recorders(communicator: "RealLabCommunicator") -> None:
    """Tear recorder processes down cleanly.

    Cloud-Labs recorders honour ``DISCONNECT`` + ``EXIT``; legacy
    ``recorder_cam_laser_align_simplified.py`` still expects ``REC_OFF``
    followed by ``EXIT``.
    """
    if not communicator._recorder_procs:
        return
    cloudlabs = getattr(communicator, "_use_cloudlab_table_recorder", False)
    for port in (9999, 10000):
        if cloudlabs:
            send_recorder_cmd(port, "DISCONNECT")
        else:
            send_recorder_cmd(port, "REC_OFF")
        send_recorder_cmd(port, "EXIT")
    for p in communicator._recorder_procs:
        try:
            p.wait(timeout=2.0)
        except Exception:
            try:
                p.terminate()
            except Exception:
                pass
    communicator._recorder_procs = []


# ---------------------------------------------------------------------------
# Video streams (MJPEG generators for HTTP routes)
# ---------------------------------------------------------------------------

def get_video_stream(communicator: "RealLabCommunicator", fps: int = 10):
    """Yield MJPEG frames from the ceiling camera (HTTP ``/video_feed``).

    Pulls frames from ``communicator.experiment.ceiling_cam1`` when
    available, falls back to a "NO SIGNAL" dummy frame when the camera
    isn't wired up or fails. ``fps`` is clamped to ``[1, 60]``. This is
    a synchronous generator -- the caller (Quart's StreamingResponse)
    runs it in a worker, which is why we use ``time.sleep`` not
    ``asyncio.sleep``.
    """
    print(f"[REAL LAB] Starting Video Stream Generator at {fps} FPS...")

    import cv2
    import numpy as np

    camera = None
    registry_cam = _registry_camera_for_tag(communicator, "tag_99")
    if registry_cam is not None:
        camera = registry_cam
    elif communicator.experiment and hasattr(communicator.experiment, "ceiling_cam1"):
        camera = communicator.experiment.ceiling_cam1

    sleep_duration = 1.0 / max(1, min(fps, 60))

    while True:
        frame = None
        if camera is not None:
            try:
                frame = (
                    camera.get_frame()
                    if hasattr(camera, "get_frame")
                    else None
                )
            except Exception as e:
                print(f"[REAL LAB] Camera stream error: {e}")
                frame = None

        if frame is None:
            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            cv2.putText(
                frame, "NO SIGNAL", (200, 240),
                cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2,
            )

        ret, buffer = cv2.imencode(".jpg", frame)
        if ret:
            frame_bytes = buffer.tobytes()
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n"
            )

        time.sleep(sleep_duration)


def get_optimization_stream(communicator: "RealLabCommunicator", fps: int = 5):
    """Yield MJPEG frames by watching the ``Camera_Images`` directory.

    Used for the HTTP ``/optimization_feed`` route during a COBYLA /
    Newton run. Polls the latest PNG produced by the optimization
    strategy and re-encodes to JPEG so the browser can stream it.
    Falls back to a "WAITING FOR OPTIMIZATION" dummy frame between
    runs or before the first PNG is written.

    The "latest PNG" lookup lives in :mod:`lab_communicator.real.optimization`
    -- imported lazily here to avoid a hard cycle (optimization.py
    doesn't import video.py, but if it ever did the lazy import keeps
    things robust). ``fps`` is clamped to ``[1, 30]``.
    """
    import cv2

    # Ensure the most likely directory exists so strategies that rely
    # on CWD won't fail silently.
    try:
        os.makedirs(os.path.abspath("Camera_Images"), exist_ok=True)
    except Exception:
        pass

    watch_dirs = communicator._get_optimization_watch_dirs()
    print(f"[REAL LAB] Starting Optimization Feed watching: {watch_dirs}")

    sleep_duration = 1.0 / max(1, min(fps, 30))
    last_mtime_ns = 0
    last_size = -1
    last_frame_bytes = None

    while True:
        try:
            latest_file, current_ns = communicator._get_latest_optimization_png()
            if latest_file:
                try:
                    current_size = os.path.getsize(latest_file)
                except Exception:
                    current_size = -1

                # Refresh if the file version changed (mtime / size /
                # file identity).
                if current_ns > last_mtime_ns or current_size != last_size:
                    # Retry decode a few times to avoid libpng "Read
                    # Error" on partially-written files.
                    img = None
                    for _attempt in range(6):
                        try:
                            time.sleep(0.05)
                            img = cv2.imread(latest_file)
                        except Exception:
                            img = None
                        if img is not None:
                            break

                    if img is not None:
                        ret, buffer = cv2.imencode(".jpg", img)
                        if ret:
                            last_frame_bytes = buffer.tobytes()
                            last_mtime_ns = current_ns
                            last_size = current_size
                            print(
                                f"[REAL LAB] optimization-stream updated "
                                f"(file={os.path.basename(latest_file)} "
                                f"ns={current_ns} size={current_size})"
                            )
        except Exception as e:
            print(f"[REAL LAB] Error in optimization stream: {e}")

        if last_frame_bytes:
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + last_frame_bytes + b"\r\n"
            )
        else:
            import numpy as np
            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            cv2.putText(
                frame, "WAITING FOR OPTIMIZATION", (50, 240),
                cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2,
            )
            ret, buffer = cv2.imencode(".jpg", frame)
            if ret:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + buffer.tobytes() + b"\r\n"
                )

        time.sleep(sleep_duration)

# ---------------------------------------------------------------------------
# Table cam lifecycle + MJPEG (cloudlabs recorder fork)
# ---------------------------------------------------------------------------

def _set_table_cam_error(
    communicator: "RealLabCommunicator", cam_id: int, message: Optional[str]
) -> None:
    communicator._table_cam_last_error[cam_id] = message  # noqa: SLF001


def table_cam_connect(
    communicator: "RealLabCommunicator",
    cam_id: int,
) -> Tuple[bool, str]:
    if cam_id not in (1, 2):
        return False, "cam_id must be 1 or 2"
    with communicator._table_cam_lock:
        if not _recorder_procs_alive(communicator):
            msg = "recorder subprocesses are not running (see startup logs)"
            _set_table_cam_error(communicator, cam_id, msg)
            communicator._table_cam_connected[cam_id] = False  # noqa: SLF001
            return False, msg
        if not communicator._use_cloudlab_table_recorder:
            port = _recorder_port_for_cam(cam_id)
            if not _probe_recorder_port(port):
                msg = f"legacy recorder port {port} is not open"
                _set_table_cam_error(communicator, cam_id, msg)
                communicator._table_cam_connected[cam_id] = False  # noqa: SLF001
                return False, msg
            communicator._table_cam_connected[cam_id] = True  # noqa: SLF001
            communicator._table_cam_hardware[cam_id] = (  # noqa: SLF001
                "mock" if communicator._table_cam_recorder_mock else "real"  # noqa: SLF001
            )
            _set_table_cam_error(communicator, cam_id, None)
            return True, "legacy recorder port open"
        try:
            from lab_automation.managers.recorder_capture_helpers_cloudlab import (  # noqa: PLC0415
                connect_cam_cloudlab,
            )
        except ImportError as e:
            msg = f"cannot import recorder_capture_helpers_cloudlab: {e}"
            _set_table_cam_error(communicator, cam_id, msg)
            return False, msg

        registry_cam = _registry_camera_for_recorder_cam(communicator, cam_id)
        print(f"[REAL LAB] table_cam_connect cam{cam_id} …", flush=True)
        if registry_cam is not None:
            ok, msg = registry_cam.connect()
        else:
            ok, msg = connect_cam_cloudlab(cam_id)
        if not ok:
            communicator._table_cam_connected[cam_id] = False  # noqa: SLF001
            communicator._table_cam_hardware[cam_id] = "none"  # noqa: SLF001
            _set_table_cam_error(communicator, cam_id, msg)
            print(f"[REAL LAB] table_cam_connect cam{cam_id} FAILED: {msg}", flush=True)
            return False, msg

        parsed = _parse_status_kv(msg)
        if registry_cam is not None:
            _sync_table_cam_flags_from_component(communicator, cam_id, registry_cam)
        else:
            communicator._table_cam_connected[cam_id] = bool(  # noqa: SLF001
                parsed.get("connected", True)
            )
            communicator._table_cam_streaming[cam_id] = bool(  # noqa: SLF001
                parsed.get("streaming", False)
            )
        hw = str(parsed.get("hardware", "real" if not communicator._table_cam_recorder_mock else "mock"))  # noqa: SLF001
        communicator._table_cam_hardware[cam_id] = hw  # noqa: SLF001
        _set_table_cam_error(communicator, cam_id, None)
        print(
            f"[REAL LAB] table_cam_connect cam{cam_id} OK ({msg})",
            flush=True,
        )
        return True, msg or "ok"


def table_cam_disconnect(
    communicator: "RealLabCommunicator",
    cam_id: int,
) -> Tuple[bool, str]:
    if cam_id not in (1, 2):
        return False, "cam_id must be 1 or 2"
    with communicator._table_cam_lock:
        if not communicator._use_cloudlab_table_recorder:
            communicator._table_cam_streaming[cam_id] = False  # noqa: SLF001
            communicator._table_cam_connected[cam_id] = False  # noqa: SLF001
            _set_table_cam_error(communicator, cam_id, None)
            return (
                True,
                "legacy recorder: UI disconnected (camera may stay open until backend exit)",
            )

        communicator._table_cam_streaming[cam_id] = False  # noqa: SLF001
        communicator._table_cam_connected[cam_id] = False  # noqa: SLF001
        communicator._table_cam_hardware[cam_id] = "none"  # noqa: SLF001
        try:
            from lab_automation.managers.recorder_capture_helpers_cloudlab import (  # noqa: PLC0415
                _close_preview_jpeg_sock,
                disconnect_cam_cloudlab,
            )
        except ImportError as e:
            msg = f"cannot import recorder_capture_helpers_cloudlab: {e}"
            _set_table_cam_error(communicator, cam_id, msg)
            return False, msg

        _close_preview_jpeg_sock(cam_id)
        registry_cam = _registry_camera_for_recorder_cam(communicator, cam_id)
        if registry_cam is not None:
            ok, msg = registry_cam.disconnect()
        else:
            ok, msg = disconnect_cam_cloudlab(cam_id)
        if not ok:
            _set_table_cam_error(communicator, cam_id, msg)
            print(f"[REAL LAB] table_cam_disconnect cam{cam_id} FAILED: {msg}", flush=True)
            return False, msg
        _set_table_cam_error(communicator, cam_id, None)
        print(f"[REAL LAB] table_cam_disconnect cam{cam_id} OK", flush=True)
        return True, msg or "ok"


def _preview_profile_for_cam(
    communicator: "RealLabCommunicator", cam_id: int
) -> str:
    profiles = getattr(communicator, "_table_cam_stream_profile", None) or {}
    return str(profiles.get(int(cam_id), "default"))


def _preview_timeout_for_cam(
    communicator: "RealLabCommunicator", cam_id: int
) -> float:
    try:
        from lab_communicator.shared.lab_view_config import (  # noqa: PLC0415
            load_table_cam_preview_config,
        )

        prof = load_table_cam_preview_config().profile(
            _preview_profile_for_cam(communicator, cam_id)
        )
        return float(prof.get("fetch_timeout_s", 0.45))
    except Exception:
        return 0.08 if _preview_profile_for_cam(communicator, cam_id) == "teleop" else 0.45


def table_cam_live_set(
    communicator: "RealLabCommunicator",
    cam_id: int,
    enabled: bool,
    *,
    profile: str = "default",
) -> Tuple[bool, str]:
    if cam_id not in (1, 2):
        return False, "cam_id must be 1 or 2"
    with communicator._table_cam_lock:
        if not communicator._use_cloudlab_table_recorder:
            msg = (
                "live MJPEG previews require recorder_cam_laser_align_cloudlab.py "
                "(set TABLE_CAM_RECORDER_VARIANT legacy to force simplified recorder)."
            )
            communicator._table_cam_streaming[cam_id] = False  # noqa: SLF001
            return False, msg
        if not communicator._table_cam_connected.get(cam_id):
            return False, "connect the camera before enabling live preview"
        try:
            from lab_automation.managers.recorder_capture_helpers_cloudlab import (  # noqa: PLC0415
                stream_off_cloudlab,
                stream_on_cloudlab,
            )
        except ImportError:
            return (
                False,
                "cannot import recorder_capture_helpers_cloudlab (upgrade lab_automation)",
            )
        if enabled:
            prof = "teleop" if str(profile).strip().lower() == "teleop" else "default"
            communicator._table_cam_stream_profile[cam_id] = prof  # noqa: SLF001
            registry_cam = _registry_camera_for_recorder_cam(communicator, cam_id)
            if registry_cam is not None:
                ok, msg = registry_cam.stream_on(profile=prof)
            else:
                ok, msg = stream_on_cloudlab(cam_id, profile=prof)
        else:
            communicator._table_cam_stream_profile[cam_id] = "default"  # noqa: SLF001
            registry_cam = _registry_camera_for_recorder_cam(communicator, cam_id)
            if registry_cam is not None:
                ok, msg = registry_cam.stream_off()
            else:
                ok, msg = stream_off_cloudlab(cam_id)
        if not ok:
            communicator._table_cam_streaming[cam_id] = False  # noqa: SLF001
            _set_table_cam_error(communicator, cam_id, msg)
            print(
                f"[REAL LAB] table_cam_live_set cam{cam_id} enabled={enabled} FAILED: {msg}",
                flush=True,
            )
            return False, msg
        parsed = _parse_status_kv(msg)
        registry_cam = _registry_camera_for_recorder_cam(communicator, cam_id)
        if registry_cam is not None:
            _sync_table_cam_flags_from_component(communicator, cam_id, registry_cam)
        else:
            communicator._table_cam_streaming[cam_id] = bool(  # noqa: SLF001
                parsed.get("streaming", enabled)
            )
        _set_table_cam_error(communicator, cam_id, None)
        print(
            f"[REAL LAB] table_cam_live_set cam{cam_id} enabled={enabled} "
            f"profile={communicator._table_cam_stream_profile.get(cam_id, 'default')} OK ({msg})",
            flush=True,
        )
        return True, msg or "ok"


def table_cam_send_vexp(
    communicator: "RealLabCommunicator",
    cam_id: int,
    exposure_s: float,
) -> Tuple[bool, str]:
    """Forward ``VEXP`` to the recorder (continuous exposure while streaming).

    Applies only once the recorder is running in lazy cloudlabs mode AND the
    selected camera reports ``CONNECTED`` (see :meth:`table_cam_connect`).
    """
    if cam_id not in (1, 2):
        return False, "cam_id must be 1 or 2"
    if not communicator._use_cloudlab_table_recorder:
        return True, "legacy recorder ignores vexp shim"
    if not communicator._table_cam_connected.get(cam_id):
        return False, "camera not connected"
    registry_cam = _registry_camera_for_recorder_cam(communicator, cam_id)
    if registry_cam is not None:
        return registry_cam.set_exposure_s(float(exposure_s))
    try:
        from lab_automation.managers.recorder_capture_helpers_cloudlab import (  # noqa: PLC0415
            set_vexp_cloudlab,
        )
    except ImportError:
        return False, "recorder_capture_helpers_cloudlab unavailable"
    ok, msg = set_vexp_cloudlab(cam_id, float(exposure_s))
    if not ok:
        _set_table_cam_error(communicator, cam_id, msg)
        return False, msg
    return True, msg or "ok"


def table_cam_send_vgain(
    communicator: "RealLabCommunicator",
    cam_id: int,
    gain: float,
) -> Tuple[bool, str]:
    if cam_id not in (1, 2):
        return False, "cam_id must be 1 or 2"
    if not communicator._use_cloudlab_table_recorder:
        return True, "legacy recorder ignores vgain shim"
    if not communicator._table_cam_connected.get(cam_id):
        return False, "camera not connected"
    try:
        from lab_automation.managers.recorder_capture_helpers_cloudlab import (  # noqa: PLC0415
            set_vgain_cloudlab,
        )
    except ImportError:
        return False, "recorder_capture_helpers_cloudlab unavailable"
    ok, msg = set_vgain_cloudlab(cam_id, float(gain))
    if not ok:
        _set_table_cam_error(communicator, cam_id, msg)
        return False, msg
    return True, msg or "ok"


def _mjpeg_status_frame(title: str, detail: str = "") -> bytes:
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    frame = np.zeros((360, 480, 3), dtype=np.uint8)
    frame[:] = (18, 18, 24)
    cv2.putText(
        frame,
        title[:40],
        (16, 160),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.62,
        (80, 160, 255),
        2,
    )
    if detail:
        y = 195
        for chunk in detail[:120].split("\n"):
            cv2.putText(
                frame,
                chunk[:56],
                (16, y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.42,
                (180, 180, 200),
                1,
            )
            y += 22
    _, buffer = cv2.imencode(".jpg", frame)
    return buffer.tobytes() if buffer is not None else b""


def get_table_cam_stream(
    communicator: "RealLabCommunicator",
    cam_id: int,
    fps: int = 30,
):
    """MJPEG bytes for ``GET /api/components/{tag_id}/telemetry/stream``."""
    prof = _preview_profile_for_cam(communicator, cam_id)
    cap_fps = 45 if prof == "teleop" else 30
    fps = max(8, min(int(fps), cap_fps))
    if prof == "teleop" and fps < 20:
        fps = 20
    frame_interval = 1.0 / fps
    next_frame_at = time.monotonic()
    timeout_s = _preview_timeout_for_cam(communicator, cam_id)
    allow_placeholder = bool(getattr(communicator, "_table_cam_recorder_mock", False))

    while True:
        frame_bytes = None
        cloud = communicator._use_cloudlab_table_recorder
        streaming = communicator._table_cam_streaming.get(cam_id, False)
        last_err = communicator._table_cam_last_error.get(cam_id)  # noqa: SLF001

        if not _recorder_procs_alive(communicator):
            frame_bytes = _mjpeg_status_frame(
                "RECORDER DEAD",
                last_err or "restart backend; check [REAL LAB][recorder] logs",
            )
        elif not cloud:
            frame_bytes = _mjpeg_status_frame(
                "LEGACY RECORDER",
                "Live MJPEG needs recorder_cam_laser_align_cloudlab.py",
            )
        elif not communicator._table_cam_connected.get(cam_id):
            frame_bytes = _mjpeg_status_frame(
                "NOT CONNECTED",
                last_err or "Click Connected or pick CAM1/CAM2",
            )
        elif not streaming:
            frame_bytes = _mjpeg_status_frame(
                "LIVE OFF",
                "Enable Live (STREAM_ON) after connect",
            )
        else:
            try:
                from lab_automation.managers.recorder_capture_helpers_cloudlab import (  # noqa: PLC0415
                    fetch_preview_jpeg_cloudlab,
                )

                payload = fetch_preview_jpeg_cloudlab(cam_id, timeout_s=timeout_s)
                if payload and len(payload) > 800:
                    frame_bytes = payload
                elif payload and allow_placeholder:
                    frame_bytes = payload
                elif not payload:
                    hw = communicator._table_cam_hardware.get(cam_id, "?")  # noqa: SLF001
                    frame_bytes = _mjpeg_status_frame(
                        "NO FRAME FROM CAMERA",
                        last_err or f"hardware={hw}; check exposure / lens cap",
                    )
            except Exception as e:
                print(f"[REAL LAB] table-cam MJPEG grab failed: {e}", flush=True)
                frame_bytes = _mjpeg_status_frame("STREAM ERROR", str(e))

        if frame_bytes is None:
            frame_bytes = _mjpeg_status_frame("TABLE CAM ERROR", last_err or "unknown")

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n"
            + frame_bytes
            + b"\r\n"
        )

        next_frame_at += frame_interval
        delay = next_frame_at - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        else:
            next_frame_at = time.monotonic()


def fetch_table_cam_preview_jpeg(
    communicator: "RealLabCommunicator",
    cam_id: int,
) -> Optional[bytes]:
    """Latest JPEG for polled live preview (lower latency than browser MJPEG)."""
    if cam_id not in (1, 2):
        return None
    allow_placeholder = bool(getattr(communicator, "_table_cam_recorder_mock", False))

    if not _recorder_procs_alive(communicator):
        return _mjpeg_status_frame(
            "RECORDER DEAD",
            "restart backend",
        )
    if not communicator._use_cloudlab_table_recorder:
        return _mjpeg_status_frame("LEGACY RECORDER", "use cloudlab recorder")
    if not communicator._table_cam_connected.get(cam_id):
        return _mjpeg_status_frame("NOT CONNECTED", "Connect first")
    if not communicator._table_cam_streaming.get(cam_id):
        return _mjpeg_status_frame("LIVE OFF", "Enable Live")

    timeout_s = _preview_timeout_for_cam(communicator, cam_id)
    registry_cam = _registry_camera_for_recorder_cam(communicator, cam_id)
    if registry_cam is not None:
        payload = registry_cam.fetch_preview_jpeg(timeout_s=timeout_s)
        if payload and len(payload) > 800:
            return payload
        if payload and allow_placeholder:
            return payload

    try:
        from lab_automation.managers.recorder_capture_helpers_cloudlab import (  # noqa: PLC0415
            fetch_preview_jpeg_cloudlab,
        )

        payload = fetch_preview_jpeg_cloudlab(cam_id, timeout_s=timeout_s)
        if payload and len(payload) > 800:
            return payload
        if payload and allow_placeholder:
            return payload
        last_err = communicator._table_cam_last_error.get(cam_id)  # noqa: SLF001
        return _mjpeg_status_frame(
            "NO FRAME FROM CAMERA",
            last_err or "check exposure",
        )
    except Exception as e:
        return _mjpeg_status_frame("STREAM ERROR", str(e))


# ---------------------------------------------------------------------------
# Single-shot capture
# ---------------------------------------------------------------------------

def capture_overhead_cam(
    communicator: "RealLabCommunicator",
    exposure: float = 0.2,
) -> Optional[bytes]:
    """Capture one PNG still from the table-overview camera (``tag_99`` registry)."""
    exp = _experiment(communicator)
    if exp is not None and hasattr(exp, "capture_still_for_tag"):
        png = exp.capture_still_for_tag("tag_99", exposure_s=float(exposure))
        if png:
            return png

    registry_cam = _registry_camera_for_tag(communicator, "tag_99")
    if registry_cam is not None:
        return registry_cam.capture_still_png(float(exposure))

    import cv2  # noqa: PLC0415

    _ = float(exposure)
    camera = None
    if exp is not None and hasattr(exp, "ceiling_cam1"):
        camera = exp.ceiling_cam1
    if camera is None:
        print("[REAL LAB] capture_overhead_cam: no tag_99 registry or ceiling_cam1")
        return None
    try:
        frame = camera.get_frame()
    except Exception as exc:
        print(f"[REAL LAB] capture_overhead_cam failed: {exc!r}")
        return None
    if frame is None:
        return None
    ok, buffer = cv2.imencode(".png", frame)
    if not ok:
        return None
    return buffer.tobytes()


def capture_table_cam(
    communicator: "RealLabCommunicator",
    cam_id: int,
    exposure: float = 0.2,
):
    """Capture one image from table recorder camera (1 or 2). Returns PNG bytes or ``None``.

    Returns ``None`` when:
    - the recorder helpers are unavailable (legacy path) **or**
      the camera never completed HTTP ``connect`` while the recorder is in lazy
      ``cloudlab`` mode,
    - ``cam_id`` is not 1 or 2,

    **Cloud recorder** (:attr:`communicator._use_cloudlab_table_recorder`) skips
    the ``REC_ON`` dance: it requires HTTP ``connect`` before capture and pushes
    a ``CAP`` on the lazy recorder path.

    Writes to a temp PNG via the helper APIs because downstream code expects
    bytes; temp files are always removed afterward.
    """
    if cam_id not in (1, 2):
        return None

    exp = float(exposure)
    registry_cam = _registry_camera_for_recorder_cam(communicator, cam_id)
    if registry_cam is not None:
        png = registry_cam.capture_still_png(exp)
        _sync_table_cam_flags_from_component(communicator, cam_id, registry_cam)
        return png

    import cv2  # noqa: PLC0415
    import tempfile
    import os as _os

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        tmp_path = _os.path.abspath(f.name)

    try:
        if getattr(communicator, "_use_cloudlab_table_recorder", False):
            if not communicator._table_cam_connected.get(cam_id):  # noqa: SLF001
                print(
                    f"[REAL LAB] capture_table_cam cam{cam_id} refused "
                    "(not connected in cloudlabs recorder mode)"
                )
                return None
            try:
                from lab_automation.managers.recorder_capture_helpers_cloudlab import (  # noqa: PLC0415
                    request_capture_cloudlab,
                    set_vexp_cloudlab,
                )
            except ImportError:
                print("[REAL LAB] recorder_capture_helpers_cloudlab import failed.")
                return None

            ok_exp, exp_msg = set_vexp_cloudlab(cam_id, exp)
            if not ok_exp:
                print(
                    f"[REAL LAB] capture_table_cam cam{cam_id} VEXP failed: {exp_msg}",
                    flush=True,
                )
                _set_table_cam_error(communicator, cam_id, exp_msg)
                return None
            time.sleep(0.35)
            img, cap_msg = request_capture_cloudlab(
                cam_id, exp, tmp_path, timeout=8.0
            )
            if img is None:
                print(
                    f"[REAL LAB] capture_table_cam cam{cam_id} failed: {cap_msg}",
                    flush=True,
                )
                _set_table_cam_error(communicator, cam_id, cap_msg)
                return None
            _set_table_cam_error(communicator, cam_id, None)
            _, buf = cv2.imencode(".png", img)
            return buf.tobytes()

        from lab_communicator.real import communicator as _real_comm  # noqa: PLC0415

        if (
            not _real_comm.RECORDER_CAPTURE_AVAILABLE
            or _real_comm.activate_cam_and_capture is None
        ):
            return None

        img = _real_comm.activate_cam_and_capture(
            cam_id=cam_id,
            video_exposure=exp,
            capture_exposure=exp,
            filename=tmp_path,
            settle_s=0.5,
            output_dir=None,
        )
        if img is None:
            return None
        _, buf = cv2.imencode(".png", img)
        return buf.tobytes()
    finally:
        if _os.path.exists(tmp_path):
            try:
                _os.remove(tmp_path)
            except Exception:
                pass
