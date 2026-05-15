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
  ``OPTICAL_CAMERA``-typed components for ``observe_measurables_for_tag``.

All helpers take the ``RealLabCommunicator`` instance explicitly. The
class keeps thin ``def`` wrappers so the dispatch layer
(``lab_primitives``) and Cloud-Labs HTTP routes keep their existing
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
import time
from typing import TYPE_CHECKING, Tuple


if TYPE_CHECKING:
    from lab_communicator.real.communicator import RealLabCommunicator


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

    use_real_camera = (
        os.getenv("TABLE_CAM_USE_MOCK", "").strip().lower()
        not in ("1", "true", "yes")
    )
    extra = ["--real-camera"] if use_real_camera else []

    cloudlab_spawn = communicator._use_cloudlab_table_recorder  # noqa: SLF001
    try:
        p1 = subprocess.Popen(
            [
                sys.executable,
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
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        p2 = subprocess.Popen(
            [
                sys.executable,
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
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        communicator._recorder_procs = [p1, p2]
        time.sleep(0.35 if cloudlab_spawn else 1.2)
        mode = (
            "cloudlabs (lazy CONNECT)"
            if cloudlab_spawn
            else "legacy (camera open at recorder start)"
        )
        print(
            f"[REAL LAB] Recorder processes started ({mode}) cam1=9999, cam2=10000 "
            f"script={os.path.basename(recorder_script)}."
        )
        atexit.register(communicator._shutdown_recorders)
    except Exception as e:
        print(f"[REAL LAB] Failed to start recorders: {e}")
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
    if communicator.experiment and hasattr(communicator.experiment, "ceiling_cam1"):
        camera = communicator.experiment.ceiling_cam1

    sleep_duration = 1.0 / max(1, min(fps, 60))

    while True:
        frame = None
        if camera:
            try:
                frame = camera.get_frame()
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

def table_cam_connect(
    communicator: "RealLabCommunicator",
    cam_id: int,
) -> Tuple[bool, str]:
    if cam_id not in (1, 2):
        return False, "cam_id must be 1 or 2"
    with communicator._table_cam_lock:
        if not getattr(communicator, "_recorder_procs", []):
            return False, "recorder subprocesses are not running"
        if not communicator._use_cloudlab_table_recorder:
            communicator._table_cam_connected[cam_id] = True  # noqa: SLF001
            return True, "legacy recorder: hardware already owns the camera session"
        try:
            from lab_automation.managers.recorder_capture_helpers_cloudlab import (  # noqa: PLC0415
                connect_cam_cloudlab,
            )
        except ImportError:
            return (
                False,
                "cannot import recorder_capture_helpers_cloudlab (upgrade lab_automation)",
            )

        connect_cam_cloudlab(cam_id)
        # Give the recorder a moment to negotiate mvsdk.
        time.sleep(0.3)
        communicator._table_cam_connected[cam_id] = True  # noqa: SLF001
        return True, "ok"


def table_cam_disconnect(
    communicator: "RealLabCommunicator",
    cam_id: int,
) -> Tuple[bool, str]:
    if cam_id not in (1, 2):
        return False, "cam_id must be 1 or 2"
    with communicator._table_cam_lock:
        if not communicator._use_cloudlab_table_recorder:
            return (
                False,
                "full SDK release/disconnect requires recorder_cam_laser_align_cloudlab.py",
            )

        communicator._table_cam_streaming[cam_id] = False  # noqa: SLF001
        communicator._table_cam_connected[cam_id] = False  # noqa: SLF001
        try:
            from lab_automation.managers.recorder_capture_helpers_cloudlab import (  # noqa: PLC0415
                disconnect_cam_cloudlab,
            )

            disconnect_cam_cloudlab(cam_id)
        except ImportError:
            return (
                False,
                "cannot import recorder_capture_helpers_cloudlab (upgrade lab_automation)",
            )
        time.sleep(0.05)
        return True, "ok"


def table_cam_live_set(
    communicator: "RealLabCommunicator",
    cam_id: int,
    enabled: bool,
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
            stream_on_cloudlab(cam_id)
            communicator._table_cam_streaming[cam_id] = True  # noqa: SLF001
        else:
            stream_off_cloudlab(cam_id)
            communicator._table_cam_streaming[cam_id] = False  # noqa: SLF001
        time.sleep(0.03)
        return True, "ok"


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
    try:
        from lab_automation.managers.recorder_capture_helpers_cloudlab import (  # noqa: PLC0415
            set_vexp_cloudlab,
        )
    except ImportError:
        return False, "recorder_capture_helpers_cloudlab unavailable"
    set_vexp_cloudlab(cam_id, float(exposure_s))
    return True, "ok"


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
    set_vgain_cloudlab(cam_id, float(gain))
    return True, "ok"


def get_table_cam_stream(
    communicator: "RealLabCommunicator",
    cam_id: int,
    fps: int = 18,
):
    """MJPEG bytes for ``GET /api/table-cam/stream`` (cloudlabs recorder)."""
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    fps = max(4, min(fps, 40))
    sleep_duration = 1.0 / fps

    label = ""

    while True:
        frame_bytes = None
        cloud = communicator._use_cloudlab_table_recorder
        streaming = communicator._table_cam_streaming.get(cam_id, False)

        if not cloud:
            label = "LEGACY recorder (no MJPEG shim)"
            frame = np.zeros((360, 480, 3), dtype=np.uint8)
            cv2.putText(
                frame,
                "Live preview unavailable",
                (30, 150),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (200, 200, 200),
                1,
            )
            cv2.putText(
                frame,
                "Use cloud recorder or Capture",
                (30, 180),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (160, 160, 180),
                1,
            )
            _, buffer = cv2.imencode(".jpg", frame)
            frame_bytes = buffer.tobytes()
        elif not communicator._table_cam_connected.get(cam_id):
            label = "disconnected"
            frame = np.zeros((360, 480, 3), dtype=np.uint8)
            cv2.putText(
                frame,
                "Table cam disconnected",
                (36, 180),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (200, 200, 200),
                2,
            )
            _, buffer = cv2.imencode(".jpg", frame)
            frame_bytes = buffer.tobytes()
        elif not streaming:
            label = "live paused"
            frame = np.zeros((360, 480, 3), dtype=np.uint8)
            cv2.putText(
                frame,
                "Live paused (STREAM_OFF)",
                (40, 180),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.66,
                (230, 200, 150),
                2,
            )
            _, buffer = cv2.imencode(".jpg", frame)
            frame_bytes = buffer.tobytes()
        else:
            try:
                from lab_automation.managers.recorder_capture_helpers_cloudlab import (  # noqa: PLC0415
                    fetch_preview_jpeg_cloudlab,
                )

                payload = fetch_preview_jpeg_cloudlab(cam_id, timeout_s=1.75)
                if payload:
                    frame_bytes = payload
            except Exception as e:
                print(f"[REAL LAB] table-cam MJPEG grab failed: {e}")

        if frame_bytes is None:
            frame = np.zeros((360, 480, 3), dtype=np.uint8)
            cv2.putText(
                frame,
                "WAITING FOR FRAME",
                (60, 180),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.67,
                (60, 200, 255),
                2,
            )
            if label:
                cv2.putText(
                    frame,
                    label[:48],
                    (20, 220),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.45,
                    (120, 120, 140),
                    1,
                )
            ret, buffer = cv2.imencode(".jpg", frame)
            frame_bytes = buffer.tobytes() if ret else b""

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n"
            + frame_bytes
            + b"\r\n"
        )

        time.sleep(sleep_duration)


# ---------------------------------------------------------------------------
# Single-shot capture
# ---------------------------------------------------------------------------

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

            settle_s = 0.5
            set_vexp_cloudlab(cam_id, exp)
            time.sleep(settle_s)
            img = request_capture_cloudlab(cam_id, exp, tmp_path, timeout=6.5)
            if img is None:
                return None
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
