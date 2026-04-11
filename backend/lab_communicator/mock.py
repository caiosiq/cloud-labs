import os
import json
import asyncio
import random
from datetime import datetime
from io import BytesIO
from typing import Any, Dict, Optional, Tuple

from .base import LabCommunicator
from . import motor_rotation_store as motor_rot
from .storage_region import (
    STORAGE_NOMINAL_ROTATION_DEG,
    find_storage_slot_and_center,
    is_placed_region,
    is_storage_region,
    nominal_center_pose_for_stored_entry,
    random_placed_position,
)

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

    def _get_component_wh(self, tag_id: str) -> Tuple[float, float]:
        for item in self.catalog:
            if item.get("tag_id") == tag_id:
                s = item.get("size")
                if isinstance(s, dict):
                    return float(s.get("width", 90)), float(s.get("height", 90))
                if isinstance(s, (int, float)):
                    v = float(s)
                    return v, v
        return 90.0, 90.0

    def _read_state(self) -> Dict[str, Any]:
        with open(self.state_file, "r") as f:
            return json.load(f)

    def _write_state(self, state: Dict[str, Any]):
        with open(self.state_file, "w") as f:
            json.dump(state, f, indent=2)

    def _catalog_meta_for_tag(self, tag_id: str) -> Optional[Dict[str, Any]]:
        for item in self.catalog:
            if item.get("tag_id") == tag_id:
                return item
        return None

    def _inject_motor_rotations_into_state(self, state: Dict[str, Any]) -> None:
        components = state.get("components") or {}
        if not isinstance(components, dict):
            return
        for tag_id, comp in components.items():
            if not isinstance(comp, dict):
                continue
            meta = self._catalog_meta_for_tag(tag_id)
            mids = (meta or {}).get("motor_ids") or []
            if not mids:
                continue
            mr = motor_rot.get_rotations_for_motor_ids(tag_id, list(mids))
            pose = comp.setdefault("pose", {})
            if isinstance(pose, dict):
                pose["motor_rotations"] = dict(mr)
            intent = comp.setdefault("intent", {})
            if isinstance(intent, dict):
                intent["nominal_motor_rotations"] = dict(mr)

    def get_lab_state(self) -> Dict[str, Any]:
        state = self._read_state()
        self._inject_motor_rotations_into_state(state)
        return state

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
        state = self._read_state()
        if "components" not in state or target_id not in state["components"]:
            print(f"[MOCK LAB] Error: Cannot move component {target_id} - Not found in Lab State.")
            return

        comp = state["components"][target_id]
        if comp.get("state") == "STORED":
            print(f"[MOCK LAB] Refusing move: {target_id} is STORED (use Place from storage).")
            return

        tx = float(target_pose.get("target_x", target_pose.get("x", 0)))
        ty = float(target_pose.get("target_y", target_pose.get("y", 0)))
        trot = float(target_pose.get("rotation", 0))

        if comp.get("state") == "PLACED" and is_storage_region(tx, ty):
            print(f"[MOCK LAB] Refusing move: target ({tx},{ty}) is in storage quadrant (use Store).")
            return

        # 1. Lock
        state["system_status"] = "BUSY"
        self._write_state(state)

        # 2. Simulate Delay
        await asyncio.sleep(2)

        # 3. Update State
        state = self._read_state()
        comp = state["components"][target_id]

        noise_x = random.uniform(-0.5, 0.5)
        noise_y = random.uniform(-0.5, 0.5)

        comp["state"] = "PLACED"
        comp["pose"] = {
            "x": tx + noise_x,
            "y": ty + noise_y,
            "rotation": trot,
        }
        comp["intent"] = {
            "nominal_pose": {"x": tx, "y": ty, "rotation": trot},
            "placement_strategy": "MANUAL",
            "last_optimized_pose": None,
            "is_optimized": False,
        }
        comp.setdefault("metadata", {}).pop("storage_slot", None)

        state["last_updated"] = datetime.now().isoformat()
        state["system_status"] = "IDLE"
        self._write_state(state)
        print(f"[MOCK LAB] Moved {target_id} to ({comp['pose']['x']:.2f}, {comp['pose']['y']:.2f})")

    async def move_motor(self, target_id: str, motor_id: int, distance: float):
        print(f"[MOCK LAB] Moving motor {motor_id} of {target_id} by {distance}...")
        state = self._read_state()
        comp = (state.get("components") or {}).get(target_id)
        if comp and comp.get("state") == "STORED":
            print(f"[MOCK LAB] Refusing motor move: {target_id} is STORED.")
            return
        meta = self._catalog_meta_for_tag(target_id)
        mids = (meta or {}).get("motor_ids") or []
        if not meta or motor_id not in mids:
            print(f"[MOCK LAB] Error: motor_id {motor_id} invalid for {target_id} (motor_ids={mids}).")
            return

        # Lock
        state = self._read_state()
        state["system_status"] = "BUSY"
        self._write_state(state)
        
        await asyncio.sleep(1)
        
        motor_rot.add_delta(target_id, motor_id, float(distance))

        # Unlock
        state = self._read_state()
        state["system_status"] = "IDLE"
        self._write_state(state)
        print(f"[MOCK LAB] Motor move complete.")

    async def motor_send_home(self, target_id: str, motor_id: int):
        meta = self._catalog_meta_for_tag(target_id)
        mids = (meta or {}).get("motor_ids") or []
        if not meta or motor_id not in mids:
            print(f"[MOCK LAB] motor_send_home: invalid tag or motor_id for {target_id} m{motor_id}")
            return
        cur = motor_rot.get_angle(target_id, motor_id)
        if abs(cur) < 1e-12:
            return
        await self.move_motor(target_id, motor_id, -cur)

    async def motor_set_zero(self, target_id: str, motor_id: int):
        meta = self._catalog_meta_for_tag(target_id)
        mids = (meta or {}).get("motor_ids") or []
        if not meta or motor_id not in mids:
            print(f"[MOCK LAB] motor_set_zero: invalid tag or motor_id for {target_id} m{motor_id}")
            return
        motor_rot.set_zero(target_id, motor_id)
        print(f"[MOCK LAB] Motor {motor_id} on {target_id}: zero reference set (software).")

    async def optimize_component(self, target_id: str, strategy: str, params: Dict[str, Any]):
        print(f"[MOCK LAB] Optimizing {target_id} with {strategy}...")
        state = self._read_state()
        comp = (state.get("components") or {}).get(target_id)
        if comp and comp.get("state") == "STORED":
            print(f"[MOCK LAB] Refusing optimize: {target_id} is STORED.")
            return

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

    async def store_component(self, target_id: str):
        print(f"[MOCK LAB] Storing {target_id}...")
        state = self._read_state()
        comp = (state.get("components") or {}).get(target_id)
        if not comp:
            print(f"[MOCK LAB] store: {target_id} not in state")
            return
        if comp.get("state") != "PLACED":
            print(f"[MOCK LAB] store: {target_id} must be PLACED (got {comp.get('state')})")
            return
        w, h = self._get_component_wh(target_id)
        slot = find_storage_slot_and_center(
            state.get("components") or {},
            target_id,
            w,
            h,
            lambda tid: self._get_component_wh(tid),
        )
        if not slot:
            print("[MOCK LAB] No free storage slot in Q3.")
            return
        x, y, si, sj = slot
        rot = STORAGE_NOMINAL_ROTATION_DEG
        state["system_status"] = "BUSY"
        self._write_state(state)
        await asyncio.sleep(1.5)
        state = self._read_state()
        comp = state["components"][target_id]
        comp["state"] = "STORED"
        comp["pose"] = {"x": x, "y": y, "rotation": rot}
        comp["intent"] = {
            "nominal_pose": {"x": x, "y": y, "rotation": rot},
            "placement_strategy": "STORAGE",
            "last_optimized_pose": None,
            "is_optimized": False,
        }
        md = comp.setdefault("metadata", {})
        md["storage_slot"] = {"i": si, "j": sj}
        state["system_status"] = "IDLE"
        state["last_updated"] = datetime.now().isoformat()
        self._write_state(state)
        print(f"[MOCK LAB] Stored {target_id} at ({x:.1f}, {y:.1f}) slot=({si},{sj})")

    async def place_from_storage(self, target_id: str, target_pose: Dict[str, float]):
        print(f"[MOCK LAB] Place from storage {target_id}...")
        state = self._read_state()
        comp = (state.get("components") or {}).get(target_id)
        if not comp or comp.get("state") != "STORED":
            print(f"[MOCK LAB] place_from_storage: {target_id} not STORED")
            return
        tx = float(target_pose.get("target_x", target_pose.get("x", 0)))
        ty = float(target_pose.get("target_y", target_pose.get("y", 0)))
        trot = float(target_pose.get("rotation", 0))
        if not is_placed_region(tx, ty):
            print("[MOCK LAB] Target must be outside storage quadrant (x<0 and y<0).")
            return
        state["system_status"] = "BUSY"
        self._write_state(state)
        await asyncio.sleep(2)
        state = self._read_state()
        comp = state["components"][target_id]
        noise_x = random.uniform(-0.5, 0.5)
        noise_y = random.uniform(-0.5, 0.5)
        comp["state"] = "PLACED"
        comp["pose"] = {"x": tx + noise_x, "y": ty + noise_y, "rotation": trot}
        comp["intent"] = {
            "nominal_pose": {"x": tx, "y": ty, "rotation": trot},
            "placement_strategy": "MANUAL",
            "last_optimized_pose": None,
            "is_optimized": False,
        }
        comp.setdefault("metadata", {}).pop("storage_slot", None)
        state["system_status"] = "IDLE"
        state["last_updated"] = datetime.now().isoformat()
        self._write_state(state)
        print(f"[MOCK LAB] Placed from storage {target_id}")

    async def add_component_to_state(self, component_data: Dict[str, Any]):
        print(f"[MOCK LAB] Adding component: {component_data}")
        state = self._read_state()

        comp_type = component_data.get("type", "OPTICAL_MIRROR")
        tag_id = component_data.get("tag_id")

        if not tag_id:
            print("[MOCK LAB] Error: No tag_id provided for add_component")
            return

        if "components" not in state:
            state["components"] = {}

        if tag_id in state["components"]:
            print(f"[MOCK LAB] Component {tag_id} already exists. Skipping placement.")
            return

        placement_mode = (component_data.get("placement_mode") or "breadboard").lower()
        w, h = self._get_component_wh(tag_id)

        if placement_mode == "storage":
            slot = find_storage_slot_and_center(
                state.get("components") or {},
                tag_id,
                w,
                h,
                lambda tid: self._get_component_wh(tid),
            )
            if not slot:
                print("[MOCK LAB] FAILED to find free storage slot.")
                return
            x, y, si, sj = slot
            st = "STORED"
            strat = "STORAGE"
            meta_extra = {"storage_slot": {"i": si, "j": sj}}
        else:
            pos = random_placed_position(
                state.get("components") or {},
                tag_id,
                w,
                h,
                lambda tid: self._get_component_wh(tid),
            )
            if not pos:
                print("[MOCK LAB] FAILED to find free pose for component.")
                return
            x, y = pos
            st = "PLACED"
            strat = "MANUAL"
            meta_extra = {}

        state["components"][tag_id] = {
            "id": tag_id,
            "type": comp_type,
            "state": st,
            "pose": {"x": x, "y": y, "rotation": 0},
            "intent": {
                "nominal_pose": {"x": x, "y": y, "rotation": 0},
                "placement_strategy": strat,
                "last_optimized_pose": None,
                "is_optimized": False,
            },
            "metadata": dict(meta_extra),
        }

        state["last_updated"] = datetime.now().isoformat()
        self._write_state(state)
        print(f"[MOCK LAB] Added {tag_id} at ({x:.1f}, {y:.1f}) state={st}")

    async def affirm_placed_at_current(self, target_id: str):
        print(f"[MOCK LAB] affirm_placed_at_current {target_id}...")
        state = self._read_state()
        comp = (state.get("components") or {}).get(target_id)
        if not comp or comp.get("state") != "STORED":
            print(f"[MOCK LAB] affirm: {target_id} must be STORED")
            return
        state["system_status"] = "BUSY"
        self._write_state(state)
        await asyncio.sleep(0.3)
        state = self._read_state()
        comp = state["components"][target_id]
        comp["state"] = "PLACED"
        comp.setdefault("metadata", {}).pop("storage_slot", None)
        comp["intent"]["placement_strategy"] = "MANUAL"
        state["system_status"] = "IDLE"
        state["last_updated"] = datetime.now().isoformat()
        self._write_state(state)
        print(f"[MOCK LAB] {target_id} marked PLACED at current pose")

    async def repack_storage_slot(self, target_id: str):
        print(f"[MOCK LAB] repack_storage_slot {target_id}...")
        state = self._read_state()
        comp = (state.get("components") or {}).get(target_id)
        if not comp or comp.get("state") != "STORED":
            print(f"[MOCK LAB] repack: {target_id} must be STORED")
            return
        w, h = self._get_component_wh(target_id)
        slot = find_storage_slot_and_center(
            state.get("components") or {},
            target_id,
            w,
            h,
            lambda tid: self._get_component_wh(tid),
        )
        if not slot:
            print("[MOCK LAB] repack: no free storage slot")
            return
        x, y, si, sj = slot
        rot = STORAGE_NOMINAL_ROTATION_DEG
        state["system_status"] = "BUSY"
        self._write_state(state)
        await asyncio.sleep(1.0)
        state = self._read_state()
        comp = state["components"][target_id]
        comp["pose"] = {"x": x, "y": y, "rotation": rot}
        comp["intent"] = {
            "nominal_pose": {"x": x, "y": y, "rotation": rot},
            "placement_strategy": "STORAGE",
            "last_optimized_pose": None,
            "is_optimized": False,
        }
        comp.setdefault("metadata", {})["storage_slot"] = {"i": si, "j": sj}
        state["system_status"] = "IDLE"
        state["last_updated"] = datetime.now().isoformat()
        self._write_state(state)
        print(f"[MOCK LAB] Repacked {target_id} to ({x:.1f},{y:.1f}) slot=({si},{sj})")

    async def recenter_stored_in_inventory(self, target_id: str):
        print(f"[MOCK LAB] recenter_stored_in_inventory {target_id}...")
        state = self._read_state()
        comp = (state.get("components") or {}).get(target_id)
        if not comp or comp.get("state") != "STORED":
            print(f"[MOCK LAB] recenter: {target_id} must be STORED")
            return
        nom = nominal_center_pose_for_stored_entry(comp)
        if nom is None:
            print("[MOCK LAB] recenter: could not resolve storage cell (need slot metadata or pose in Q3).")
            return
        x, y, si, sj = nom
        rot = STORAGE_NOMINAL_ROTATION_DEG
        state["system_status"] = "BUSY"
        self._write_state(state)
        await asyncio.sleep(1.0)
        state = self._read_state()
        comp = state["components"][target_id]
        comp["pose"] = {"x": x, "y": y, "rotation": rot}
        comp["intent"] = {
            "nominal_pose": {"x": x, "y": y, "rotation": rot},
            "placement_strategy": "STORAGE",
            "last_optimized_pose": None,
            "is_optimized": False,
        }
        comp.setdefault("metadata", {})["storage_slot"] = {"i": si, "j": sj}
        state["system_status"] = "IDLE"
        state["last_updated"] = datetime.now().isoformat()
        self._write_state(state)
        print(f"[MOCK LAB] Recentered {target_id} at ({x:.1f},{y:.1f}) slot=({si},{sj}) rot=0°")

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
