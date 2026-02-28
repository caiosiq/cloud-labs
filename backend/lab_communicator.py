import os
import json
import asyncio
import random
from datetime import datetime
from typing import Dict, Any, Optional

# Constants
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEMAS_DIR = os.path.join(BASE_DIR, "..", "schemas")
LAB_STATE_FILE = os.path.abspath(os.path.join(SCHEMAS_DIR, "lab_state.json"))
CATALOG_FILE = os.path.abspath(os.path.join(SCHEMAS_DIR, "component_catalog.json"))

class LabCommunicator:
    """
    Abstract Base Class for Lab Communication.
    """
    def get_lab_state(self) -> Dict[str, Any]:
        raise NotImplementedError

    async def move_component(self, target_id: str, target_pose: Dict[str, float]):
        raise NotImplementedError

    async def optimize_component(self, target_id: str, strategy: str, params: Dict[str, Any]):
        raise NotImplementedError

    async def remove_component(self, target_id: str):
        raise NotImplementedError

    def get_video_feed_status(self) -> Dict[str, Any]:
        return {"connected": True, "source": "/static/mock_feed.svg"}

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

    def _write_default_state(self):
        print("[MOCK LAB] Creating default state...")
        default_state = {
            "system_status": "IDLE",
            "last_updated": datetime.now().isoformat(),
            "components": {
            "tag_22": {
              "id": "tag_22",
              "type": "OPTICAL_MIRROR",
              "state": "PLACED",
              "pose": { "x": 234.52, "y": 239.87, "rotation": 45 },
              "intent": { "nominal_pose": { "x": 235, "y": 240, "rotation": 45 }, "placement_strategy": "MANUAL", "last_optimized_pose": None, "is_optimized": False },
              "metadata": { "last_optimization_score": 0.99 }
            },
            "tag_11": {
              "id": "tag_11",
              "type": "OPTICAL_LENS",
              "state": "PLACED",
              "pose": { "x": 478.45, "y": 396.33, "rotation": 0 },
              "intent": { "nominal_pose": { "x": 478, "y": 396.5, "rotation": 0 }, "placement_strategy": "MANUAL", "last_optimized_pose": None, "is_optimized": False },
              "metadata": {}
            },
            "tag_33": {
              "id": "tag_33",
              "type": "OPTICAL_BEAMSPLITTER",
              "state": "PLACED",
              "pose": { "x": 773.74, "y": 293.02, "rotation": 90 },
              "intent": { "nominal_pose": { "x": 774, "y": 293, "rotation": 90 }, "placement_strategy": "MANUAL", "last_optimized_pose": None, "is_optimized": False },
              "metadata": { "last_optimization_score": 0.99 }
            },
            "tag_22_cam": {
              "id": "tag_22_cam",
              "type": "OPTICAL_CAMERA",
              "state": "PLACED",
              "pose": { "x": 621.58, "y": 200.22, "rotation": 180 },
              "intent": { "nominal_pose": { "x": 622, "y": 200, "rotation": 180 }, "placement_strategy": "MANUAL", "last_optimized_pose": None, "is_optimized": False },
              "metadata": { "port": 1, "last_optimization_score": 0.99 }
            }
          }
        }
        self._write_state(default_state)

    def _read_state(self) -> Dict[str, Any]:
        with open(self.state_file, "r") as f:
            return json.load(f)

    def _write_state(self, state: Dict[str, Any]):
        with open(self.state_file, "w") as f:
            json.dump(state, f, indent=2)

    def get_lab_state(self) -> Dict[str, Any]:
        return self._read_state()

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
        
        # Determine Component Type if new
        comp_type = "OPTICAL_MIRROR" # Default
        if "type" in target_pose:
            comp_type = target_pose["type"]
        elif target_id in state["components"]:
            comp_type = state["components"][target_id]["type"]

        if target_id not in state["components"]:
            print(f"[MOCK LAB] Error: Cannot move component {target_id} - Not found in Lab State.")
            self._write_state(state) # Release lock (write back unmodified state or with system_status IDLE)
            # We need to ensure system_status is reset to IDLE if we return early
            state["system_status"] = "IDLE"
            self._write_state(state)
            return

        # If already exists, preserve its type
        comp_type = state["components"][target_id]["type"]

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
                
                # Simple AABB collision check
                # Check if distance is less than sum of half-sizes (assuming squares)
                # If |x1 - x2| < (s1/2 + s2/2) AND |y1 - y2| < (s1/2 + s2/2)
                min_dist_x = (my_size / 2) + (other_size / 2)
                min_dist_y = (my_size / 2) + (other_size / 2)
                
                if abs(x - cx) < min_dist_x and abs(y - cy) < min_dist_y:
                    valid_pose = False
                    break
            
            attempts += 1
            
        if not valid_pose:
            print("[MOCK LAB] FAILED to find free space for component after 100 attempts.")
            return # Should probably signal error to UI

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

    def get_video_feed_status(self) -> Dict[str, Any]:
        return {"connected": True, "source": "/api/video-feed/stream"}
