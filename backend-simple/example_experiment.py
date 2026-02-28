import sys
import os
import time
import numpy as np

# Ensure lab_automation package is in path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from lab_automation.managers.experiment_manager import OpticalExperiment
from lab_automation.objects.base import OpticalComponent
from lab_automation.objects.strategies import NewtonPlacementStrategy, RotationalScanStrategy, CobylaAlignmentStrategy

def run_experiment(mock_mode=False):
    """
    Main script for Optical Assembly Experiment (Refactored Imperative Style).
    Reproduces logic from analyzer_laser_align.py using modular objects.
    """
    
    # 1. Initialize the Experiment Environment
    experiment = OpticalExperiment(mock=mock_mode)
    experiment.initialize_robot()

    # 2. Define Components (Objects)
    # Tag IDs based on original code
    ND = OpticalComponent("ND", tag_id=9)
    CAM1 = OpticalComponent("CAM1", tag_id=22)
    CAM2 = OpticalComponent("CAM2", tag_id=21)
    OC = OpticalComponent("OC", tag_id=18)
    BB = OpticalComponent("BB", tag_id=2)
    BS = OpticalComponent("BS", tag_id=10)
    Lens = OpticalComponent("Lens", tag_id=11)
    IC = OpticalComponent("IC", tag_id=20)
    Filter = OpticalComponent("Filter", tag_id=3)
    Crystal = OpticalComponent("Crystal", tag_id=19)
    
    # all_components = [ND, CAM1, CAM2, OC, BB, BS, Lens, IC, Filter, Crystal]
    all_components = [ND, CAM1,CAM2]

    # # 3. Scan Inventory (Find all objects first)
    experiment.scan_components(all_components)
    
    # 4. Experiment Workflow
    
    # --- Step 1: Place ND ---
    # Reduces laser intensity
    experiment.place_component(ND, target_y=235)
    
    # --- Step 2: Place CAM1 ---
    # Used to measure initial laser position
    experiment.place_component_wo_home(CAM1, target_y=-410, x_offset=-3)
    
    # Capture Reference (Laser Beam Position)
    print("\n[Analysis] Capturing Reference Laser Position from CAM1...")
    img_cam1_nd = experiment.capture_image(experiment.CAM_GRIPPER_1, exposure=0.05)
    laser_x, laser_y = experiment.analyze_beam(img_cam1_nd)
    
    if laser_x is None:
        print("Error: Could not find laser beam on CAM1. Using default center.")
        laser_x = 2744 # Default center for 5488 width
    else:
        print(f"Reference Laser Position: X={laser_x:.2f}, Y={laser_y:.2f}")
    
    # # --- Step 3: Place CAM2 ---
    experiment.place_component_wo_home(CAM2, target_y=110, x_offset=-120)
    
    # --- Step 4: Place OC (Output Coupler) ---
    experiment.place_component(OC, target_y=-200)
    
    # Optimize OC Placement (Newton)
    # Goal: Center the beam on CAM1 (match laser_x)
    experiment.optimize_component(OC, strategy=NewtonPlacementStrategy(
        camera_port=experiment.CAM_GRIPPER_1,
        target_x_pixel=laser_x,
        tolerance_ratio=0.025,
        exposure=20
    ))
    
    # --- Step 5: Place Beam Block (BB) ---
    experiment.place_component(BB, target_y=-100)
    
    # --- Step 6: Place Beam Splitter (BS) ---
    experiment.place_component(BS, target_y=110)
    
    # Optimize BS Placement (Newton)
    # Goal: Center the reflected beam on CAM2
    experiment.optimize_component(BS, strategy=NewtonPlacementStrategy(
        camera_port=experiment.CAM_GRIPPER_2,
        target_x_pixel=None, # Defaults to Center
        tolerance_ratio=0.1,
        exposure=0.05
    ))
    
    # Capture Reference for OC Alignment (The "One Beam" image)
    print("\n[Analysis] Capturing BS Reference for OC Alignment...")
    img_bs_ref = experiment.capture_image(experiment.CAM_GRIPPER_2, exposure=0.05)
    experiment.memory["bs_ref"] = img_bs_ref
    
    # --- Step 7: Remove Beam Block ---
    experiment.remove_component(BB, target_y=-100)
    
    # --- Step 8: Fine Adjust OC (Alignment) ---
    # Uses Motorized Knobs (Cobyla) to align OC reflection to BS reference
    experiment.optimize_component(OC, strategy=CobylaAlignmentStrategy(
        camera_port=experiment.CAM_GRIPPER_2,
        motor_ids=[1, 2], 
        reference_image=img_bs_ref
    ))
    
    # --- Step 9: Remove Beam Splitter ---
    experiment.remove_component(BS, target_y=110)
    
    # --- Step 10: Place Lens ---
    experiment.place_component(Lens, target_y=110)
    
    # Optimize Lens Placement (Newton)
    # Goal: Recenter beam on CAM1
    experiment.optimize_component(Lens, strategy=NewtonPlacementStrategy(
        camera_port=experiment.CAM_GRIPPER_1,
        target_x_pixel=laser_x,
        tolerance_ratio=0.1,
        exposure=20
    ))
    
    # --- Step 11: Remove ND Filter ---
    experiment.remove_component(ND, target_y=240)
    
    # --- Step 12: Place IC (Input Coupler) ---
    
    # Capture Reference (Before IC) - Average of 5 frames
    print("\n[Analysis] Capturing Reference (Before IC)...")
    imgs = []
    for _ in range(5):
        img = experiment.capture_image(experiment.CAM_GRIPPER_1, exposure=1)
        if img is not None: imgs.append(img)
    
    if imgs:
        ic_ref = np.mean(imgs, axis=0).astype(np.uint8)
    else:
        print("Warning: Failed to capture IC reference.")
        ic_ref = None
        
    experiment.place_component(IC, target_y=-20)
    
    # Align IC (Cobyla)
    # Adjusts IC knobs to minimize interference/deviation
    experiment.optimize_component(IC, strategy=CobylaAlignmentStrategy(
        camera_port=experiment.CAM_GRIPPER_1,
        motor_ids=[1, 2],
        reference_image=ic_ref
    ))
    
    # --- Step 13: Place Filter ---
    experiment.place_component(Filter, target_y=-328)
    
    # --- Step 14: Place Crystal ---
    experiment.place_component(Crystal, target_y=-110)
    
    # Optimize Crystal Placement (Newton)
    experiment.optimize_component(Crystal, strategy=NewtonPlacementStrategy(
        camera_port=experiment.CAM_GRIPPER_1,
        target_x_pixel=laser_x,
        exposure=100
    ))
    
    # Optimize Crystal Angle (Rotational Scan)
    experiment.optimize_component(Crystal, strategy=RotationalScanStrategy(
        camera_port=experiment.CAM_GRIPPER_1,
        start_angle=0,
        end_angle=5
    ))
    
    experiment.finish()

if __name__ == "__main__":
    # Check for mock flag
    mock = "--mock" in sys.argv
    run_experiment(mock_mode=mock)