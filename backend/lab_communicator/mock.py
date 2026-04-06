import os
import json
import asyncio
import random
from datetime import datetime
from io import BytesIO
from typing import Any, Dict, Optional, Tuple

from .base import LabCommunicator

# Constants
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEMAS_DIR = os.path.join(BASE_DIR, "..", "..", "schemas")
LAB_STATE_FILE = os.path.abspath(os.path.join(SCHEMAS_DIR, "mock_lab_state.json"))
CATALOG_FILE = os.path.abspath(os.path.join(SCHEMAS_DIR, "component_catalog.json"))

class MockLabCommunicator(LabCommunicator):
    """
    Mock implementation that simulates a physical lab.
    Uses a local JSON file to persist state.
    """
    def __init__(self):
        self.state_file = LAB_STATE_FILE
        self.catalog_file = CATALOG_FILE
        print(f"[MOCK LAB] Using state file: {self.state_file}")
        self._ensure_state()
        self._load_catalog()
        self._cobyla_reference_bgr = None  # optional BGR ndarray for UI / parity with real

    def _ensure_state(self):
        if not os.path.exists(self.state_file):
            raise FileNotFoundError(f"CRITICAL: Lab State file not found at: {self.state_file}")
        
        # Validate content
        try:
            with open(self.state_file, "r") as f:
                data = json.load(f)
                if "components" not in data:
                    raise ValueError("Lab State file is missing 'components' key")
        except json.JSONDecodeError:
            raise ValueError(f"CRITICAL: Invalid JSON in Lab State file: {self.state_file}")
        except Exception as e:
            raise RuntimeError(f"CRITICAL: Failed to load Lab State: {str(e)}")

    def _load_catalog(self):
        self.catalog = []
        if os.path.exists(self.catalog_file):
            try:
                with open(self.catalog_file, "r") as f:
                    self.catalog = json.load(f)
            except Exception as e:
                print(f"[MOCK LAB] Failed to load catalog: {e}")

    def _get_component_size(self, tag_id: str) -> float:
        # Default 90mm
        size = 90.0
        for item in self.catalog:
            if item.get("tag_id") == tag_id:
                s = item.get("size")
                if isinstance(s, dict):
                    size = max(s.get("width", 90), s.get("height", 90))
                elif isinstance(s, (int, float)):
                    size = float(s)
                break
        return size

    def _read_state(self) -> Dict[str, Any]:
        with open(self.state_file, "r") as f:
            return json.load(f)

    def _write_state(self, state: Dict[str, Any]):
        with open(self.state_file, "w") as f:
            json.dump(state, f, indent=2)

    def get_lab_state(self) -> Dict[str, Any]:
        return self._read_state()

    def refresh_state(self):
        """
        Mock mode has no sensors to re-scan; keep the in-file state as-is.
        """
        # No-op: the state is already persisted in `self.state_file`.
        return

    def set_lab_state(self, state: Dict[str, Any]):
        """
        Load a previously saved lab state snapshot into the mock persistence file.
        """
        self._write_state(state)

    async def move_component(self, target_id: str, target_pose: Dict[str, float]):
        print(f"[MOCK LAB] Moving {target_id}...")
        
        # 1. Lock
        state = self._read_state()
        state["system_status"] = "BUSY"
        self._write_state(state)
        
        # 2. Simulate Delay
        await asyncio.sleep(2)
        
        # 3. Update State
        state = self._read_state()
        
        # Simulate Noise
        noise_x = random.uniform(-0.5, 0.5)
        noise_y = random.uniform(-0.5, 0.5)
        
        if "components" not in state: state["components"] = {}
        
        if target_id not in state["components"]:
            print(f"[MOCK LAB] Error: Cannot move component {target_id} - Not found in Lab State.")
            state["system_status"] = "IDLE"
            self._write_state(state)
            return

        comp = state["components"][target_id]
        comp["state"] = "PLACED"
        
        tx = target_pose.get("target_x", target_pose.get("x", 0))
        ty = target_pose.get("target_y", target_pose.get("y", 0))
        trot = target_pose.get("rotation", 0)
        
        comp["pose"] = {
            "x": tx + noise_x,
            "y": ty + noise_y,
            "rotation": trot
        }
        
        comp["intent"] = {
            "nominal_pose": {"x": tx, "y": ty, "rotation": trot},
            "placement_strategy": "MANUAL",
            "last_optimized_pose": None,
            "is_optimized": False
        }
        
        state["last_updated"] = datetime.now().isoformat()
        state["system_status"] = "IDLE"
        self._write_state(state)
        print(f"[MOCK LAB] Moved {target_id} to ({comp['pose']['x']:.2f}, {comp['pose']['y']:.2f})")

    async def move_motor(self, target_id: str, motor_id: int, distance: float):
        print(f"[MOCK LAB] Moving motor {motor_id} of {target_id} by {distance}...")
        
        # Lock
        state = self._read_state()
        state["system_status"] = "BUSY"
        self._write_state(state)
        
        await asyncio.sleep(1)
        
        # Unlock
        state = self._read_state()
        state["system_status"] = "IDLE"
        self._write_state(state)
        print(f"[MOCK LAB] Motor move complete.")

    async def optimize_component(self, target_id: str, strategy: str, params: Dict[str, Any]):
        print(f"[MOCK LAB] Optimizing {target_id} with {strategy}...")
        
        state = self._read_state()
        state["system_status"] = "OPTIMIZING"
        self._write_state(state)
        
        await asyncio.sleep(3)
        
        state = self._read_state()
        if "components" in state and target_id in state["components"]:
            comp = state["components"][target_id]
            
            # Simulate result
            optimized_rotation = comp["pose"].get("rotation", 0) + random.uniform(-1, 1)
            comp["pose"]["rotation"] = optimized_rotation
            comp["metadata"]["last_optimization_score"] = 0.99
            
            if not comp.get("intent"): comp["intent"] = {}
            comp["intent"]["placement_strategy"] = strategy
            comp["intent"]["last_optimized_pose"] = comp["pose"].copy()
            comp["intent"]["is_optimized"] = True
            
        state["system_status"] = "IDLE"
        state["last_updated"] = datetime.now().isoformat()
        self._write_state(state)
        print(f"[MOCK LAB] Optimization complete.")

    async def remove_component(self, target_id: str):
        print(f"[MOCK LAB] Removing {target_id}...")
        state = self._read_state()
        if "components" in state and target_id in state["components"]:
            del state["components"][target_id]
            state["last_updated"] = datetime.now().isoformat()
            self._write_state(state)
        await asyncio.sleep(1)

    async def add_component_to_state(self, component_data: Dict[str, Any]):
        print(f"[MOCK LAB] Adding component: {component_data}")
        state = self._read_state()
        
        comp_type = component_data.get("type", "OPTICAL_MIRROR")
        tag_id = component_data.get("tag_id")
        
        if not tag_id:
             print("[MOCK LAB] Error: No tag_id provided for add_component")
             return

        if "components" not in state: state["components"] = {}
        
        if tag_id in state["components"]:
             print(f"[MOCK LAB] Component {tag_id} already exists. Skipping placement.")
             return

        # Determine size for collision check
        my_size = self._get_component_size(tag_id)
        
        # Place at a random valid location
        valid_pose = False
        attempts = 0
        x, y = 0, 0
        
        while not valid_pose and attempts < 100:
            x = random.uniform(100, 900)
            y = random.uniform(100, 600)
            valid_pose = True
            
            for cid, comp in state["components"].items():
                cx = comp["pose"]["x"]
                cy = comp["pose"]["y"]
                other_size = self._get_component_size(cid)
                
                min_dist_x = (my_size / 2) + (other_size / 2)
                min_dist_y = (my_size / 2) + (other_size / 2)
                
                if abs(x - cx) < min_dist_x and abs(y - cy) < min_dist_y:
                    valid_pose = False
                    break
            
            attempts += 1
            
        if not valid_pose:
            print("[MOCK LAB] FAILED to find free space for component after 100 attempts.")
            return 

        state["components"][tag_id] = {
            "id": tag_id, 
            "type": comp_type,
            "state": "PLACED",
            "pose": { "x": x, "y": y, "rotation": 0 },
            "intent": {
                "nominal_pose": { "x": x, "y": y, "rotation": 0 },
                "placement_strategy": "MANUAL",
                "last_optimized_pose": None,
                "is_optimized": False
            },
            "metadata": {}
        }
        
        state["last_updated"] = datetime.now().isoformat()
        self._write_state(state)
        print(f"[MOCK LAB] Placed {tag_id} at ({x:.1f}, {y:.1f})")

    def set_cobyla_reference_from_png_bytes(self, data: bytes) -> Tuple[bool, str]:
        """Same API as real lab; mock optimize does not use it, but UI can test the flow."""
        if not data or len(data) < 8:
            return False, "empty body"
        try:
            from PIL import Image
            import numpy as np
        except ImportError:
            return False, "Pillow and numpy required (pip install Pillow numpy)"
        try:
            pil = Image.open(BytesIO(data))
            pil.load()
            rgb = np.array(pil.convert("RGB"))
        except Exception as e:
            return False, f"could not decode PNG: {e}"
        if rgb.ndim != 3 or rgb.shape[2] != 3:
            return False, "decoded image must have 3 channels"
        # BGR ndarray for parity with OpenCV / real lab
        bgr = rgb[:, :, ::-1].copy()
        self._cobyla_reference_bgr = bgr
        h, w = bgr.shape[:2]
        print(f"[MOCK LAB] Cobyla reference image set ({w}x{h} BGR)")
        return True, f"stored {w}x{h} BGR reference (mock)"

    def clear_cobyla_reference(self) -> None:
        self._cobyla_reference_bgr = None
        print("[MOCK LAB] Cobyla reference image cleared")

    def get_cobyla_reference_status(self) -> Dict[str, Any]:
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

    def get_cobyla_reference_png_bytes(self) -> Optional[bytes]:
        ref = self._cobyla_reference_bgr
        if ref is None:
            return None
        try:
            from PIL import Image
            import numpy as np
        except ImportError:
            return None
        # ref is BGR; PIL expects RGB
        rgb = ref[:, :, ::-1]
        img = Image.fromarray(np.ascontiguousarray(rgb))
        buf = BytesIO()
        img.save(buf, format="PNG", compress_level=6)
        return buf.getvalue()

    def get_video_feed_status(self) -> Dict[str, Any]:
        return {"connected": True, "source": "/api/video-feed/stream"}

    def capture_table_cam(self, cam_id: int, exposure: float = 0.2) -> bytes:
        """
        Return a synthetic PNG so the table-cam panel and Cobyla reference flow work in MOCK mode.
        Real hardware uses RealLabCommunicator.capture_table_cam.
        """
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            print("[MOCK LAB] capture_table_cam: Pillow not installed")
            return None
        if cam_id not in (1, 2):
            return None

        w, h = 640, 480
        img = Image.new("RGB", (w, h), (18, 22, 30))
        draw = ImageDraw.Draw(img)
        grid = (36, 40, 52)
        for x in range(0, w, 40):
            draw.line([(x, 0), (x, h)], fill=grid, width=1)
        for y in range(0, h, 40):
            draw.line([(0, y), (w, y)], fill=grid, width=1)

        try:
            font = ImageFont.load_default()
        except Exception:
            font = None

        title = f"MOCK table cam {cam_id}"
        sub = f"exposure={exposure:g}s — LAB_MODE=MOCK"
        hint = "Capture / Set Cobyla reference use this image for UI testing."
        if font:
            draw.text((24, 20), title, fill=(226, 232, 240), font=font)
            draw.text((24, 38), sub, fill=(148, 163, 184), font=font)
            draw.text((24, 58), hint, fill=(100, 116, 139), font=font)
        else:
            draw.text((24, 20), title, fill=(226, 232, 240))
            draw.text((24, 38), sub, fill=(148, 163, 184))

        # Offset "beam" spot slightly per cam so CAM1 vs CAM2 is visible
        cx = w // 2 + (cam_id - 1) * 55
        cy = h // 2 - 10
        r = 28
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(248, 113, 113), width=3)
        draw.line([(cx - 40, cy), (cx + 40, cy)], fill=(251, 191, 36), width=2)

        buf = BytesIO()
        img.save(buf, format="PNG", compress_level=6)
        return buf.getvalue()

    def get_video_stream(self):
        """Yields a static placeholder image for mock mode."""
        import time
        while True:
            # Yield the SVG bytes or a placeholder text
            # Since browsers expect MJPEG (usually JPEGs), yielding SVG might not work in an <img src> expecting a stream.
            # But we can try yielding a multipart response where each part is the SVG? 
            # No, MJPEG is specifically JPEG.
            # Let's yield a simple text frame if we can't generate JPEG.
            # OR, we can just yield the same bytes as the SVG file if we change the content type in main.py?
            # No, main.py sets multipart/x-mixed-replace; boundary=frame
            
            # Let's generate a minimal JPEG header and some dummy data? No, that's corrupt.
            # We should probably use the same SVG file response approach for Mock in main.py,
            # BUT since we want to unify the API, let's make get_video_stream return None for Mock,
            # and handle it in main.py.
            
            # Actually, let's just sleep forever, effectively "hanging" the stream (not good).
            
            # Better approach: The user wants to see the Mock Feed.
            # The Mock Feed is an SVG.
            # We can't easily stream an SVG as MJPEG.
            # So for Mock, we should probably stick to the static file.
            # I will modify main.py to handle this distinction.
            # But I must implement this method to satisfy the abstract base class.
            
            # Raise an error to signal main.py to use fallback?
            # Or yield nothing and return.
            yield b'' 
            break
