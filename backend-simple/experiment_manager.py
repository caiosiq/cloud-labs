import time
import sys
import os
try:
    import pyrealsense2 as rs
except ImportError:
    rs = None
try:
    import cv2
except ImportError:
    cv2 = None
import numpy as np
from typing import Dict, List, Optional, Callable, Any, Tuple
import json
from ..objects.base import OpticalComponent, Pose
from ..objects.sensors import PlacementSensor
from ..drivers.xarm_driver import XArmDriver
from ..drivers.camera_driver import CameraDriver
from ..drivers.wifi_stepper import WiFiStepperController
from ..managers.vision_manager import VisionManager
from ..managers.robot_manager import AssemblyManager
from ..managers.calibration import CalibrationManager
from ..managers.alignment import AlignmentOptimizer

from ..objects.strategies import OptimizationStrategy


def get_components_current_locations(
    components: List[OpticalComponent],
) -> Dict[str, Any]:
    """
    Returns current_location for each component.
    Keys are component names; values are Pose or None.
    """
    out = {}
    for c in components:
        out[c.name] = c.current_location
    return out


def print_components_current_locations(components: List[OpticalComponent]) -> None:
    """Prints name and current_location for each component."""
    print("\n--- Current locations of components ---")
    for c in components:
        loc = c.current_location
        if loc is None:
            print(f"  {c.name}: None")
        else:
            print(f"  {c.name}: x={loc.x:.3f}, y={loc.y:.3f}, z={loc.z:.3f} (roll={loc.roll}, pitch={loc.pitch}, yaw={loc.yaw})")
    print("---")


class OpticalExperiment:
    """
    High-level manager for Optical Assembly Experiments.
    Encapsulates the complexity of drivers, managers, and calibration.
    Allows scripts to be written in terms of "Physical Ideas" (Scan, Place, Optimize).
    """
    
    # Configuration Constants
    ROBOT_IP = "192.168.1.241"
    WIFI_STEPPER_IPS = ["192.168.2.192", "192.168.2.9"]
    
    # Camera Ports
    CAM_GRIPPER_1 = 1
    CAM_GRIPPER_2 = 2
    CAM_CEILING_1 = 0
    CAM_CEILING_2 = 5  

    def __init__(self, mock: bool = False):
        self.mock = mock
        self.pipeline = None
        
        print("=== Initializing Optical Experiment ===")
        self._setup_drivers()
        self._setup_managers()
        
        # State
        self.initial_positions: Dict[int, Pose] = {}   # Latest scanned position (updated on rescan)
        self.original_positions: Dict[int, Pose] = {}  # First scanned position (never overwritten; used for return-to-inventory)
        self.accumulated_movement = 0.0
        self.memory: Dict[str, Any] = {} # Shared memory for strategies

    def finish(self):
        print("\n=== Experiment Complete ===")
        # Cleanup if needed
        # Close Realsense
        if self.pipeline:
            try:
                self.pipeline.stop()
            except:
                pass
        
        # Close Cameras
        if self.camera_gripper_1: self.camera_gripper_1.release()
        if self.camera_gripper_2: self.camera_gripper_2.release()
        if self.ceiling_cam1: self.ceiling_cam1.release()
        if self.ceiling_cam2: self.ceiling_cam2.release()
        
        # Close Robot
        if self.robot: self.robot.disconnect()

    def get_camera(self, port: int) -> Optional[CameraDriver]:
        """Returns the camera driver instance associated with the given port."""
        if port == self.CAM_GRIPPER_1: return self.camera_gripper_1
        if port == self.CAM_GRIPPER_2: return self.camera_gripper_2
        if port == self.CAM_CEILING_1: return self.ceiling_cam1
        if port == self.CAM_CEILING_2: return self.ceiling_cam2
        return None

    def _setup_drivers(self):
        print(f"Connecting to Robot at {self.ROBOT_IP}...")
        self.robot = XArmDriver(ip=self.ROBOT_IP, mock=self.mock)
        
        print(f"Connecting to Cameras...")
        self.camera_gripper_1 = CameraDriver(camera_id=self.CAM_GRIPPER_1, mock=self.mock)
        self.camera_gripper_2 = CameraDriver(camera_id=self.CAM_GRIPPER_2, mock=self.mock)
        
        # Ceiling cameras use MSMF backend for higher res/stability on Windows
        backend = cv2.CAP_MSMF if cv2 else None
        self.ceiling_cam1 = CameraDriver(camera_id=self.CAM_CEILING_1, mock=self.mock, width=3840, height=2160, backend=backend)
        self.ceiling_cam2 = CameraDriver(camera_id=self.CAM_CEILING_2, mock=self.mock, width=3840, height=2160, backend=backend)
        
        print(f"Connecting to Steppers...")
        self.wifi_stepper1 = WiFiStepperController(ip_address=self.WIFI_STEPPER_IPS[0], mock=self.mock)
        self.wifi_stepper2 = WiFiStepperController(ip_address=self.WIFI_STEPPER_IPS[1], mock=self.mock)

        # Initialize RealSense Pipeline if available and not mocking
        if not self.mock and rs is not None:
            self._initialize_realsense()

    def _initialize_realsense(self):
        try:
            self.pipeline = rs.pipeline()
            config = rs.config()
            config.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 15)
            config.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 15)
            self.pipeline.start(config)
            print("RealSense Pipeline Started.")
        except Exception as e:
            print(f"Warning: Failed to start RealSense: {e}")
            self.pipeline = None

    def _setup_managers(self):
        self.calibration_manager = CalibrationManager(calibration_file="laser_line_fit.npy")
        self.vision_manager = VisionManager() 
        
        self.assembly_manager = AssemblyManager(
            driver=self.robot, 
            camera=self.camera_gripper_1, 
            secondary_camera=self.camera_gripper_2, 
            ceiling_camera=self.ceiling_cam1,
            ceiling_secondary=self.ceiling_cam2,
            vision_manager=self.vision_manager, 
            calibration_manager=self.calibration_manager
        )
        
        self.optimizer = AlignmentOptimizer()

    def initialize_robot(self):
        """Homes the robot and prepares for assembly."""
        self.assembly_manager.initialize()

    def scan_components(self, components: List[OpticalComponent], force_rescan: bool = False):
        """
        Scans for the provided list of components.
        Stores found locations in self.initial_positions.

        If force_rescan is True, re-scans even when a position for that tag_id
        already exists (e.g. after the tray has moved or components were re-scanned).
        """
        print("\n--- Scanning Inventory ---")

        for comp in components:
            tag_id = int(comp.id)  # Assuming ID is tag ID
            name = comp.name

            if not force_rescan and tag_id in self.initial_positions:
                continue

            pose = self.assembly_manager.scan_inventory(tag_id, marker_length=comp.marker_length)
            if pose:
                self.initial_positions[tag_id] = pose
                comp.inventory_location = pose
                print(f"Found {name} (ID {tag_id}) at {pose}")
            else:
                print(f"Warning: {name} (ID {tag_id}) not found.")
                if self.mock:
                    # Mock position for testing
                    mock_pose = Pose(200, 0, 0)
                    self.initial_positions[tag_id] = mock_pose
                    comp.inventory_location = mock_pose

    def place_component(self, component: OpticalComponent, target_y: float, angle: List[float] = None, x_offset: float = 0.0):
        """
        Places a component at a target Y coordinate (converted to robot X).
        """
        if angle is None:
            angle = [127.28, 127.28, 0.0]
            
        tag_id = component.tag_id
        name = component.name
        
        if tag_id is None:
             print(f"Error: Cannot place {name}. No Tag ID.")
             return
        
        # Resolve Inventory Location
        if tag_id in self.initial_positions:
            init_pose = self.initial_positions[tag_id]
            component.inventory_location = init_pose
        elif component.inventory_location is None:
            print(f"Error: Cannot place {name}. Inventory location unknown (Scan first).")
            return

        # Calculate Target
        target_x = self.calibration_manager.camera_y_to_robot_x(target_y)
        target_x += x_offset
        
        x_adjust = self.calibration_manager.calculate_x_adjust(component.inventory_location.x, component.inventory_location.y)
        
        print(f"\n--- Placing {name} ---")
        print(f"Moving {name} from {component.inventory_location} to X={target_x:.2f}, Y={target_y:.2f}")
        
        result = self.assembly_manager.pick_and_place(
            component=component, 
            pipeline=self.pipeline, 
            x_adjust=x_adjust, 
            place_spatial_position=[target_x, target_y], 
            place_spatial_angle=angle, 
            use_vision=not self.mock,
        )
        if result is not None:
            pick_up_pose, rotation = result
            if pick_up_pose is not None and len(pick_up_pose) >= 3 and tag_id not in self.original_positions:
                self.original_positions[tag_id] = Pose(pick_up_pose[0], pick_up_pose[1], pick_up_pose[2])

        component.current_location = Pose(target_x, target_y, component.inventory_location.z)
        component.is_placed = True
        pick_up_pose, rotation = result
        return pick_up_pose, rotation


    def scan_with_gripper(self, components: List[OpticalComponent]):
        """
        Places a component at a target Y coordinate (converted to robot X).
        """
        print("\n--- Scanning with Gripper ---")
        for component in components:
            tag_id = component.tag_id
            name = component.name
            
            if tag_id is None:
                print(f"Error: Cannot place {name}. No Tag ID.")
                return
            
            x_adjust = self.calibration_manager.calculate_x_adjust(component.inventory_location.x, component.inventory_location.y)

        
            
            # Resolve Inventory Location
            if tag_id in self.initial_positions:
                init_pose = self.initial_positions[tag_id]
                component.inventory_location = init_pose
            elif component.inventory_location is None:
                print(f"Error: Cannot place {name}. Inventory location unknown (Scan first).")
                return

            result = self.assembly_manager.detect_before_pick(
                component=component, 
                pipeline=self.pipeline, 
                x_adjust=x_adjust,
                use_vision=not self.mock,
            )
            
            if result is not None:
                pick_up_pose, rotation = result
                if pick_up_pose is not None and len(pick_up_pose) >= 3 and tag_id not in self.original_positions:
                    self.original_positions[tag_id] = Pose(pick_up_pose[0], pick_up_pose[1], pick_up_pose[2])

            component.current_location = Pose(pick_up_pose[0], pick_up_pose[1], component.inventory_location.z)
            component.is_placed = True
        ## save the current location to the json file (same format as current_location.json)
        current_locs = get_components_current_locations(components)
        def _pose_to_dict(pose):
            if pose is None:
                return None
            return {"x": float(pose.x), "y": float(pose.y), "z": float(pose.z),
                    "roll": float(pose.roll), "pitch": float(pose.pitch), "yaw": float(pose.yaw)}
        data = {name: _pose_to_dict(loc) for name, loc in current_locs.items()}
        with open('current_location_with_gripper.json', 'w') as f:
            json.dump(data, f, indent=2)
        return data

    def load_current_location(self, component_names: List[str]) -> Dict[str, Any]:
        """
        Loads coordinate data from the JSON file for the requested component names.
        Returns a dict mapping each name to its pose data (dict with x, y, z, roll, pitch, yaw) or None.
        """
        with open('current_location.json', 'r') as f:
            data = json.load(f)
        return {name: data.get(name) for name in component_names}

    def compare_current_locations(self, number_of_return_components: int, current_locations_before_monitor: Dict[str, Any], current_locations_after_monitor: Dict[str, Any]) -> List[str]:
        """
        Computes squared distance (delta_x**2 + delta_y**2) for components present in both dicts.
        Returns the top number_of_return_components (e.g. 2) component names with the biggest difference.
        Skips components that are missing from either dict or have None as location.
        """
        common_names = set(current_locations_before_monitor) & set(current_locations_after_monitor)
        name_to_diff = []
        for name in common_names:
            before = current_locations_before_monitor[name]
            after = current_locations_after_monitor[name]
            if before is None or after is None:
                continue
            delta_x = before["x"] - after["x"]
            delta_y = before["y"] - after["y"]
            diff_sq = delta_x ** 2 + delta_y ** 2
            name_to_diff.append((name, diff_sq))
        name_to_diff.sort(key=lambda p: p[1], reverse=True)
        return [name for name, _ in name_to_diff[:number_of_return_components]]

  
    
    def place_component_wo_home(self, component: OpticalComponent, target_y: float, angle: List[float] = None, x_offset: float = 0.0, y_offset: float = 0.0):
        """
        Places a component at a target Y coordinate (converted to robot X).
        """
        if angle is None:
            angle = [127.28, 127.28, 0.0]
            
        tag_id = component.tag_id
        name = component.name
        
        if tag_id is None:
             print(f"Error: Cannot place {name}. No Tag ID.")
             return
        
        # Resolve Inventory Location
        if tag_id in self.initial_positions:
            init_pose = self.initial_positions[tag_id]
            component.inventory_location = init_pose
        elif component.inventory_location is None:
            print(f"Error: Cannot place {name}. Inventory location unknown (Scan first).")
            return

        # Calculate Target
        target_x = self.calibration_manager.camera_y_to_robot_x(target_y)
        target_x += x_offset
        target_y += y_offset
        
        x_adjust = self.calibration_manager.calculate_x_adjust(component.inventory_location.x, component.inventory_location.y)
        
        print(f"\n--- Placing {name} ---")
        print(f"Moving {name} from {component.inventory_location} to X={target_x:.2f}, Y={target_y:.2f}")
        
        result = self.assembly_manager.pick_and_place_wo_home(
            component=component, 
            pipeline=self.pipeline, 
            x_adjust=x_adjust, 
            place_spatial_position=[target_x, target_y], 
            place_spatial_angle=angle, 
            use_vision=not self.mock
        )
        if result is not None:
            pick_up_pose, rotation = result
            if pick_up_pose is not None and len(pick_up_pose) >= 3 and tag_id not in self.original_positions:
                self.original_positions[tag_id] = Pose(pick_up_pose[0], pick_up_pose[1], pick_up_pose[2])

        component.current_location = Pose(target_x, target_y, component.inventory_location.z)
        component.is_placed = True

    def place_component_wo_home_specific_xy(self, component: OpticalComponent, target_y: float, target_x: float, angle: List[float] = None):
        """
        Places a component at a target Y coordinate (converted to robot X).
        """
        if angle is None:
            angle = [127.28, 127.28, 0.0]
            
        tag_id = component.tag_id
        name = component.name
        
        if tag_id is None:
             print(f"Error: Cannot place {name}. No Tag ID.")
             return
        
        # Resolve Inventory Location
        if tag_id in self.initial_positions:
            init_pose = self.initial_positions[tag_id]
            component.inventory_location = init_pose
        elif component.inventory_location is None:
            print(f"Error: Cannot place {name}. Inventory location unknown (Scan first).")
            return
        
        x_adjust = self.calibration_manager.calculate_x_adjust(component.inventory_location.x, component.inventory_location.y)
        
        print(f"\n--- Placing {name} ---")
        print(f"Moving {name} from {component.inventory_location} to X={target_x:.2f}, Y={target_y:.2f}")
        
        result = self.assembly_manager.pick_and_place_wo_home(
            component=component, 
            pipeline=self.pipeline, 
            x_adjust=x_adjust, 
            place_spatial_position=[target_x, target_y], 
            place_spatial_angle=angle, 
            use_vision=not self.mock
        )
        if result is not None:
            pick_up_pose, rotation = result
            if pick_up_pose is not None and len(pick_up_pose) >= 3 and tag_id not in self.original_positions:
                self.original_positions[tag_id] = Pose(pick_up_pose[0], pick_up_pose[1], pick_up_pose[2])

        component.current_location = Pose(target_x, target_y, component.inventory_location.z)
        component.is_placed = True





    def place_component_and_rotate(self, component: OpticalComponent, target_y: float, angle: List[float] = None, x_offset: float = 0.0, y_offset: float = 0.0, start_angle: float = 0.0, unit_step: float = 0.2, unit_step_number: int = 20):
        """
        Places a component at a target Y coordinate (converted to robot X).
        """
        if angle is None:
            angle = [127.28, 127.28, 0.0]
            
        tag_id = component.tag_id
        name = component.name
        
        if tag_id is None:
             print(f"Error: Cannot place {name}. No Tag ID.")
             return
        
        # Resolve Inventory Location
        if tag_id in self.initial_positions:
            init_pose = self.initial_positions[tag_id]
            component.inventory_location = init_pose
        elif component.inventory_location is None:
            print(f"Error: Cannot place {name}. Inventory location unknown (Scan first).")
            return

        # Calculate Target
        target_x = self.calibration_manager.camera_y_to_robot_x(target_y)
        target_x += x_offset
        target_y += y_offset
        
        x_adjust = self.calibration_manager.calculate_x_adjust(component.inventory_location.x, component.inventory_location.y)
        
        print(f"\n--- Placing {name} ---")
        print(f"Moving {name} from {component.inventory_location} to X={target_x:.2f}, Y={target_y:.2f}")
        
        pickup_pos,rotation,found_meaningful_place,M2,useful_mean,final_best_angle = self.assembly_manager.pick_and_place_wo_home_and_rotate(
            component=component, 
            pipeline=self.pipeline, 
            x_adjust=x_adjust, 
            place_spatial_position=[target_x, target_y], 
            place_spatial_angle=angle, 
            start_angle=start_angle,
            unit_step=unit_step,
            unit_step_number=unit_step_number,
            use_vision=not self.mock
        )
        
        component.current_location = Pose(target_x, target_y, component.inventory_location.z)
        component.is_placed = True

        return pickup_pos,rotation,found_meaningful_place,M2,useful_mean,final_best_angle


    def remove_component(self, component: OpticalComponent, target_y: float = None, angle: List[float] = None):
        """
        Removes a component from the table and returns it to inventory.
        """
        if angle is None:
            angle = [127.28, 127.28, 0.0]

        tag_id = component.tag_id
        name = component.name
        if component.current_location:
            current_x = component.current_location.x
            current_y = component.current_location.y
        elif target_y is not None:
             # Fallback if state lost
             current_x = self.calibration_manager.camera_y_to_robot_x(target_y)
             current_y = target_y
        else:
            print(f"Error: Cannot remove {name}. Current location unknown.")
            return

        if tag_id is None or tag_id not in self.original_positions:
             print(f"Error: Cannot remove {name}. Original inventory location unknown.")
             return
        print(current_x, current_y, "current location")
        dest_pose = self.original_positions[tag_id]
        print(f"Destination (return-to-inventory): {dest_pose}")
        x_adjust = self.calibration_manager.calculate_x_adjust(current_x, current_y)

        component_new = component
        all_components = [component]
        self.scan_components(all_components, force_rescan=True)

        pickup_pos, rotation = self.assembly_manager.pick_and_place(
            component=component_new,
            pipeline=self.pipeline,
            x_adjust=x_adjust,
            place_spatial_position=[dest_pose.x, dest_pose.y],
            place_spatial_angle=angle,
            use_vision=not self.mock
        )
        







    # def remove_component(self, component: OpticalComponent, target_y: float = None, angle: List[float] = None):
    #     """
    #     Removes a component from the table and returns it to inventory.
    #     """
    #     if angle is None:
    #         angle = [127.28, 127.28, 0.0]

    #     tag_id = component.tag_id
    #     name = component.name
        
    #     if tag_id is None or tag_id not in self.initial_positions:
    #          print(f"Error: Cannot remove {name}. Original inventory location unknown.")
    #          return
             
    #     dest_pose = self.initial_positions[tag_id]
        
    #     # Current Location
    #     if component.current_location:
    #         current_x = component.current_location.x
    #         current_y = component.current_location.y
    #     elif target_y is not None:
    #          # Fallback if state lost
    #          current_x = self.calibration_manager.camera_y_to_robot_x(target_y)
    #          current_y = target_y
    #     else:
    #         print(f"Error: Cannot remove {name}. Current location unknown.")
    #         return

    #     print(f"\n--- Removing {name} ---")
        
    #     # Create a temp component representing the object on the table (to be picked)
    #     source_pose = Pose(current_x, current_y, dest_pose.z)
    #     temp_comp = OpticalComponent(name, inventory_location=source_pose)
    #     temp_comp.id = str(tag_id)
        
    #     x_adjust = self.calibration_manager.calculate_x_adjust(current_x, current_y)
        
    #     print(f"Returning {name} from X={current_x:.2f}, Y={current_y:.2f} to {dest_pose}")
        
    #     self.assembly_manager.pick_and_place(
    #         component=temp_comp,
    #         pipeline=self.pipeline,
    #         x_adjust=x_adjust,
    #         place_spatial_position=[dest_pose.x, dest_pose.y],
    #         place_spatial_angle=angle,
    #         use_vision=not self.mock
    #     )
        
    #     component.is_placed = False
    #     component.current_location = None

    def optimize_component(self, component: OpticalComponent, strategy: OptimizationStrategy):
        """Runs an optimization strategy on the component."""
        print(f"\n--- Optimizing {component.name} ---")
        if not component.is_placed and not self.mock:
            print(f"Warning: {component.name} is not marked as placed. Optimization might fail.")
            
        strategy.execute(self, component)

    def capture_image(self, camera_port: int, exposure: float = 0.05) -> Optional[Any]:
        """Captures an image from the specified camera."""
        print(f"Capturing image from Camera {camera_port} (Exp: {exposure})...")
        camera = self.get_camera(camera_port)
        if camera:
            camera.set_video_exposure(exposure)
            time.sleep(0.5)
            return camera.capture()
        return None

    def analyze_beam(self, image: Any) -> Tuple[Optional[float], Optional[float]]:
        """Finds the beam centroid in the image."""
        if image is None:
            return None, None
        center, _, _ = self.vision_manager.find_beam_centroid(image)
        if center is None:
            return None, None
        return (float(center[0]), float(center[1]))



    def finish(self):
        print("\n=== Experiment Complete ===")
        # Cleanup if needed