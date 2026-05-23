"""Real-backend optimization-run plumbing.

Three sub-areas, all tied to the COBYLA / Newton optimizers in
``lab_automation.objects.strategies``:

* **Run directory + image bookkeeping** -- per-run subdirectories
  under ``Camera_Images``, the file watcher that drives
  ``current_state['optimization_step']``, and the helpers the video
  stream uses to find the most recent PNG.
  Functions: :func:`make_optimization_run_dir`,
  :func:`apply_optimization_output_dir_kw`,
  :func:`get_optimization_watch_dirs`,
  :func:`get_latest_optimization_png`,
  :func:`optimization_step_from_image_path`,
  :func:`monitor_optimization_dir`.

* **Cobyla reference image cache** -- the operator can upload a
  reference image (PNG bytes) before kicking off a Cobyla alignment;
  this module stores it, provides a status probe, and re-encodes it
  Phase 9d: COBYLA reads the reference from ``measurables.camera_image`` on
  the catalog camera tag (via :func:`load_cobyla_reference_bgr_from_state`).

* **Cloudlab Newton place-UI hook** -- ``NewtonPlacementStrategy_cloudlab``
  exposes a ``progress_callback`` so cloud-labs can update the canvas
  ghost / physical poses as the strategy iterates. We install/remove
  the wrapper around ``OpticalExperiment.place_component_wo_home_specific_xy_cloudlab``
  to drive that update path. Functions: :func:`cloudlab_progress_callback`,
  :func:`install_cloudlab_place_ui_hook`,
  :func:`remove_cloudlab_place_ui_hook`.

All helpers take the ``RealLabCommunicator`` instance explicitly. The
class keeps thin ``def`` wrappers so external call sites
(``optimize_component``, the Cloud-Labs HTTP routes for the cobyla
reference image, the video stream's ``_get_latest_optimization_png``
caller) keep their existing API. Phase 2 (see
``communicator_refactor.md`` §6) will fold the optimization run logic
into a ``_primitive_optimize_component`` hook driven by a ``progress_callback``
constructed by the base orchestrator.

Architectural rule (``communicator_refactor.md`` §5.1): real-only --
free to import lab_automation strategy types via duck typing on the
communicator's ``experiment``. Must NOT import the mock backend or
:mod:`lab_communicator.base`.
"""

from __future__ import annotations

import glob
import inspect
import os
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple


if TYPE_CHECKING:
    from lab_communicator.real.communicator import RealLabCommunicator


# ---------------------------------------------------------------------------
# Run directory + image bookkeeping
# ---------------------------------------------------------------------------

def make_optimization_run_dir(
    communicator: "RealLabCommunicator", strategy_name: str
) -> str:
    """Create a per-run ``Camera_Images/opt_<ts>_<strategy>/`` subdirectory.

    Successive optimization runs go in fresh folders so PNGs don't
    overwrite. ``strategy_name`` is sanitized (alnum/dot/dash/underscore
    only, capped at 48 chars) before being used in the folder name.
    Returns the absolute path; the directory exists when this returns.
    """
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", (strategy_name or "OPT").strip())
    safe = safe.strip("_")[:48] or "OPT"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder = f"opt_{ts}_{safe}"
    path = os.path.join(communicator._camera_images_base_dir(), folder)
    os.makedirs(path, exist_ok=True)
    print(f"[REAL LAB] Optimization run image directory: {path}")
    return path


def apply_optimization_output_dir_kw(
    strategy_cls: Any, kw: Dict[str, Any], run_dir: str
) -> None:
    """Inject ``run_dir`` into the strategy kwargs under whatever name it accepts.

    Different lab_automation strategies name the output-dir parameter
    differently (``output_dir``, ``camera_images_dir``, ``save_dir``,
    ``image_output_dir``). We introspect ``__init__`` and use the
    first matching name. Logs a warning when no recognized parameter
    exists -- the strategy will then write to the flat ``Camera_Images``
    dir, which means the optimization step counter and stream will
    still work, just without per-run isolation.
    """
    try:
        sig = inspect.signature(strategy_cls.__init__)
    except (TypeError, ValueError):
        return
    for param_name in (
        "output_dir", "camera_images_dir", "save_dir", "image_output_dir",
    ):
        if param_name in sig.parameters:
            kw[param_name] = run_dir
            print(f"[REAL LAB] {strategy_cls.__name__}: {param_name}={run_dir}")
            return
    print(
        f"[REAL LAB] Warning: "
        f"{getattr(strategy_cls, '__name__', strategy_cls)} has no "
        f"output_dir-like parameter; images may still write to the flat "
        f"Camera_Images folder. See update_lab.md in optics-digital-twin repo."
    )


def get_optimization_watch_dirs(communicator: "RealLabCommunicator") -> List[str]:
    """Directories the file watcher / video stream should poll for new PNGs.

    During an active run, restrict to the run subdir so the step
    counter and MJPEG feed track that run. Otherwise watch both the
    backend-CWD ``Camera_Images`` dir and the
    ``$LAB_AUTOMATION_PATH/Camera_Images`` dir (some strategies write
    relative to their own CWD).
    """
    active = getattr(communicator, "_active_optimization_image_dir", None)
    if active and os.path.isdir(active):
        return [active]
    candidates = {os.path.abspath("Camera_Images")}
    try:
        from lab_communicator.shared.lab_view_config import get_lab_automation_path  # noqa: PLC0415

        lab_path = get_lab_automation_path()
    except Exception:
        lab_path = os.getenv("LAB_AUTOMATION_PATH")
    if lab_path:
        candidates.add(os.path.join(os.path.abspath(lab_path), "Camera_Images"))
    return sorted(candidates)


def get_latest_optimization_png(
    communicator: "RealLabCommunicator",
) -> Tuple[Optional[str], int]:
    """Find the most recently modified image under the watch dirs.

    Returns ``(path_or_None, mtime_ns_or_0)``. Considers ``*.png``,
    ``*.jpg``, and ``*.jpeg`` (Newton/vision paths have used both
    formats historically). Best-effort -- ``stat`` failures on
    individual files are silently skipped.
    """
    latest_file: Optional[str] = None
    latest_ns: int = 0

    for d in get_optimization_watch_dirs(communicator):
        if not os.path.exists(d):
            continue
        try:
            files: List[str] = []
            files.extend(glob.glob(os.path.join(d, "*.png")))
            files.extend(glob.glob(os.path.join(d, "*.jpg")))
            files.extend(glob.glob(os.path.join(d, "*.jpeg")))

            for f in files:
                try:
                    ns = os.stat(f).st_mtime_ns
                except Exception:
                    continue
                if ns > latest_ns:
                    latest_ns = ns
                    latest_file = f
        except Exception:
            continue

    return latest_file, latest_ns


def optimization_step_from_image_path(path: str) -> Optional[int]:
    """Parse ``stepNN`` from a PNG basename. Returns ``None`` when absent.

    Strategies that name their PNGs ``test_step02.png`` etc. provide a
    canonical step index this way; the file watcher prefers parsed
    indices over basename-changed-counting because writers often touch
    the same file twice (mtime+size both change), which previously
    doubled the increment.
    """
    m = re.search(r"(?i)step(\d+)", os.path.basename(path))
    if not m:
        return None
    return int(m.group(1), 10)


def monitor_optimization_dir(communicator: "RealLabCommunicator") -> None:
    """Background thread loop -- watches the latest PNG and bumps ``optimization_step``.

    Started once during ``RealLabCommunicator.__init__``. Polls every
    0.5 s. While ``system_status == "OPTIMIZING"``:

    1. Prefer the parsed ``stepNN`` index from the filename when
       available.
    2. Otherwise count distinct filenames seen.

    When NOT optimizing, still tracks the latest mtime/size/path so a
    new run doesn't trigger a stale "step jumped" event on first
    iteration.
    """
    import time  # local -- the thread may outlive normal imports

    latest_file, last_mtime_ns = get_latest_optimization_png(communicator)
    last_size = -1
    last_path = latest_file
    try:
        if latest_file:
            last_size = os.path.getsize(latest_file)
    except Exception:
        last_size = -1

    while True:
        time.sleep(0.5)
        if communicator.current_state.get("system_status") == "OPTIMIZING":
            try:
                current_file, current_ns = get_latest_optimization_png(communicator)
                if not current_file:
                    continue

                try:
                    current_size = os.path.getsize(current_file)
                except Exception:
                    current_size = -1

                # Prefer step index parsed from filename. Writers often
                # touch the same file twice (mtime + size), which
                # previously doubled increments (0->2->4...).
                parsed = optimization_step_from_image_path(current_file)
                if parsed is not None:
                    with communicator._state_lock:
                        if (
                            communicator.current_state.get("system_status")
                            != "OPTIMIZING"
                        ):
                            pass
                        else:
                            prev = communicator.current_state.get("optimization_step")
                            if parsed != prev:
                                communicator.current_state["optimization_step"] = parsed
                                print(
                                    f"[REAL LAB] optimization_step={parsed} "
                                    f"(from file={os.path.basename(current_file)})"
                                )
                else:
                    basename = os.path.basename(current_file)
                    with communicator._state_lock:
                        if (
                            communicator.current_state.get("system_status")
                            != "OPTIMIZING"
                        ):
                            pass
                        elif basename != communicator._last_optimization_image_basename:
                            communicator._last_optimization_image_basename = basename
                            current_step = communicator.current_state.get(
                                "optimization_step", 0
                            )
                            communicator.current_state["optimization_step"] = (
                                current_step + 1
                            )
                            print(
                                f"[REAL LAB] optimization_step={current_step + 1} "
                                f"(new image basename={basename} "
                                f"ns={current_ns} size={current_size})"
                            )

                last_mtime_ns = current_ns
                last_size = current_size
                last_path = current_file
            except Exception:
                pass
        else:
            # Keep last_mtime updated even when not optimizing so a new
            # run doesn't trigger a stale jump on first iteration.
            try:
                current_file, current_ns = get_latest_optimization_png(communicator)
                if current_file:
                    try:
                        current_size = os.path.getsize(current_file)
                    except Exception:
                        current_size = -1
                    if (
                        current_ns > last_mtime_ns
                        or current_size != last_size
                        or current_file != last_path
                    ):
                        last_mtime_ns = current_ns
                        last_size = current_size
                        last_path = current_file
            except Exception:
                pass


# ---------------------------------------------------------------------------
# COBYLA reference from recorded measurables (Phase 9d)
# ---------------------------------------------------------------------------

def _decode_png_bytes_to_bgr(data: bytes) -> Optional[Any]:
    """Decode PNG bytes to a BGR ``numpy`` array, or ``None`` on failure."""
    if not data or len(data) < 8:
        return None
    try:
        import cv2
    except ImportError:
        return None
    import numpy as np

    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_UNCHANGED)
    if img is None:
        return None
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    elif img.ndim == 3 and img.shape[2] == 4:
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    if img.ndim != 3 or img.shape[2] != 3:
        return None
    return img


def load_cobyla_reference_bgr_from_state(
    communicator: "RealLabCommunicator",
    *,
    camera_number: int = 1,
) -> Optional[Any]:
    """Load the COBYLA reference BGR image from ``measurables.camera_image``.

    Phase 9d replaces the legacy side-channel ``_cobyla_reference_bgr``
    cache. The operator workflow is:

      1. ``RECORD_MEASURABLES`` on the gripper camera (e.g. ``tag_22``).
      2. ``OPTIMIZE`` with strategy COBYLA and matching ``camera_number``.

    Returns ``None`` when no suitable recorded image exists (strategy
    may fall back to its own default).
    """
    from lab_communicator.shared.catalog_schema import find_tag_id_for_cam_id

    cam_id = int(camera_number)
    tag_id = find_tag_id_for_cam_id(communicator.catalog_map or {}, cam_id)
    if not tag_id:
        print(
            f"[REAL LAB] COBYLA: no catalog tag for camera_number={cam_id}"
        )
        return None

    with communicator._state_lock:
        entry = (communicator.current_state.get("components") or {}).get(tag_id)
    if not isinstance(entry, dict):
        print(f"[REAL LAB] COBYLA: tag {tag_id!r} not in lab state")
        return None

    ci = (entry.get("measurables") or {}).get("camera_image")
    if not isinstance(ci, dict):
        print(
            f"[REAL LAB] COBYLA: no measurables.camera_image on {tag_id!r} "
            f"(run RECORD_MEASURABLES on that camera first)"
        )
        return None

    path = ci.get("path")
    if not isinstance(path, str) or not path:
        print(f"[REAL LAB] COBYLA: camera_image.path empty on {tag_id!r}")
        return None

    abs_path = os.path.abspath(path)
    if not os.path.isfile(abs_path):
        print(f"[REAL LAB] COBYLA: camera image file missing: {abs_path}")
        return None

    try:
        with open(abs_path, "rb") as f:
            data = f.read()
    except OSError as e:
        print(f"[REAL LAB] COBYLA: could not read {abs_path}: {e}")
        return None

    img = _decode_png_bytes_to_bgr(data)
    if img is None:
        print(f"[REAL LAB] COBYLA: could not decode reference at {abs_path}")
        return None

    h, w = img.shape[:2]
    print(
        f"[REAL LAB] COBYLA reference from {tag_id!r} measurables.camera_image "
        f"({w}x{h} BGR)"
    )
    return img


# ---------------------------------------------------------------------------
# Cloudlab Newton place-UI hook
# ---------------------------------------------------------------------------

def cloudlab_progress_callback(
    communicator: "RealLabCommunicator", target_tag_id: str
):
    """Build the ``progress_callback`` for ``NewtonPlacementStrategy_cloudlab``.

    The strategy calls back with ``(phase, component, target_x,
    target_y, angle=None, step=None)`` for each Newton sub-move.
    ``phase`` is ``"ghost"`` (planned target) or ``"physical"`` (after
    a successful sub-place). We translate the component back to its
    cloud-labs tag and update ``measurables.pose`` /
    ``tunables.nominal_pose`` accordingly so the UI's canvas tracks
    each iteration.

    The closure captures ``target_tag_id`` so callbacks fired for any
    *other* component (the strategy may bounce around) are silently
    ignored.
    """

    def _cb(
        phase: str,
        component: Any,
        target_x: float,
        target_y: float,
        angle: Any = None,  # noqa: ARG001
        step: Any = None,  # noqa: ARG001
    ) -> None:
        tid = communicator._tag_id_for_component(component)
        if tid != target_tag_id:
            return
        pose = communicator._ui_pose_for_placement_tick(tid, target_x, target_y)
        communicator._apply_placement_ui_phase(tid, phase, pose)

    return _cb


def install_cloudlab_place_ui_hook(
    communicator: "RealLabCommunicator", target_tag_id: str
) -> None:
    """Wrap ``place_component_wo_home_specific_xy_cloudlab`` so the UI sees ghost+physical updates.

    Idempotent: a second call is a no-op as long as the original is
    still saved on the communicator. The wrapper introspects the
    method's signature so it works even when lab_automation versions
    add/remove parameters.

    Failure modes (each logs and returns without raising):
    - the experiment doesn't expose the method at all,
    - signature introspection fails,
    - the original raises (re-raised after we skip the "physical"
      update -- the caller's normal error path takes over).
    """
    exp = communicator.experiment
    if not hasattr(exp, "place_component_wo_home_specific_xy_cloudlab"):
        print(
            "[REAL LAB] No place_component_wo_home_specific_xy_cloudlab on "
            "experiment; UI hook skipped."
        )
        return
    if communicator._place_cloudlab_orig is not None:
        return
    orig = exp.place_component_wo_home_specific_xy_cloudlab
    communicator._place_cloudlab_orig = orig
    comm = communicator

    try:
        sig = inspect.signature(orig)
    except (TypeError, ValueError):
        sig = None

    def wrapped(*args, **kwargs):
        component = target_x = target_y = None
        if sig is not None:
            try:
                ba = sig.bind_partial(*args, **kwargs)
                ba.apply_defaults()
                component = ba.arguments.get("component")
                target_x = ba.arguments.get("target_x")
                target_y = ba.arguments.get("target_y")
            except TypeError:
                pass
        tid = (
            comm._tag_id_for_component(component)
            if component is not None
            else None
        )
        pose = None
        if tid == target_tag_id and target_x is not None and target_y is not None:
            pose = comm._ui_pose_for_placement_tick(tid, target_x, target_y)
            comm._apply_placement_ui_phase(tid, "ghost", pose)
        try:
            return orig(*args, **kwargs)
        except Exception:
            raise
        else:
            if pose is not None and tid == target_tag_id:
                comm._apply_placement_ui_phase(tid, "physical", pose)

    exp.place_component_wo_home_specific_xy_cloudlab = wrapped  # type: ignore[method-assign]
    print(
        "[REAL LAB] Installed place_component_wo_home_specific_xy_cloudlab "
        "UI hook for Newton."
    )


def remove_cloudlab_place_ui_hook(communicator: "RealLabCommunicator") -> None:
    """Restore the original ``place_component_wo_home_specific_xy_cloudlab``.

    No-op when no hook is currently installed. Idempotent so multiple
    cleanup paths can call it without coordination.
    """
    if communicator._place_cloudlab_orig is None:
        return
    if hasattr(communicator.experiment, "place_component_wo_home_specific_xy_cloudlab"):
        communicator.experiment.place_component_wo_home_specific_xy_cloudlab = (
            communicator._place_cloudlab_orig
        )
    communicator._place_cloudlab_orig = None
