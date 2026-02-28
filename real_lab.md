# Real Lab Integration Guide

This document outlines the architecture and steps required to integrate the **Optical Digital Twin** with the physical robotic laboratory, enabling the execution of experiments on real hardware.

## 1. Overview

The goal is to replace the `MockLabCommunicator` with a `RealLabCommunicator` that interfaces with the actual `lab_automation` Python package. This will allow the frontend to:
1.  **Move Components**: Send coordinates to the xArm robot.
2.  **Optimize Alignment**: Trigger real-world optimization algorithms (Newton, COBYLA) using camera feedback.
3.  **Sync State**: Reflect the physical location of components in the Digital Twin.

## 2. Prerequisites

The system assumes the following environment:
*   **Hardware**: xArm6 Robot, RealSense Camera, various optical mounts with ArUco markers.
*   **Software**: The `lab_automation` package must be installed or available in the `PYTHONPATH`.
    *   This package contains drivers (`XArmDriver`, `CameraDriver`), managers (`AssemblyManager`, `VisionManager`), and the high-level `OpticalExperiment` class.
*   **Network**: The backend server must have network access to the Robot Controller and Camera streams.

## 3. Architecture: The `RealLabCommunicator`

We will implement a new class `RealLabCommunicator` (in `backend/lab_communicator.py` or a new file) that adheres to the `LabCommunicator` interface used by `main.py`.

### 3.1. Responsibility
The `RealLabCommunicator` acts as an **Adapter**:
*   **Input**: JSON commands from `main.py` (e.g., `move_component(target_id="tag_22", x=200, y=300)`).
*   **Logic**: Translates these into `OpticalExperiment` method calls (e.g., `experiment.place_component(...)`).
*   **Output**: Updates the local `lab_state.json` with the actual final positions reported by the robot/vision system.

### 3.2. Key Imports
The communicator needs to import the high-level manager and strategy definitions:

```python
import sys
import os

# Ensure lab_automation is reachable (adjust path as needed)
# sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from lab_automation.managers.experiment_manager import OpticalExperiment
from lab_automation.objects.base import OpticalComponent, Pose
from lab_automation.objects.strategies import (
    NewtonPlacementStrategy, 
    CobylaAlignmentStrategy, 
    RotationalScanStrategy
)
```

## 4. Implementation Strategy

### 4.1. Initialization (`__init__`)
*   Initialize `OpticalExperiment(mock=False)`.
*   **Scan Inventory**: Call `experiment.scan_components()` to find all available tags on the table/inventory.
*   **Populate State**: Convert the scanned `OpticalComponent` objects into the dictionary format expected by `lab_state.json` and cache them.

### 4.2. Moving Components (`move_component`)
*   **Lookup**: Find the `OpticalComponent` object corresponding to the `target_id`.
*   **Execute**: Call `experiment.place_component_wo_home_specific_xy(...)` (or similar) to move the robot.
    *   *Note*: The Digital Twin uses a global 2D coordinate system. The `OpticalExperiment` might use a specific calibration (Robot X/Y vs Camera Y). Ensure coordinates are transformed correctly if `place_component` expects "Camera Y" instead of "Robot X/Y".
*   **Update**: After the move, update the `lab_state.json` with the new actual position.

### 4.3. Optimization (`optimize_component`)
*   **Strategy Mapping**: Map the string `strategy` (e.g., "NEWTON") to the corresponding class (`NewtonPlacementStrategy`).
*   **Parameter Injection**: Pass parameters like `camera_port`, `tolerance`, etc., into the strategy constructor.
*   **Execution**: Call `experiment.optimize_component(component, strategy_instance)`.

### 4.4. State Management (`get_lab_state`)
*   Return the cached state (which is updated after every move/optimize action).
*   Optionally, trigger a "Rescan" if the state is believed to be stale.

## 5. Code Skeleton

Here is the proposed implementation for `backend/real_lab_communicator.py`:

```python
import json
import os
import asyncio
from datetime import datetime
from typing import Dict, Any

# Import Abstract Base Class
from lab_communicator import LabCommunicator

# Import Real Lab Automation
# (Assumes lab_automation is in PYTHONPATH)
from lab_automation.managers.experiment_manager import OpticalExperiment
from lab_automation.objects.base import OpticalComponent, Pose
from lab_automation.objects.strategies import NewtonPlacementStrategy, CobylaAlignmentStrategy

class RealLabCommunicator(LabCommunicator):
    def __init__(self):
        print("[REAL LAB] Initializing OpticalExperiment...")
        self.experiment = OpticalExperiment(mock=False)
        self.experiment.initialize_robot()
        
        # Cache of OpticalComponent objects: { "tag_22": OpticalComponent(...) }
        self.component_map: Dict[str, OpticalComponent] = {}
        
        # Initialize State
        self._initialize_state()

    def _initialize_state(self):
        """Scans the table and populates the component map."""
        print("[REAL LAB] Scanning components...")
        
        # 1. Define known tags (ideally load from catalog)
        # For now, we scan for a known set or discover them
        known_tags = [9, 22, 21, 18, 2, 10, 11, 20, 3, 19] # Example IDs
        
        components_to_scan = []
        for tag_id in known_tags:
            # Create a placeholder component
            comp = OpticalComponent(name=f"Component_{tag_id}", tag_id=tag_id)
            components_to_scan.append(comp)
            self.component_map[f"tag_{tag_id}"] = comp

        # 2. Perform Scan
        self.experiment.scan_components(components_to_scan, force_rescan=True)
        
        # 3. Update internal state cache (lab_state.json format)
        self.current_state = {
            "system_status": "IDLE",
            "last_updated": datetime.now().isoformat(),
            "components": {}
        }
        
        for comp in components_to_scan:
            if comp.inventory_location:
                tag_key = f"tag_{comp.tag_id}"
                self.current_state["components"][tag_key] = {
                    "id": tag_key,
                    "type": "OPTICAL_MIRROR", # Default, needs catalog lookup
                    "state": "PLACED", # Scan finds them on table/inventory
                    "pose": {
                        "x": comp.inventory_location.x,
                        "y": comp.inventory_location.y,
                        "rotation": comp.inventory_location.yaw or 0
                    },
                    "intent": { "nominal_pose": None, "is_optimized": False },
                    "metadata": {}
                }

    def get_lab_state(self) -> Dict[str, Any]:
        return self.current_state

    async def move_component(self, target_id: str, params: Dict[str, Any]):
        print(f"[REAL LAB] Moving {target_id}...")
        
        # 1. Update Status
        self.current_state["system_status"] = "BUSY"
        
        # 2. Get Component
        if target_id not in self.component_map:
            print(f"Error: Component {target_id} not found in map.")
            self.current_state["system_status"] = "IDLE"
            return

        comp = self.component_map[target_id]
        
        # 3. Extract Coordinates
        # params comes from frontend: { "target_x": ..., "target_y": ..., "rotation": ... }
        tx = params.get("target_x")
        ty = params.get("target_y")
        rot = params.get("rotation", 0)
        
        # 4. Execute Move (Running in thread/executor recommended if blocking)
        # Note: place_component calls are synchronous in OpticalExperiment
        try:
            # Use specific XY placement
            self.experiment.place_component_wo_home_specific_xy(
                component=comp,
                target_x=tx,
                target_y=ty,
                angle=[180, 0, rot] # Robot End-Effector Orientation
            )
            
            # 5. Update State
            self.current_state["components"][target_id]["pose"] = {
                "x": tx,
                "y": ty,
                "rotation": rot
            }
            self.current_state["components"][target_id]["state"] = "PLACED"
            
        except Exception as e:
            print(f"[REAL LAB] Move Failed: {e}")
            
        finally:
            self.current_state["system_status"] = "IDLE"
            self.current_state["last_updated"] = datetime.now().isoformat()

    async def optimize_component(self, target_id: str, strategy_name: str, params: Dict[str, Any]):
        print(f"[REAL LAB] Optimizing {target_id} with {strategy_name}...")
        self.current_state["system_status"] = "OPTIMIZING"
        
        comp = self.component_map.get(target_id)
        if not comp: return

        try:
            # 1. Select Strategy
            strategy = None
            if strategy_name == "NEWTON":
                strategy = NewtonPlacementStrategy(
                    camera_port=params.get("camera_number", 1),
                    target_x_pixel=params.get("target_x_pixel"),
                    tolerance_ratio=params.get("tolerance_ratio", 0.05)
                )
            elif strategy_name == "COBYLA":
                strategy = CobylaAlignmentStrategy(
                    camera_port=params.get("camera_number", 1),
                    motor_ids=params.get("motor_ids", [1, 2]),
                    objective_threshold=params.get("objective_threshold", 100.0)
                )
            
            if strategy:
                # 2. Execute
                self.experiment.optimize_component(comp, strategy)
                
                # 3. Update State (Rotation might have changed)
                # We need to query the component's new location/status if updated by strategy
                # For now, assume optimization success
                self.current_state["components"][target_id]["intent"]["is_optimized"] = True
                
        except Exception as e:
            print(f"[REAL LAB] Optimization Failed: {e}")
            
        finally:
            self.current_state["system_status"] = "IDLE"

    def get_video_feed_status(self):
        return {"connected": True, "source": "/api/video-feed/stream"} # Stream handled separately
```

## 6. Integrating into `main.py`

Modify `backend/main.py` to select the communicator based on an environment variable.

```python
# In backend/main.py

# ... imports ...
from lab_communicator import MockLabCommunicator

# Import Real Lab (Conditional)
try:
    from real_lab_communicator import RealLabCommunicator
except ImportError:
    RealLabCommunicator = None

# ...

# Initialize Communicator
LAB_MODE = os.getenv("LAB_MODE", "MOCK").upper()

if LAB_MODE == "REAL" and RealLabCommunicator:
    print(">>> STARTING IN REAL LAB MODE <<<")
    lab = RealLabCommunicator()
else:
    print(">>> STARTING IN MOCK MODE <<<")
    lab = MockLabCommunicator()
```

## 7. Running the Experiment

1.  **Set Environment**:
    ```bash
    export LAB_MODE=REAL
    export PYTHONPATH=$PYTHONPATH:/path/to/lab_automation
    ```
2.  **Start Backend**:
    ```bash
    uvicorn main:app --host 0.0.0.0 --port 8000
    ```
3.  **Frontend**:
    The frontend will now control the real robot. Dragging a component will trigger the xArm to pick and place it. Clicking "Optimize" will run the actual beam alignment loop.
