import atexit
import inspect
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
from scipy.spatial.transform import Rotation as R

from .base import LabCommunicator

# Configuration for External Lab Automation Library
# LAB_AUTOMATION_PATH = path to the lab_automation package folder (repo root).
# For "from lab_automation.managers..." we must add its parent to sys.path so the package name resolves.
LAB_AUTOMATION_PATH = os.getenv("LAB_AUTOMATION_PATH")
if LAB_AUTOMATION_PATH and os.path.exists(LAB_AUTOMATION_PATH):
    _lab_parent = os.path.dirname(LAB_AUTOMATION_PATH)
    if _lab_parent not in sys.path:
        sys.path.insert(0, _lab_parent)
    print(f"[REAL LAB] Added parent {_lab_parent} to sys.path (package lab_automation at {LAB_AUTOMATION_PATH})")
else:
    print("[REAL LAB] Warning: LAB_AUTOMATION_PATH not set or invalid.")

# Import Real Lab Automation
try:
    from lab_automation.managers.experiment_manager import OpticalExperiment
    from lab_automation.objects.base import OpticalComponent, Pose
    from lab_automation.objects.strategies import NewtonPlacementStrategy_cloudlab, CobylaAlignmentStrategy
    LAB_LIB_AVAILABLE = True
except ImportError as e:
    print(f"[REAL LAB] Critical Error: Failed to import lab_automation: {e}")
    LAB_LIB_AVAILABLE = False

try:
    from lab_automation.managers.recorder_capture_helpers import activate_cam_and_capture
    RECORDER_CAPTURE_AVAILABLE = True
except ImportError:
    activate_cam_and_capture = None
    RECORDER_CAPTURE_AVAILABLE = False

# --- Lab vs robot table XY (see coordinate_rotation.md in repo root) ---
# UI and overhead-camera geometry use "lab" axes. The robot table frame is rotated by a small angle.
# Calibrated: motion that is a straight line in the lab (e.g. +100 mm along lab Y) decomposes in robot
# coordinates as approximately Δx_robot = +2.5 mm and Δy_robot = +100 mm (same sign convention as your
# robot axes). That implies sin(θ) ≈ −2.5/100 for the lab→robot rotation below → θ = atan2(-2.5, 100).
# Refine by changing this constant after re-measurement.
LAB_ROBOT_TABLE_ROTATION_RAD: float = math.atan2(-2.66, 100.0)


def lab_table_xy_to_robot_xy(x_lab: float, y_lab: float) -> Tuple[float, float]:
    """Map UI / lab table mm to robot controller table mm before place/move calls."""
    t = LAB_ROBOT_TABLE_ROTATION_RAD
    c, s = math.cos(t), math.sin(t)
    return (c * x_lab - s * y_lab, s * x_lab + c * y_lab)


def robot_table_xy_to_lab_xy(x_robot: float, y_robot: float) -> Tuple[float, float]:
    """Map robot-reported table mm to lab / UI mm (inverse of lab_table_xy_to_robot_xy)."""
    t = LAB_ROBOT_TABLE_ROTATION_RAD
    c, s = math.cos(t), math.sin(t)
    return (c * x_robot + s * y_robot, -s * x_robot + c * y_robot)


class RealLabCommunicator(LabCommunicator):
    def __init__(self):
        if not LAB_LIB_AVAILABLE:
            raise RuntimeError("lab_automation library not available. Cannot start RealLabCommunicator.")

        print("[REAL LAB] Initializing OpticalExperiment...")
        # Initialize the experiment manager
        self.experiment = OpticalExperiment(mock=False)
        self.experiment.initialize_robot()
        
        # Cache of OpticalComponent objects: { "tag_22": OpticalComponent(...) }
        self.component_map: Dict[str, OpticalComponent] = {}
        
        # Initialize State
        self.current_state = {
            "system_status": "IDLE",
            "last_updated": datetime.now().isoformat(),
            "components": {},
            "optimization_step": 0
        }
        self._state_lock = threading.RLock()
        self._place_cloudlab_orig: Any = None

        # CobylaAlignmentStrategy.reference_image (BGR ndarray, same family as table-cam / capture_image)
        self._cobyla_ref_lock = threading.Lock()
        self._cobyla_reference_bgr: Optional[Any] = None  # np.ndarray when set

        self._initialize_state()
        self._recorder_procs: List[subprocess.Popen] = []
        self._start_recorder_processes()
        # Fallback when image names have no stepNN: count once per new basename (avoids double bumps on mtime+size).
        self._last_optimization_image_basename: Optional[str] = None

        # Start a background thread to monitor optimization steps reliably
        self._opt_monitor_thread = threading.Thread(target=self._monitor_optimization_dir, daemon=True)
        self._opt_monitor_thread.start()

    def _get_optimization_watch_dirs(self) -> List[str]:
        """
        Optimization images may be written from different working directories
        (cloud-labs process vs lab_automation process). Watch both to avoid stale frames.
        """
        candidates = {os.path.abspath("Camera_Images")}
        lab_path = os.getenv("LAB_AUTOMATION_PATH")
        if lab_path:
            candidates.add(os.path.join(os.path.abspath(lab_path), "Camera_Images"))
        # Keep deterministic ordering
        return sorted(candidates)

    def _get_latest_optimization_png(self) -> Tuple[Optional[str], int]:
        """
        Returns (latest_image_path, latest_mtime_ns). latest_mtime_ns is 0 when none found.
        """
        import glob

        latest_file: Optional[str] = None
        latest_ns: int = 0

        for d in self._get_optimization_watch_dirs():
            if not os.path.exists(d):
                continue
            try:
                # Newton/vision paths have been used with both png/jpg historically.
                files = []
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

    @staticmethod
    def _optimization_step_from_image_path(path: str) -> Optional[int]:
        """If basename contains step<digits> (e.g. test_step00.png), return that index; else None."""
        m = re.search(r"(?i)step(\d+)", os.path.basename(path))
        if not m:
            return None
        return int(m.group(1), 10)

    def _monitor_optimization_dir(self):
        """Background task to watch optimization PNGs and increment optimization_step when files change."""
        import time

        latest_file, last_mtime_ns = self._get_latest_optimization_png()
        last_size = -1
        last_path = latest_file
        try:
            if latest_file:
                last_size = os.path.getsize(latest_file)
        except Exception:
            last_size = -1

        while True:
            time.sleep(0.5)
            if self.current_state.get("system_status") == "OPTIMIZING":
                try:
                    current_file, current_ns = self._get_latest_optimization_png()
                    if not current_file:
                        continue

                    try:
                        current_size = os.path.getsize(current_file)
                    except Exception:
                        current_size = -1

                    # Prefer step index from filename (e.g. test_step02.png -> 2). Writers often touch the same
                    # file twice (mtime + size), which previously doubled increments (0->2->4...).
                    parsed = self._optimization_step_from_image_path(current_file)
                    if parsed is not None:
                        with self._state_lock:
                            if self.current_state.get("system_status") != "OPTIMIZING":
                                pass
                            else:
                                prev = self.current_state.get("optimization_step")
                                if parsed != prev:
                                    self.current_state["optimization_step"] = parsed
                                    print(
                                        f"[REAL LAB] optimization_step={parsed} "
                                        f"(from file={os.path.basename(current_file)})"
                                    )
                    else:
                        basename = os.path.basename(current_file)
                        with self._state_lock:
                            if self.current_state.get("system_status") != "OPTIMIZING":
                                pass
                            elif basename != self._last_optimization_image_basename:
                                self._last_optimization_image_basename = basename
                                current_step = self.current_state.get("optimization_step", 0)
                                self.current_state["optimization_step"] = current_step + 1
                                print(
                                    f"[REAL LAB] optimization_step={current_step + 1} "
                                    f"(new image basename={basename} ns={current_ns} size={current_size})"
                                )

                    last_mtime_ns = current_ns
                    last_size = current_size
                    last_path = current_file
                except Exception:
                    pass
            else:
                # Keep last_mtime updated even when not optimizing to avoid a jump when it starts
                try:
                    current_file, current_ns = self._get_latest_optimization_png()
                    if current_file:
                        try:
                            current_size = os.path.getsize(current_file)
                        except Exception:
                            current_size = -1
                        if current_ns > last_mtime_ns or current_size != last_size or current_file != last_path:
                            last_mtime_ns = current_ns
                            last_size = current_size
                            last_path = current_file
                except Exception:
                    pass

    def _send_recorder_cmd(self, port: int, cmd: str) -> None:
        """Send a command to a recorder process on the given port (9999 or 10000)."""
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.5)
            s.connect(("localhost", port))
            s.sendall((cmd.strip() + "\n").encode())
            s.close()
        except Exception as e:
            print(f"[REAL LAB] Recorder cmd (port {port}): {e}")

    def _start_recorder_processes(self) -> None:
        """Start the two recorder subprocesses (cam1=9999, cam2=10000) to warm up table cameras."""
        lab_path = os.getenv("LAB_AUTOMATION_PATH")
        if not lab_path or not os.path.isdir(lab_path):
            print("[REAL LAB] LAB_AUTOMATION_PATH not set or invalid; skipping recorder warm-up.")
            return
        recorder_script = os.path.join(lab_path, "recorder_cam_laser_align_simplified.py")
        if not os.path.isfile(recorder_script):
            recorder_script = os.path.join(lab_path, "scripts", "recorder_cam_laser_align_simplified.py")
        if not os.path.isfile(recorder_script):
            print("[REAL LAB] Recorder script not found; table cam capture may fail (ports 9999/10000).")
            return
        use_real_camera = os.getenv("TABLE_CAM_USE_MOCK", "").strip().lower() not in ("1", "true", "yes")
        extra = ["--real-camera"] if use_real_camera else []
        try:
            p1 = subprocess.Popen(
                [sys.executable, recorder_script, "--cam", "0", "--port", "9999", "--prefix", "cam1"] + extra,
                cwd=lab_path,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            p2 = subprocess.Popen(
                [sys.executable, recorder_script, "--cam", "1", "--port", "10000", "--prefix", "cam2"] + extra,
                cwd=lab_path,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            self._recorder_procs = [p1, p2]
            time.sleep(1.2)
            print("[REAL LAB] Recorder processes started (cam1=9999, cam2=10000). Table cam capture ready.")
            atexit.register(self._shutdown_recorders)
        except Exception as e:
            print(f"[REAL LAB] Failed to start recorders: {e}")
            self._recorder_procs = []

    def _shutdown_recorders(self) -> None:
        """Send EXIT to recorder ports and wait for processes. Called on backend exit."""
        if not self._recorder_procs:
            return
        for port in (9999, 10000):
            self._send_recorder_cmd(port, "REC_OFF")
            self._send_recorder_cmd(port, "EXIT")
        for p in self._recorder_procs:
            try:
                p.wait(timeout=2.0)
            except Exception:
                try:
                    p.terminate()
                except Exception:
                    pass
        self._recorder_procs = []

    def _initialize_state(self):
        """Scans the table based on the catalog and populates the component map."""
        print("[REAL LAB] Scanning components...")
        
        # 1. Load Catalog to know what to look for
        # Assuming we are in backend/lab_communicator/real.py
        catalog_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "schemas", "component_catalog.json"))
        if not os.path.exists(catalog_path):
             print("[REAL LAB] Error: Catalog not found. Cannot scan.")
             return

        with open(catalog_path, "r") as f:
            catalog = json.load(f)

        # Store catalog map for metadata lookup (e.g. motor_controller)
        self.catalog_map = {item.get("tag_id"): item for item in catalog if item.get("tag_id")}

        # 2. Create OpticalComponent objects for everything in catalog
        components_to_scan = []
        for item in catalog:
            tag_id_str = item.get("tag_id") # e.g. "tag_22"
            if not tag_id_str: continue
            
            # Extract numeric ID from "tag_22" -> 22
            try:
                numeric_id = int(tag_id_str.replace("tag_", ""))
            except ValueError:
                print(f"[REAL LAB] Warning: Invalid tag format {tag_id_str}")
                continue

            comp = OpticalComponent(name=item.get("name", tag_id_str), tag_id=numeric_id)
            components_to_scan.append(comp)
            self.component_map[tag_id_str] = comp

        # 3. Perform Physical Scan
        self.experiment.scan_components_cloudlab(components_to_scan, force_rescan=True)
        
        # 4. Populate Lab State (build off lock, then swap)
        new_components: Dict[str, Any] = {}

        for item in catalog:
            tag_id = item.get("tag_id")
            comp = self.component_map.get(tag_id)
            inv = getattr(comp, "inventory_location", None) if comp else None

            # --- Debug: what we have for this component ---
            print(f"[REAL LAB] --- {tag_id} ---")
            print(f"  comp exists: {comp is not None}, inventory_location exists: {inv is not None}")
            if inv is not None:
                attrs = {}
                for a in ("x", "y", "z", "roll", "pitch", "yaw", "angle", "rx", "ry", "rz"):
                    if hasattr(inv, a):
                        attrs[a] = getattr(inv, a)
                print(f"  inventory_location attrs: {attrs}")
            else:
                print(f"  (no inventory_location)")

            if comp and comp.inventory_location:
                # Found on table
                comp.current_location = comp.inventory_location
                inv = comp.inventory_location
                calc_rotation = getattr(inv, "yaw", None) or 0
                print(f"  fallback yaw (deg): {getattr(inv, 'yaw', None)} -> rotation: {calc_rotation:.2f}")

                pose = {
                    "x": inv.x,
                    "y": inv.y,
                    "rotation": calc_rotation
                }
                # Include roll, pitch, yaw so UI can derive display rz (e.g. from yaw for top-down view)
                for key in ("roll", "pitch", "yaw"):
                    val = getattr(inv, key, None)
                    if val is not None:
                        pose[key] = val
                state = "PLACED"
                print(f"  pose written: {pose}")
            else:
                pose = {"x": 0, "y": 0, "rotation": 0}
                state = "INVENTORY"
                print(f"  state: INVENTORY (pose {pose})")

            entry = {
                "id": tag_id,
                "type": item.get("type", "OPTICAL_MIRROR"),
                "state": state,
                "pose": pose,
                "intent": {
                    "nominal_pose": pose if state == "PLACED" else None,
                    "is_optimized": False,
                    "placement_strategy": "MANUAL"
                },
                "metadata": {}
            }
            new_components[tag_id] = entry
            print(f"  entry keys: {list(entry.keys())}, intent.nominal_pose: {entry['intent'].get('nominal_pose')}")

        with self._state_lock:
            self.current_state["components"] = new_components
            self.current_state["last_updated"] = datetime.now().isoformat()
        n_placed = len([c for c in new_components.values() if c["state"] == "PLACED"])
        print(f"[REAL LAB] Scan complete. {n_placed} components PLACED.")

    def refresh_state(self):
        """
        Re-initialize lab state using the same logic as at startup.
        This is used by the UI "Refresh State" button.
        """
        with self._state_lock:
            self.current_state["system_status"] = "BUSY"
        try:
            self._initialize_state()
        finally:
            with self._state_lock:
                self.current_state["system_status"] = "IDLE"
                self.current_state["optimization_step"] = 0
                self.current_state["last_updated"] = datetime.now().isoformat()

    def set_lab_state(self, state: Dict[str, Any]):
        """
        Load a previously saved lab state snapshot and apply it to both:
        1) `self.current_state` (what the UI reads)
        2) `self.component_map` (what the robot uses)
        """
        if not isinstance(state, dict):
            raise ValueError("Loaded state must be a JSON object/dict")

        with self._state_lock:
            # Update the current_state that the UI reads.
            self.current_state = state
            self.current_state["system_status"] = "IDLE"
            self.current_state["optimization_step"] = int(self.current_state.get("optimization_step", 0) or 0)
            self.current_state["last_updated"] = datetime.now().isoformat()
            components = dict(self.current_state.get("components", {}) or {})

        # Apply to component_map so pick/place uses correct coordinates.
        # Snapshot poses are lab / UI mm; automation expects robot table mm.
        for tag_id, entry in components.items():
            pose = (entry or {}).get("pose", {}) or {}
            x_lab = float(pose.get("x", 0.0))
            y_lab = float(pose.get("y", 0.0))
            x, y = lab_table_xy_to_robot_xy(x_lab, y_lab)
            z = float(pose.get("z", 500.0))  # z isn't stored in current UI payload; default matches scan

            roll = 180
            pitch = 0
            yaw = float(pose.get("rotation"))

            comp = self.component_map.get(tag_id)
            if not comp:
                # If missing from map, skip (UI will still render, but robot may not know it).
                continue

            p = Pose(x=x, y=y, z=z, roll=roll, pitch=pitch, yaw=yaw)
            comp.inventory_location = p
            comp.current_location = p

            # Best-effort: keep flags consistent
            comp.is_placed = (entry or {}).get("state") == "PLACED"

    def get_lab_state(self) -> Dict[str, Any]:
        with self._state_lock:
            return json.loads(json.dumps(self.current_state))

    def set_cobyla_reference_from_png_bytes(self, data: bytes) -> Tuple[bool, str]:
        """Decode PNG bytes to BGR (OpenCV) and store for the next COBYLA optimize run."""
        if not data or len(data) < 8:
            return False, "empty body"
        try:
            import cv2
        except ImportError:
            return False, "cv2 not installed"
        arr = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_UNCHANGED)
        if img is None:
            return False, "could not decode PNG"
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        elif img.ndim == 3 and img.shape[2] == 4:
            img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        if img.ndim != 3 or img.shape[2] != 3:
            return False, "decoded image must be BGR with 3 channels"
        with self._cobyla_ref_lock:
            self._cobyla_reference_bgr = img.copy()
        h, w = img.shape[:2]
        print(f"[REAL LAB] Cobyla reference image set ({w}x{h} BGR)")
        return True, f"stored {w}x{h} BGR reference"

    def clear_cobyla_reference(self) -> None:
        with self._cobyla_ref_lock:
            self._cobyla_reference_bgr = None
        print("[REAL LAB] Cobyla reference image cleared")

    def get_cobyla_reference_status(self) -> Dict[str, Any]:
        with self._cobyla_ref_lock:
            ref = self._cobyla_reference_bgr
        if ref is None:
            return {"available": True, "set": False}
        h, w = ref.shape[:2]
        return {
            "available": True,
            "set": True,
            "width": int(w),
            "height": int(h),
            "channels": int(ref.shape[2]),
        }

    def _tag_id_for_component(self, comp: Any) -> Optional[str]:
        for tid, c in self.component_map.items():
            if c is comp:
                return tid
        return None

    def _ui_pose_for_placement_tick(
        self, tag_id: str, target_x: Optional[float], target_y: Optional[float]
    ) -> Dict[str, float]:
        """
        Newton sub-moves: take table X/Y from the place call, keep canvas rotation from current lab state.
        Robot `angle` / rotvec is not the same as the UI's top-down `rotation` (e.g. 270 vs ~29); parsing it
        misaligns ghost and solid.
        When target_x/target_y are set, they are robot-frame mm from the strategy; convert to lab for UI state.
        """
        with self._state_lock:
            comp_entry = (self.current_state.get("components") or {}).get(tag_id)
            pose = dict((comp_entry or {}).get("pose") or {})
        if target_x is not None and target_y is not None:
            nx, ny = robot_table_xy_to_lab_xy(float(target_x), float(target_y))
        else:
            nx = float(pose.get("x", 0.0))
            ny = float(pose.get("y", 0.0))
        rot = float(pose.get("rotation", 0.0) or 0.0)
        out: Dict[str, float] = {"x": nx, "y": ny, "rotation": rot}
        for key in ("roll", "pitch", "yaw"):
            if key in pose and pose[key] is not None:
                try:
                    out[key] = float(pose[key])
                except (TypeError, ValueError):
                    pass
        return out

    def _apply_placement_ui_phase(self, tag_id: str, phase: str, pose: Dict[str, float]) -> None:
        """
        phase='ghost' -> update intent.nominal_pose only (planned target before/at start of move).
        phase='physical' -> update solid pose + intent to match (after successful place).
        """
        if phase not in ("ghost", "physical"):
            return
        with self._state_lock:
            comp_entry = (self.current_state.get("components") or {}).get(tag_id)
            if not comp_entry:
                return
            intent = comp_entry.setdefault("intent", {})
            if phase == "ghost":
                intent["nominal_pose"] = dict(pose)
            else:
                comp_entry["pose"] = dict(pose)
                comp_entry["state"] = "PLACED"
                intent["nominal_pose"] = dict(pose)
            self.current_state["last_updated"] = datetime.now().isoformat()

    def _cloudlab_progress_callback(self, target_tag_id: str):
        """
        Optional callback for NewtonPlacementStrategy_cloudlab(progress_callback=...).
        Signature: (phase, component, target_x, target_y, angle=None, step=None)
        phase in ('ghost', 'physical').
        """

        def _cb(phase: str, component: Any, target_x: float, target_y: float, angle: Any = None, step: Any = None):
            tid = self._tag_id_for_component(component)
            if tid != target_tag_id:
                return
            pose = self._ui_pose_for_placement_tick(tid, target_x, target_y)
            self._apply_placement_ui_phase(tid, phase, pose)

        return _cb

    def _install_cloudlab_place_ui_hook(self, target_tag_id: str) -> None:
        """Wrap place_component_wo_home_specific_xy_cloudlab so UI gets ghost then physical updates."""
        exp = self.experiment
        if not hasattr(exp, "place_component_wo_home_specific_xy_cloudlab"):
            print("[REAL LAB] No place_component_wo_home_specific_xy_cloudlab on experiment; UI hook skipped.")
            return
        if self._place_cloudlab_orig is not None:
            return
        orig = exp.place_component_wo_home_specific_xy_cloudlab
        self._place_cloudlab_orig = orig
        comm = self

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
            tid = comm._tag_id_for_component(component) if component is not None else None
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
        print("[REAL LAB] Installed place_component_wo_home_specific_xy_cloudlab UI hook for Newton.")

    def _remove_cloudlab_place_ui_hook(self) -> None:
        if self._place_cloudlab_orig is None:
            return
        if hasattr(self.experiment, "place_component_wo_home_specific_xy_cloudlab"):
            self.experiment.place_component_wo_home_specific_xy_cloudlab = self._place_cloudlab_orig
        self._place_cloudlab_orig = None

    def get_rotation_from_angle(self, robot_angle: List[float]) -> float:
        """
        Reverses the logic of find_angle to recover the Z-rotation (theta)
        from the robot's orientation vector (rx, ry, rz).
        Assumes robot_angle is a Rotation Vector in degrees.
        """
        try:
            # 1. Convert degrees to radians
            # Based on forward logic: target = rotvec_rad * 180/pi
            # So rotvec_rad = target * pi/180
            v_rad = np.array(robot_angle) * np.pi / 180.0
            
            # 2. Create Rotation object
            r = R.from_rotvec(v_rad)
            
            # 3. Get Euler angles [Rx, Ry, Rz]
            # We expect [180, 0, theta] roughly
            euler = r.as_euler('xyz', degrees=True)
            
            # 4. Extract Z rotation
            # euler[2] is the rotation around Z
            return float(euler[2])
            
        except Exception as e:
            print(f"[REAL LAB] Error in get_rotation_from_angle: {e}")
            return 0.0

    async def move_component(self, target_id: str, params: Dict[str, Any]):
        print(f"[REAL LAB] Moving {target_id}...")

        # 1. Update Status
        with self._state_lock:
            self.current_state["system_status"] = "BUSY"

        # 2. Get Component
        if target_id not in self.component_map:
            print(f"[REAL LAB] Error: Component {target_id} not found in map.")
            with self._state_lock:
                self.current_state["system_status"] = "IDLE"
            return

        comp = self.component_map[target_id]
        
        # 3. Extract Coordinates (UI / API uses lab mm)
        tx = params.get("target_x")
        ty = params.get("target_y")
        rot = params.get("rotation")
        tx_lab = float(tx)
        ty_lab = float(ty)
        tx_robot, ty_robot = lab_table_xy_to_robot_xy(tx_lab, ty_lab)
        
        # 4. Execute Move
        try:
            print(
                f"[REAL LAB] Dispatching robot to X={tx_robot}, Y={ty_robot}, Rot={rot} "
                f"(lab X={tx_lab}, Y={ty_lab})"
            )
            
            if not comp.inventory_location:
                print(f"[REAL LAB] Warning: {target_id} inventory location unknown. Assuming it's at previous location or 0,0")
            
            # Worker thread: place blocks for a long time; must not block the event loop or lab-state polls stall.
            await asyncio.to_thread(
                lambda: self.experiment.place_component_wo_home_specific_xy_cloudlab(
                    component=comp,
                    target_x=tx_robot,
                    target_y=ty_robot,
                    angle=[-180, 0, -rot],
                )
            )
            
            # 5. Update State
            #UPDATE TO GET REFORCE-SCAM
            with self._state_lock:
                if target_id in self.current_state["components"]:
                    self.current_state["components"][target_id]["pose"] = {
                        "x": tx_lab,
                        "y": ty_lab,
                        "rotation": rot
                    }
                    self.current_state["components"][target_id]["state"] = "PLACED"

                    # Update intent to match reality
                    self.current_state["components"][target_id]["intent"]["nominal_pose"] = {
                        "x": tx_lab, "y": ty_lab, "rotation": rot
                    }
                    self.current_state["last_updated"] = datetime.now().isoformat()
            print(comp)
        except Exception as e:
            print(f"[REAL LAB] Move Failed: {e}")

        finally:
            with self._state_lock:
                self.current_state["system_status"] = "IDLE"
                self.current_state["optimization_step"] = 0
                self.current_state["last_updated"] = datetime.now().isoformat()

    async def move_motor(self, target_id: str, motor_id: int, distance: float):
        print(f"[REAL LAB] Moving motor {motor_id} of {target_id} by {distance} (RELATIVE)...")
        
        # Determine controller from catalog metadata
        meta = self.catalog_map.get(target_id)
        if not meta:
            print(f"[REAL LAB] Error: {target_id} not in component_catalog.")
            return
        controller_name = meta.get("motor_controller")
        if not controller_name:
            print(f"[REAL LAB] Error: catalog entry for {target_id} has no 'motor_controller'.")
            return

        controller = getattr(self.experiment, controller_name, None)
        
        if not controller:
            print(f"[REAL LAB] Error: Controller '{controller_name}' not found on experiment.")
            return

        try:
            # Worker thread: same event-loop issue as optimize / place.
            await asyncio.to_thread(
                controller.move_motor,
                motor_id,
                distance,
                wait_completion=True,
            )
            print(f"[REAL LAB] Motor moved.")
        except Exception as e:
            print(f"[REAL LAB] Motor move failed: {e}")

    async def optimize_component(self, target_id: str, strategy_name: str, params: Dict[str, Any]):
        print(f"[REAL LAB] Optimizing {target_id} with {strategy_name}...")
        with self._state_lock:
            self.current_state["system_status"] = "OPTIMIZING"
            self.current_state["optimization_step"] = 0
        self._last_optimization_image_basename = None

        if target_id not in self.component_map:
            with self._state_lock:
                self.current_state["system_status"] = "IDLE"
            return

        comp = self.component_map[target_id]
        newton_place_hook_installed = False

        try:
            # 1. Select Strategy
            strategy = None
            if strategy_name == "NEWTON":
                # Live UI: ghost follows planned targets; solid follows completed places (see _cloudlab hooks).
                self._install_cloudlab_place_ui_hook(target_id)
                newton_place_hook_installed = True

                newton_kw: Dict[str, Any] = dict(
                    camera_number=params["camera_number"],
                    target_x_pixel=params["target_x_pixel"],
                    tolerance_ratio=params["tolerance_ratio"],
                    axis=params["axis"],
                    initial_move=-0.2,
                    do_repositioning=False,
                    video_exposure = 1.0
                )
                try:
                    init_sig = inspect.signature(NewtonPlacementStrategy_cloudlab.__init__)
                    if "progress_callback" in init_sig.parameters:
                        newton_kw["progress_callback"] = self._cloudlab_progress_callback(target_id)
                except (TypeError, ValueError):
                    pass

                strategy = NewtonPlacementStrategy_cloudlab(**newton_kw)
                print("DOING NEWTON STRATEGY")
            elif strategy_name == "COBYLA":
                motor_ids = params.get("motor_ids")
                if not motor_ids:
                    # Fallback: check catalog/state if we have it
                    # But params should ideally come from UI
                    # Let's see if we can check our loaded catalog info?
                    # We didn't persist the full catalog in component_map, but we have self.current_state
                    if target_id in self.current_state["components"]:
                        # We didn't save motor_ids in current_state["components"] entry in _initialize_state yet.
                        # We should probably update _initialize_state to save it if we want to rely on it.
                        # For now, expect it in params.
                        pass
                
                if not motor_ids:
                     raise ValueError("COBYLA strategy requires 'motor_ids' parameter.")

                try:
                    _exp = float(params.get("exposure", 0.2))
                except (TypeError, ValueError):
                    _exp = 0.2
                _exp = max(0.001, min(30.0, _exp))

                cobyla_kw: Dict[str, Any] = {
                    "camera_number": params.get("camera_number", 1),
                    "motor_ids": motor_ids,
                    "objective_threshold": params.get("objective_threshold", 100.0),
                    "video_exposure": _exp,
                    "capture_exposure": _exp,
                }
                with self._cobyla_ref_lock:
                    ref_copy = None if self._cobyla_reference_bgr is None else self._cobyla_reference_bgr.copy()
                if ref_copy is not None:
                    try:
                        sig = inspect.signature(CobylaAlignmentStrategy.__init__)
                        if "reference_image" in sig.parameters:
                            cobyla_kw["reference_image"] = ref_copy
                    except (TypeError, ValueError):
                        cobyla_kw["reference_image"] = ref_copy
                else:
                    print("[REAL LAB] COBYLA: no reference image set via UI; strategy will use its own fallback if any.")

                strategy = CobylaAlignmentStrategy(**cobyla_kw)
            
            if strategy:
                # 2. Execute off the event loop. optimize_component() in lab_automation is synchronous and
                # can run for minutes; if we block here, GET /api/lab-state never runs and the UI never
                # sees system_status=OPTIMIZING or optimization_step updates (mock works because it awaits sleep).
                await asyncio.to_thread(self.experiment.optimize_component, comp, strategy)

                # 3. Update State
                with self._state_lock:
                    if target_id in self.current_state["components"]:
                        self.current_state["components"][target_id]["intent"]["is_optimized"] = True
                        self.current_state["components"][target_id]["intent"]["placement_strategy"] = strategy_name
                        self.current_state["last_updated"] = datetime.now().isoformat()

        except Exception as e:
            print(f"[REAL LAB] Optimization Failed: {e}")

        finally:
            if newton_place_hook_installed:
                self._remove_cloudlab_place_ui_hook()
            with self._state_lock:
                self.current_state["system_status"] = "IDLE"
                self.current_state["optimization_step"] = 0
                self.current_state["last_updated"] = datetime.now().isoformat()
            
    async def remove_component(self, target_id: str):
         print(f"[REAL LAB] Remove requested for {target_id} (Not implemented)")
         pass

    def get_video_feed_status(self):
        # TODO: Check actual camera connection
        return {"connected": True, "source": "/api/video-feed/stream"} 

    def get_video_stream(self, fps: int = 10):
        """
        Yields MJPEG frames from the camera.
        Uses CameraDriver if available, or a fallback generator.
        """
        print(f"[REAL LAB] Starting Video Stream Generator at {fps} FPS...")
        
        # We need to import cv2 here inside the method or at module level if not already
        import cv2
        import numpy as np

        camera = None
        # Try to get the ceiling camera (Port 0)
        if self.experiment and hasattr(self.experiment, 'ceiling_cam1'):
            camera = self.experiment.ceiling_cam1
            
        sleep_duration = 1.0 / max(1, min(fps, 60)) # Clamp between 1 and 60 FPS
            
        while True:
            frame = None
            if camera:
                try:
                    # Use the CameraDriver's get_frame method instead of accessing cap directly
                    frame = camera.get_frame()
                except Exception as e:
                    print(f"[REAL LAB] Camera stream error: {e}")
                    frame = None
            
            if frame is None:
                # Generate a dummy frame
                frame = np.zeros((480, 640, 3), dtype=np.uint8)
                cv2.putText(frame, "NO SIGNAL", (200, 240), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)

            ret, buffer = cv2.imencode('.jpg', frame)
            if ret:
                frame_bytes = buffer.tobytes()
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
            
            # Use time.sleep instead of asyncio.sleep in a synchronous generator
            import time
            time.sleep(sleep_duration)

    def get_optimization_stream(self, fps: int = 5):
        """Yields MJPEG frames by watching the Camera_Images directory."""
        import cv2
        import time

        # Ensure the most likely directory exists so strategies that rely on CWD won't fail silently.
        try:
            os.makedirs(os.path.abspath("Camera_Images"), exist_ok=True)
        except Exception:
            pass

        watch_dirs = self._get_optimization_watch_dirs()
        print(f"[REAL LAB] Starting Optimization Feed watching: {watch_dirs}")
        
        sleep_duration = 1.0 / max(1, min(fps, 30))
        last_mtime_ns = 0
        last_size = -1
        last_frame_bytes = None
        
        while True:
            try:
                latest_file, current_ns = self._get_latest_optimization_png()
                if latest_file:
                    try:
                        current_size = os.path.getsize(latest_file)
                    except Exception:
                        current_size = -1

                    # Try to refresh if file version changed (mtime/size/file identity)
                    if current_ns > last_mtime_ns or current_size != last_size:
                        # Retry decode a few times to avoid libpng "Read Error" from partially-written files.
                        img = None
                        for attempt in range(6):
                            try:
                                time.sleep(0.05)
                                img = cv2.imread(latest_file)
                            except Exception:
                                img = None
                            if img is not None:
                                break

                        if img is not None:
                            ret, buffer = cv2.imencode('.jpg', img)
                            if ret:
                                last_frame_bytes = buffer.tobytes()
                                last_mtime_ns = current_ns
                                last_size = current_size
                                print(
                                    f"[REAL LAB] optimization-stream updated "
                                    f"(file={os.path.basename(latest_file)} ns={current_ns} size={current_size})"
                                )
            except Exception as e:
                print(f"[REAL LAB] Error in optimization stream: {e}")
            
            # Yield the last known frame
            if last_frame_bytes:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + last_frame_bytes + b'\r\n')
            else:
                # Dummy frame
                import numpy as np
                frame = np.zeros((480, 640, 3), dtype=np.uint8)
                cv2.putText(frame, "WAITING FOR OPTIMIZATION", (50, 240), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)
                ret, buffer = cv2.imencode('.jpg', frame)
                if ret:
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
                       
            time.sleep(sleep_duration)

    def capture_table_cam(self, cam_id: int, exposure: float = 0.2):
        """Capture one image from table recorder camera (1 or 2). Returns PNG bytes or None."""
        if not RECORDER_CAPTURE_AVAILABLE or activate_cam_and_capture is None:
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
            img = activate_cam_and_capture(
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

    async def add_component_to_state(self, component_data: Dict[str, Any]):
        print(f"[REAL LAB] User requested to add {component_data.get('tag_id')}. Please place it on the table and Rescan.")
