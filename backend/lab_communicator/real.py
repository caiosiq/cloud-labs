import atexit
import json
import os
import subprocess
import sys
import time
import asyncio
from datetime import datetime
from typing import Dict, Any, List
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
    from lab_automation.objects.strategies import NewtonPlacementStrategy, CobylaAlignmentStrategy
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
            "components": {}
        }
        
        self._initialize_state()
        self._recorder_procs: List[subprocess.Popen] = []
        self._start_recorder_processes()

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
        self.experiment.scan_components(components_to_scan, force_rescan=True)
        
        # 4. Populate Lab State
        self.current_state["components"] = {}
        
        for item in catalog:
            tag_id = item.get("tag_id")
            comp = self.component_map.get(tag_id)
            
            if comp and comp.inventory_location:
                # Found on table
                
                # Try to get angle vector from inventory_location
                angle_vector = None
                if hasattr(comp.inventory_location, 'angle') and comp.inventory_location.angle:
                    angle_vector = comp.inventory_location.angle
                elif hasattr(comp.inventory_location, 'rx'):
                     angle_vector = [comp.inventory_location.rx, comp.inventory_location.ry, comp.inventory_location.rz]
                
                if angle_vector:
                     calc_rotation = self.get_rotation_from_angle(angle_vector)
                     print(f"[REAL LAB] {tag_id} found. Angle vec: {angle_vector} -> Z-Rot: {calc_rotation:.2f}")
                else:
                     # Fallback to yaw if no angle vector
                     calc_rotation = comp.inventory_location.yaw or 0
                     print(f"[REAL LAB] {tag_id} found. Using fallback yaw: {calc_rotation:.2f}")

                pose = {
                    "x": comp.inventory_location.x,
                    "y": comp.inventory_location.y,
                    "rotation": calc_rotation
                }
                state = "PLACED"
            else:
                # Not found -> In Inventory (virtual)
                pose = {"x": 0, "y": 0, "rotation": 0}
                state = "INVENTORY"

            self.current_state["components"][tag_id] = {
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
        
        self.current_state["last_updated"] = datetime.now().isoformat()
        print(f"[REAL LAB] Scan complete. Found {len([c for c in self.current_state['components'].values() if c['state'] == 'PLACED'])} components.")

    def get_lab_state(self) -> Dict[str, Any]:
        return self.current_state

    def find_angle(self, rotation_degrees: float) -> List[float]:
        """
        Converts a simple Z-rotation (degrees) into the robot's specific 3D orientation format (rx, ry, rz).
        Using logic provided:
        1. Create Euler angles [180, 0, rotation] (assuming gripper down)
        2. Convert to rotation vector
        3. Convert to degree-like magnitude
        4. Invert X and Y, set Z to 0 (specific to this robot configuration)
        """
        try:
            # 1. Create Euler angles [180, 0, rotation]
            # Standard convention: Gripper down is Rx=180.
            euler_angles = [180, 0, rotation_degrees]
            
            # 2. Convert to Rotation object
            r_inverse = R.from_euler('xyz', euler_angles, degrees=True)
            
            # 3. Get Rotation Vector (radians)
            inverted_rad = r_inverse.as_rotvec()
            
            # 4. Convert to degrees (inverted)
            inverted_place = -inverted_rad * 180 / np.pi
            
            # 5. Construct target angle
            # User snippet: target_angle = [-inverted_place[0], -inverted_place[1], 0.0]
            target_angle = [-inverted_place[0], -inverted_place[1], 0.0]
            
            print(f"[REAL LAB] find_angle({rotation_degrees}) -> {target_angle}")
            return [float(x) for x in target_angle]
            
        except Exception as e:
            print(f"[REAL LAB] Error in find_angle: {e}. Fallback to default.")
            return [180.0, 0.0, 0.0]

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
        self.current_state["system_status"] = "BUSY"
        
        # 2. Get Component
        if target_id not in self.component_map:
            print(f"[REAL LAB] Error: Component {target_id} not found in map.")
            self.current_state["system_status"] = "IDLE"
            return

        comp = self.component_map[target_id]
        
        # 3. Extract Coordinates
        tx = params.get("target_x")
        ty = params.get("target_y")
        rot = params.get("rotation", 0)
        
        # 4. Execute Move
        try:
            print(f"[REAL LAB] Dispatching robot to X={tx}, Y={ty}, Rot={rot}")
            
            if not comp.inventory_location:
                print(f"[REAL LAB] Warning: {target_id} inventory location unknown. Assuming it's at previous location or 0,0")
            
            self.experiment.place_component_wo_home_specific_xy_from_current(
                component=comp,
                target_x=tx,
                target_y=ty,
                angle=self.find_angle(rot)
            )
            
            # 5. Update State
            #UPDATE TO GET REFORCE-SCAM
            if target_id in self.current_state["components"]:
                self.current_state["components"][target_id]["pose"] = {
                    "x": tx,
                    "y": ty,
                    "rotation": rot
                }
                self.current_state["components"][target_id]["state"] = "PLACED"
                
                # Update intent to match reality
                self.current_state["components"][target_id]["intent"]["nominal_pose"] = {
                    "x": tx, "y": ty, "rotation": rot
                }
            
        except Exception as e:
            print(f"[REAL LAB] Move Failed: {e}")
            
        finally:
            self.current_state["system_status"] = "IDLE"
            self.current_state["last_updated"] = datetime.now().isoformat()

    async def move_motor(self, target_id: str, motor_id: int, distance: float):
        print(f"[REAL LAB] Moving motor {motor_id} of {target_id} by {distance} (RELATIVE)...")
        
        # Determine controller from catalog metadata
        meta = self.catalog_map[target_id]
        if "motor_controller" in meta:
            controller_name = meta["motor_controller"]
        
        controller = getattr(self.experiment, controller_name, None)
        
        if not controller:
            print(f"[REAL LAB] Error: Controller '{controller_name}' not found on experiment.")
            return

        try:
            # We assume the underlying library treats move_motor as relative if that's what COBYLA strategy implies.
            # User confirmed: "move motor is already assuming relative motions"
            controller.move_motor(motor_id, distance, wait_completion=True)
            print(f"[REAL LAB] Motor moved.")
        except Exception as e:
            print(f"[REAL LAB] Motor move failed: {e}")

    async def optimize_component(self, target_id: str, strategy_name: str, params: Dict[str, Any]):
        print(f"[REAL LAB] Optimizing {target_id} with {strategy_name}...")
        self.current_state["system_status"] = "OPTIMIZING"
        
        if target_id not in self.component_map:
             self.current_state["system_status"] = "IDLE"
             return

        comp = self.component_map[target_id]

        try:
            # 1. Select Strategy
            strategy = None
            if strategy_name == "NEWTON":
                # STRICT PARAMETER HANDLING: No defaults allowed.
                # If params are missing, this will raise a KeyError, which is desired behavior.
                strategy = NewtonPlacementStrategy(
                    camera_number=params["camera_number"],
                    target_x_pixel=params["target_x_pixel"],
                    tolerance_ratio=params["tolerance_ratio"]
                )
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

                strategy = CobylaAlignmentStrategy(
                    camera_number=params.get("camera_number", 1),
                    motor_ids=motor_ids,
                    objective_threshold=params.get("objective_threshold", 100.0)
                )
            
            if strategy:
                # 2. Execute
                self.experiment.optimize_component(comp, strategy)
                
                # 3. Update State 
                if target_id in self.current_state["components"]:
                    self.current_state["components"][target_id]["intent"]["is_optimized"] = True
                    self.current_state["components"][target_id]["intent"]["placement_strategy"] = strategy_name
                
        except Exception as e:
            print(f"[REAL LAB] Optimization Failed: {e}")
            
        finally:
            self.current_state["system_status"] = "IDLE"
            self.current_state["last_updated"] = datetime.now().isoformat()
            
    async def remove_component(self, target_id: str):
         print(f"[REAL LAB] Remove requested for {target_id} (Not implemented)")
         pass

    def get_video_feed_status(self):
        # TODO: Check actual camera connection
        return {"connected": True, "source": "/api/video-feed/stream"} 

    def get_video_stream(self):
        """
        Yields MJPEG frames from the camera.
        Uses CameraDriver if available, or a fallback generator.
        """
        print("[REAL LAB] Starting Video Stream Generator...")
        
        # We need to import cv2 here inside the method or at module level if not already
        import cv2
        import numpy as np

        camera = None
        # Try to get the ceiling camera (Port 0)
        if self.experiment and hasattr(self.experiment, 'ceiling_cam1'):
            camera = self.experiment.ceiling_cam1
            
        while True:
            frame = None
            if camera:
                try:
                    # Attempt to read frame directly from OpenCV capture object
                    if hasattr(camera, 'cap') and camera.cap is not None:
                         ret, frame = camera.cap.read()
                         if not ret:
                             frame = None
                    else:
                        # Fallback if no direct cap access
                        frame = None 
                except Exception:
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
            # But StreamingResponse takes an iterator. If it's async, we use async generator.
            # FastAPI StreamingResponse supports both. Let's stick to synchronous for simplicity if cv2 blocks,
            # but ideally we should be async. However, cv2.read() is blocking.
            # To be safe with FastAPI's event loop, we should probably run this in a thread or accept blocking.
            # For now, let's use time.sleep(0.05) to yield control.
            import time
            time.sleep(0.05)

    def capture_table_cam(self, cam_id: int):
        """Capture one image from table recorder camera (1 or 2). Returns PNG bytes or None."""
        if not RECORDER_CAPTURE_AVAILABLE or activate_cam_and_capture is None:
            return None
        if cam_id not in (1, 2):
            return None
        import cv2
        import tempfile
        import os as _os
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp_path = f.name
        try:
            img = activate_cam_and_capture(
                cam_id=cam_id,
                video_exposure=0.05,
                capture_exposure=0.05,
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
