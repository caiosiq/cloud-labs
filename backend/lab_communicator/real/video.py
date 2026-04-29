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
from typing import TYPE_CHECKING


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
    """Spawn the cam1/cam2 recorder subprocesses and warm up the table cams.

    Looks for ``recorder_cam_laser_align_simplified.py`` under
    ``$LAB_AUTOMATION_PATH`` (root or ``scripts/`` subdir). When the
    script can't be found, table-cam capture will fail later; we log a
    warning and return without populating ``_recorder_procs``.

    Honours ``$TABLE_CAM_USE_MOCK`` (set to truthy to drive the recorder
    in mock-camera mode). On success, registers
    ``communicator._shutdown_recorders`` with ``atexit`` so the
    subprocesses are torn down cleanly when the backend exits.
    """
    lab_path = os.getenv("LAB_AUTOMATION_PATH")
    if not lab_path or not os.path.isdir(lab_path):
        print(
            "[REAL LAB] LAB_AUTOMATION_PATH not set or invalid; "
            "skipping recorder warm-up."
        )
        return
    recorder_script = os.path.join(lab_path, "recorder_cam_laser_align_simplified.py")
    if not os.path.isfile(recorder_script):
        recorder_script = os.path.join(
            lab_path, "scripts", "recorder_cam_laser_align_simplified.py"
        )
    if not os.path.isfile(recorder_script):
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
    try:
        p1 = subprocess.Popen(
            [sys.executable, recorder_script,
             "--cam", "0", "--port", "9999", "--prefix", "cam1"] + extra,
            cwd=lab_path,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        p2 = subprocess.Popen(
            [sys.executable, recorder_script,
             "--cam", "1", "--port", "10000", "--prefix", "cam2"] + extra,
            cwd=lab_path,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        communicator._recorder_procs = [p1, p2]
        time.sleep(1.2)
        print(
            "[REAL LAB] Recorder processes started "
            "(cam1=9999, cam2=10000). Table cam capture ready."
        )
        # Register the bound thin wrapper so shutdown survives the Phase
        # 1 indirection -- atexit will look up ``_shutdown_recorders``
        # on the instance at call time.
        atexit.register(communicator._shutdown_recorders)
    except Exception as e:
        print(f"[REAL LAB] Failed to start recorders: {e}")
        communicator._recorder_procs = []


def shutdown_recorders(communicator: "RealLabCommunicator") -> None:
    """Send ``REC_OFF`` + ``EXIT`` to recorder ports and reap subprocesses.

    Idempotent: a second call is a no-op once ``_recorder_procs`` is
    empty. Each subprocess gets a 2 second grace period to exit on its
    own; if it overstays we ``terminate()`` (best-effort -- if even that
    fails we drop the handle and move on, the backend is exiting
    anyway).
    """
    if not communicator._recorder_procs:
        return
    for port in (9999, 10000):
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
# Single-shot capture
# ---------------------------------------------------------------------------

def capture_table_cam(
    communicator: "RealLabCommunicator",
    cam_id: int,
    exposure: float = 0.2,
):
    """Capture one image from table recorder camera (1 or 2). Returns PNG bytes or ``None``.

    Returns ``None`` when:
    - the recorder-capture helper is unavailable in this lab_automation
      version (``RECORDER_CAPTURE_AVAILABLE`` is False),
    - ``cam_id`` is not 1 or 2,
    - the capture itself returns ``None`` (camera not warm, exposure
      bad, etc.).

    Writes to a temp PNG and decodes via cv2 because that's the shape
    of the existing ``activate_cam_and_capture`` API. The temp file is
    always removed afterwards.
    """
    # Imported here (not at module top) so this file is importable on
    # machines without lab_automation -- only the *call* fails when the
    # helper is unavailable, not the whole import.
    from lab_communicator.real import communicator as _real_comm  # noqa: PLC0415
    if (
        not _real_comm.RECORDER_CAPTURE_AVAILABLE
        or _real_comm.activate_cam_and_capture is None
    ):
        return None
    if cam_id not in (1, 2):
        return None
    import cv2
    import tempfile
    import os as _os
    exp = float(exposure)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        tmp_path = f.name
    try:
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
