from abc import ABC, abstractmethod
import numpy as np
import time
import datetime
from typing import Optional, List, Tuple, Callable
from scipy.optimize import fmin_cobyla, minimize
try:
    from skopt import gp_minimize
    from skopt.space import Real
    from skopt.utils import use_named_args
    from skopt.callbacks import EarlyStopper
    SKOPT_AVAILABLE = True
except ImportError:
    SKOPT_AVAILABLE = False
    print("[strategies] Warning: skopt not available. BayesianCavityAlignmentStrategy will not work.")

from .base import OpticalComponent, Pose
from ..managers.vision_manager import VisionManager
from ..drivers.camera_driver import CameraDriver
from ..managers.recorder_capture_helpers import activate_cam_and_capture, request_capture_safe, change_camera_exposure

CAM1_PORT = 9999
CAM2_PORT = 10000

class OptimizationStrategy(ABC):
    """
    Abstract base class for any optimization routine.
    
    Categories:
    1. Placement Strategies (Robot Arm): Move the component in (x,y,z) space.
    2. Alignment Strategies (Motorized Mounts): Adjust actuators on the component itself.
    """
    @abstractmethod
    def execute(self, experiment_manager, component: OpticalComponent):
        pass

class NewtonPlacementStrategy(OptimizationStrategy):
    """
    [Placement Strategy]
    Uses the Robot Arm to iteratively place the component to center the beam.
    
    Mechanism:
    - Robot picks up component.
    - Robot places component.
    - Camera measures beam deviation.
    - Robot moves relative to current position.
    - Repeats until tolerance met.
    
    Ref: determine_movement_from_cam1 logic in analyzer_laser_align.py
    """
    def __init__(self, camera_number: int, target_x_pixel: float = None, tolerance_ratio: float = 0.05, max_steps: int = 10, video_exposure: float = 20, capture_exposure: float = 20, file_name: str = "test.png",axis: str = 'x',original_position: float = 0.0,initial_move: float = 0.0,do_repositioning: bool = False):
        self.camera_number = camera_number
        self.target_x = target_x_pixel # If None, defaults to image_width/2
        self.tolerance_ratio = tolerance_ratio
        self.max_steps = max_steps
        self.video_exposure = video_exposure
        self.capture_exposure = capture_exposure
        self.file_name = file_name
        self.axis = axis.lower() # 'x' or 'y'
        self.original_position = original_position
        self.initial_move = initial_move
        self.do_repositioning = do_repositioning # do recalibration if the placement function before the optimization is "place_component", not "place_component_wo_home"
    def execute(self, experiment, component: OpticalComponent):
        print(f"--- [Strategy] Starting Newton Placement for {component.id} (Axis: {self.axis.upper()}) ---")

        
        
        # 0. readjust the position
        if self.do_repositioning:
            component_new = component
            all_components = [component]
            experiment.scan_components(all_components, force_rescan=True)
            experiment.place_component_wo_home(
                component=component_new,
                target_y=self.original_position)
    
        
        # 1. Setup Camera
        width = 5488 # Default from legacy
        height = 3672
        
        if self.target_x is not None:
            target = self.target_x
        else:
            target = width / 2 
            
        tolerance = width * self.tolerance_ratio # Using width for scale in both cases typically
        
        # 2. Optimization State
        if self.axis == 'x':
            current_pos = component.current_location.x
        else:
            current_pos = component.current_location.y
            
        prev_pos = current_pos
        prev_beam_val = 0
        prev_move = 0
        
        accumulated_move = 0.0
        
        for step in range(self.max_steps):
            # Capture & Analyze
            img = change_camera_exposure(
                    cam_id=self.camera_number,
                    video_exposure=self.video_exposure,
                    filename=self.file_name,
                    settle_s=0.5,
                    output_dir="Camera_Images",
            )
            if img is None:
                print("Error: Capture failed.")
                break
                
            centroid, _, _ = experiment.vision_manager.find_beam_centroid(img)
            if not centroid:
                print("Error: Beam not found.")
                break
                
            current_beam_val = centroid[0] 
            offset = current_beam_val - target
            print(f"Step {step}: Beam Val={current_beam_val}, Target={target}, Offset={offset:.2f}")
            
            # Check Convergence
            if abs(offset) < tolerance:
                print("Converged!")
                break
                
            # Calculate Move
            if step == 0:
                # Initial probe move (from legacy: 3mm or -1mm depending on component, let's use 2mm)
                move = self.initial_move
                if component.id == "Lens": move = -1.0 # Legacy specific
            else:
                # Newton Step
                denom = (current_beam_val - prev_beam_val)
                if abs(denom) < 1e-6:
                    print("Gradient zero, random probe.")
                    move = 1.0
                else:
                    gradient = denom / prev_move
                    move = -(current_beam_val - target) / gradient
                    
                # Safety Clamp
                move = np.clip(move, -10, 10)

            print(f"Calculated Move: {move:.2f}mm")
            
            # Execute Re-Place
            # Update state
            prev_beam_val = current_beam_val
            prev_move = move
            current_pos += move
            accumulated_move += move
            
            # Perform Physical Move (Pick -> Move -> Place)
            # We assume the component is currently PLACED at 'component.current_location'
            # We need to pick it up from there and place it at 'new_location'
            
            # Update inventory to current location (so we can pick it up)
            component.inventory_location = component.current_location
            
            # Calculate new target pose
            if self.axis == 'x':
                new_target = Pose(current_pos, component.current_location.y, component.current_location.z, 
                                  component.current_location.roll, component.current_location.pitch, component.current_location.yaw)
            else:
                 new_target = Pose(component.current_location.x, current_pos, component.current_location.z, 
                                  component.current_location.roll, component.current_location.pitch, component.current_location.yaw)
            
            # Re-calculate x_adjust? Legacy does re-calc x_adjust each time.
            component_new = component
            all_components = [component]
            experiment.scan_components(all_components, force_rescan=True)
            if self.axis == 'x':
                experiment.place_component_wo_home(
                    component=component_new,
                    target_y=self.original_position,
                    x_offset=accumulated_move,
                )
            else:
                experiment.place_component_wo_home(
                    component=component_new,   
                    target_y=self.original_position,
                    y_offset=accumulated_move
                )


            # experiment.assembly_manager.pick_and_place_wo_home(
            #     component=component,
            #     pipeline=experiment.pipeline,
            #     x_adjust=x_adjust,
            #     place_spatial_position=[new_target.x, new_target.y],
            #     place_spatial_angle=[127.28, 127.28, new_target.yaw], # Should pass this in
            #     use_vision=not experiment.mock
            # )
            
            # Update current location
            # component.current_location = new_target
            # print(f"New target: {new_target}")

class RotationalScanStrategy(OptimizationStrategy):
    """
    [Placement/Rotation Strategy]
    Optimizes component rotation (Yaw/Roll) by scanning angles.
    
    Mechanism:
    - Robot rotates the end-effector (or re-places at different angles).
    - Camera measures beam quality (M2 or Width/Intensity).
    - Best angle is selected.
    
    Used for Crystal optimization.
    Ref: robotic_arm_angle_scan in analyzer_laser_align.py
    """
    def __init__(self, camera_port: int, start_angle: float = 0.0, end_angle: float = 5.0, steps: int = 25):
        self.camera_port = camera_port
        self.start_angle = start_angle
        self.end_angle = end_angle
        self.steps = steps

    def execute(self, experiment, component: OpticalComponent):
        print(f"--- [Strategy] Starting Rotational Scan for {component.id} ---")
        camera = experiment.get_camera(self.camera_port)
        camera.set_video_exposure(100)
        
        best_score = float('inf')
        best_angle = 0.0
        
        # 1. Scan Loop
        for i in range(self.steps):
            angle_deg = self.start_angle + (self.end_angle - self.start_angle) * (i / self.steps)
            
            # The legacy code (robotic_arm_angle_scan) calculates a new spatial angle for the robot
            # and moves to it. This implies the component is held or the robot moves around it.
            # Assuming 'place_spatial_angle' in pick_and_place controls this.
            # We need to re-place the component with the new angle.
            
            print(f"Testing Angle: {angle_deg:.2f}")
            
            # Update component angle
            # Legacy: target_angle = np.array([-inverted_place[0], -inverted_place[1], 0.0])
            # Simplified: We adjust the yaw/roll passed to pick_and_place
            # Assuming standard orientation + angle offset
            base_angle = [127.28, 127.28, 0.0]
            # Applying rotation to Z (Yaw) or X (Roll)? Legacy applies to X/Y based on calculation.
            # For modularity, let's rotate around Z (Yaw) which is typical for alignment.
            # Or if Crystal, maybe Roll.
            new_angle = [base_angle[0], base_angle[1], base_angle[2] + angle_deg]
            
            # Re-Place with new angle
            # Update inventory to current location to pick it up
            component.inventory_location = component.current_location
            
            # We must re-pick and place to rotate (or just rotate if held, but architecture assumes pick-place)
            # This might be slow but matches the "safe" iterative logic.
            experiment.assembly_manager.pick_and_place(
                component=component,
                pipeline=experiment.pipeline,
                x_adjust=experiment.calibration_manager.calculate_x_adjust(component.current_location.x, component.current_location.y),
                place_spatial_position=[component.current_location.x, component.current_location.y],
                place_spatial_angle=new_angle,
                use_vision=not experiment.mock
            )
            
            # Capture
            img = camera.capture()
            if img is None: continue
            
            # Metric: Minimize Width/Intensity (Proxy for M2/sqrt(Area))
            profile = experiment.vision_manager.analyze_beam_projection(img)
            width = profile['sigma_x'] + profile['sigma_y']
            intensity = profile['amplitude']
            
            score = width / (intensity + 1e-6)
            
            print(f"Angle {angle_deg:.2f}: Score={score:.4f} (W={width:.1f}, I={intensity:.1f})")
            
            if score < best_score:
                best_score = score
                best_angle = angle_deg
                
        print(f"Best Angle Found: {best_angle:.2f} (Score={best_score:.4f})")
        
        # Move to Best Angle
        component.inventory_location = component.current_location
        best_spatial_angle = [127.28, 127.28, best_angle]
        experiment.assembly_manager.pick_and_place(
            component=component,
            pipeline=experiment.pipeline,
            x_adjust=0,
            place_spatial_position=[component.current_location.x, component.current_location.y],
            place_spatial_angle=best_spatial_angle,
            use_vision=not experiment.mock
        )

class CobylaAlignmentStrategy(OptimizationStrategy):
    """
    [Alignment Strategy]
    Optimizes motor knobs (Actuators) using COBYLA algorithm.
    
    Mechanism:
    - Uses Motor Controllers (not Robot Arm).
    - Turns knobs to minimize distance between current beam and reference beam.
    - Used for IC, OC, and Cavity alignment.
    
    Ref: obj_function_oc/ic in analyzer_laser_align.py
    Objective: Minimize distance between Main Beam (Reference) and Secondary Beam (Current).
    """
    def __init__(self, camera_number: int, motor_ids: List[int], max_iter: int = 20, reference_image: np.ndarray = None, reference_key: str = None, video_exposure: float = 0.05, capture_exposure: float = 0.05, file_name: str = "test.png", objective_threshold: Optional[float] = None):
        self.camera_number = camera_number
        self.motor_ids = motor_ids # e.g. [1, 3] for Controller 1
        self.max_iter = max_iter
        self.reference_image = reference_image # "One Beam" image
        self.reference_key = reference_key # Key to look up in experiment memory
        self.video_exposure = video_exposure
        self.capture_exposure = capture_exposure
        self.file_name = file_name
        self.objective_threshold = objective_threshold  # Stop early if distance < threshold
        
        # Internal state to track absolute position (Legacy: oc_knob_positions)
        self.current_positions = np.zeros(len(motor_ids))

    def execute(self, experiment, component: OpticalComponent):
        print(f"--- [Strategy] Starting COBYLA Alignment for {component.id} ---")

        CAM_PORT = CAM1_PORT if self.camera_number == 1 else CAM2_PORT
        img = activate_cam_and_capture(
                cam_id=self.camera_number,
                video_exposure=self.video_exposure,
                capture_exposure=self.capture_exposure,
                filename=self.file_name,
                settle_s=0.5,
        )
        if img is None:
            print("Error: Capture failed.")
   
        # Resolve Reference Image
        ref_img = self.reference_image
        if ref_img is None and self.reference_key:
            ref_img = experiment.memory.get(self.reference_key)
            
        if ref_img is None:
            print("Warning: No reference image provided for subtraction. Capturing current as reference (might be wrong).")
            ref_img = request_capture_safe(CAM_PORT, self.capture_exposure, self.file_name)
            if ref_img is None:
                print("Error: cam capture failed.")

        # Track last objective value for callback
        last_objective_value = [None]  # Use list to allow modification in nested function
        
        # Custom exception for early stopping
        class ThresholdReached(Exception):
            """Raised when objective value falls below threshold."""
            pass
        
        def objective(x):
            # x contains absolute target positions for the knobs relative to start (0,0)
            
            # 1. Move Motors
            for i, motor_id in enumerate(self.motor_ids):
                target_pos = x[i]
                current_pos = self.current_positions[i]
                relative_move = target_pos - current_pos
                
                # Legacy "Backlash/Wakeup" move: move(-sign), then move(relative)
                # We will replicate this behavior exactly as requested
                # Assuming experiment.wifi_stepper1 controls these motors
                # We need to know WHICH controller. Assuming Controller 1 for OC/BS, Controller 2 for IC.
                # Ideally pass controller instance. For now, use experiment.wifi_stepper1 as default.
                
                controller = experiment.wifi_stepper1
                if component.id == "IC": controller = experiment.wifi_stepper2
                
                if abs(relative_move) > 1e-5:
                    # Legacy: controller.move_motor(id, -np.sign(rel))
                    # This moves 1 step in opposite direction?
                    # Note: move_motor(id, val) usually takes steps or angle.
                    # If x is float, it's likely angle/microns.
                    
                    controller.move_motor(motor_id, -np.sign(relative_move), wait_completion=False)
                    controller.move_motor(motor_id, relative_move, wait_completion=False)
                    
                # Update state
                self.current_positions[i] = target_pos
            
            time.sleep(2) # Settle time
            
            # 2. Measure
            img_two_beams = request_capture_safe(CAM_PORT, self.capture_exposure, self.file_name)
            if img_two_beams is None:
                last_objective_value[0] = 1000.0
                return 1000.0
            
            # 3. Calculate Metric: Distance between beams
            # Using VisionManager's detection
            main_center, secondary_center, _ = experiment.vision_manager.detect_two_beams(
                ref_img, img_two_beams
            )
            print(main_center, secondary_center)
            if main_center is None or secondary_center is None:
                last_objective_value[0] = 1000.0
                return 1000.0 # High penalty
                
            dist = np.sqrt((main_center[0] - secondary_center[0])**2 + (main_center[1] - secondary_center[1])**2)
            last_objective_value[0] = dist  # Store for callback
            print(f"COBYLA Step: Pos={x}, Dist={dist:.2f}")
            return dist

        # Callback for early stopping - raises exception to actually stop COBYLA
        def callback(xk):
            """Stop optimization if objective value is below threshold."""
            if self.objective_threshold is not None and last_objective_value[0] is not None:
                if last_objective_value[0] < self.objective_threshold:
                    print(f"\n[GOAL REACHED] Distance ({last_objective_value[0]:.2f}) is below threshold ({self.objective_threshold:.2f}). Stopping.")
                    raise ThresholdReached(f"Objective value {last_objective_value[0]:.2f} below threshold {self.objective_threshold:.2f}")

        # Initial Guess (0,0)
        x0 = np.zeros(len(self.motor_ids))
        
        # Constraints: |x| <= 5000
        # minimize with COBYLA expects constraints as dicts with 'type': 'ineq' and 'fun': callable
        # Each constraint function should return >= 0 for feasibility
        constraints = []
        for i in range(len(self.motor_ids)):
            constraints.append({'type': 'ineq', 'fun': lambda x, i=i: 5000 - x[i]})  # Upper bound: 5000 - x[i] >= 0
            constraints.append({'type': 'ineq', 'fun': lambda x, i=i: x[i] + 5000})  # Lower bound: x[i] + 5000 >= 0
        
        # Run Optimization using minimize with COBYLA method
        if self.objective_threshold is not None:
            print(f"Starting COBYLA optimization (max_iter={self.max_iter}, threshold={self.objective_threshold:.2f})...")
        else:
            print(f"Starting COBYLA optimization (max_iter={self.max_iter})...")
        
        try:
            res = minimize(
                objective, 
                x0, 
                method='COBYLA',
                constraints=constraints,
                options={'rhobeg': 700, 'rhoend': 20, 'maxiter': self.max_iter},
                callback=callback if self.objective_threshold is not None else None
            )
            print(f"Optimization Result: x={res.x}, fun={res.fun:.2f}, success={res.success}")
        except ThresholdReached as e:
            print(f"Optimization stopped early: {e}")
            # Create a result-like object with the current state
            from scipy.optimize import OptimizeResult
            res = OptimizeResult()
            res.x = self.current_positions.copy()
            res.fun = last_objective_value[0] if last_objective_value[0] is not None else 0.0
            res.success = True
            res.message = f"Stopped early: {e}"
            print(f"Final position: {res.x}, Final distance: {res.fun:.2f}")


class StopAtObjectiveThreshold(EarlyStopper):
    """
    Early stopper for Bayesian optimization: stops when objective value (M^2/useful_mean) falls below threshold.
    """
    def __init__(self, threshold, objective_history_ref):
        if not SKOPT_AVAILABLE:
            raise ImportError("skopt is required for StopAtObjectiveThreshold")
        super(StopAtObjectiveThreshold, self).__init__()
        self.threshold = threshold
        self.objective_history_ref = objective_history_ref  # Reference to strategy's objective_history list

    def _criterion(self, result):
        if not self.objective_history_ref:
            return False
        current_objective = self.objective_history_ref[-1]
        if current_objective < self.threshold:
            print(f"\n[GOAL REACHED] Objective value (M^2/useful_mean) is {current_objective:.4f} (< {self.threshold}). Stopping.")
            return True
        return False


class BayesianCavityAlignmentStrategy(OptimizationStrategy):
    """
    [Alignment Strategy]
    Optimizes IC (Input Coupler) motor knobs using Bayesian Optimization (GP).
    OC motors are not moved.
    
    Mechanism:
    - Uses Motor Controllers (not Robot Arm).
    - Optimizes 2 motors: IC_x, IC_y (Controller 2).
    - Uses M^2/useful_mean as objective (minimize).
    - Uses skopt's gp_minimize with Gaussian Process.
    
    Ref: obj_function_cavity_bas_intensity in analyzer_laser_align.py
    Objective: Minimize M^2/useful_mean (beam quality metric).
    """
    def __init__(
        self,
        camera_number: int = 1,
        max_iter: int = 50,
        objective_threshold: Optional[float] = None,
        video_exposure: float = 100.0,
        capture_exposure: float = 100.0,
        file_name: str = "cam1_current.png",
        ic_x_range: Tuple[float, float] = (-200.0, 200.0),
        ic_y_range: Tuple[float, float] = (-200.0, 200.0),
    ):
        """
        Args:
            objective_threshold: If provided, stops optimization early when objective value (M^2/useful_mean) < threshold.
        """
        if not SKOPT_AVAILABLE:
            raise ImportError("skopt is required for BayesianCavityAlignmentStrategy. Install: pip install scikit-optimize")
        
        self.camera_number = camera_number
        self.max_iter = max_iter
        self.objective_threshold = objective_threshold  # Threshold for objective value (M^2/useful_mean)
        self.video_exposure = video_exposure
        self.capture_exposure = capture_exposure
        self.file_name = file_name
        
        # Define search space (IC only)
        self.space = [
            Real(ic_x_range[0], ic_x_range[1], name='ic_x'),
            Real(ic_y_range[0], ic_y_range[1], name='ic_y'),
        ]
        
        # Internal state: track absolute positions of IC motors [ic_x, ic_y]
        self.ic_knob_positions = np.zeros(2)
        
        # History tracking
        self.m2_history = []
        self.useful_history = []
        self.objective_history = []  # Track objective values (M^2/useful_mean)
        self.current_time_history = []

    def execute(self, experiment, component: OpticalComponent):
        print(f"--- [Strategy] Starting Bayesian Cavity Alignment for {component.id} ---")
        
        CAM_PORT = CAM1_PORT if self.camera_number == 1 else CAM2_PORT
        controller2 = experiment.wifi_stepper2  # IC motors only
        
        # Constant parameters for decomposition
        L, R, wavelength, d = 14*1e-2, 15*1e-2, 1064*1e-9, 40*1e-2
        
        # --- Initial Check: Measure objective value BEFORE moving motors ---
        if self.objective_threshold is not None:
            print(f"Checking initial objective value (threshold: {self.objective_threshold:.4f})...")
            initial_img = request_capture_safe(CAM_PORT, self.capture_exposure, self.file_name)
            if initial_img is not None:
                M2_init, otsu_init, useful_init = experiment.assembly_manager.laser_beam_decomposition_high_exp(
                    self.file_name, "beam_final_square.png", L, R, wavelength, d
                )
                if M2_init is not None and useful_init > 0:
                    initial_objective = M2_init / useful_init
                    print(f"Initial objective value (M^2/useful_mean): {initial_objective:.4f}")
                    if initial_objective < self.objective_threshold:
                        print(f"\n[ALREADY OPTIMAL] Initial objective ({initial_objective:.4f}) is below threshold ({self.objective_threshold:.4f}).")
                        print("Skipping optimization - no motor movements needed.")
                        return  # Stop early, don't run optimization
                else:
                    print("Warning: Could not calculate initial M^2. Proceeding with optimization.")
            else:
                print("Warning: Initial capture failed. Proceeding with optimization.")
        
        @use_named_args(self.space)
        def obj_function_cavity_bas_intensity(ic_x, ic_y):
            """Objective function: minimize M^2/useful_mean (IC motors only)"""
            # Optimizer suggests absolute positions; compute relative move from current
            relatives = [
                ic_x - self.ic_knob_positions[0],
                ic_y - self.ic_knob_positions[1],
            ]

            # --- Hardware Control Sequence: Input Coupler only (Controller 2) ---
            controller2.move_motor(3, -np.sign(relatives[0]), wait_completion=False)
            controller2.move_motor(3, relatives[0], wait_completion=False)
            controller2.move_motor(1, -np.sign(relatives[1]), wait_completion=False)
            controller2.move_motor(1, relatives[1], wait_completion=False)

            time.sleep(5)  # Settle time for mechanical vibrations

            # --- Data Acquisition ---
            img_a = request_capture_safe(CAM_PORT, self.capture_exposure, self.file_name)
            
            if img_a is None:
                print("Error: Camera capture failed. Returning penalty.")
                return 10.0  # High penalty for lost beam

            # --- M2 Calculation ---
            M2, otsu_value, useful_mean = experiment.assembly_manager.laser_beam_decomposition_high_exp(
                self.file_name, "beam_final_square.png", L, R, wavelength, d
            )
            
            if M2 is None:
                print("Error: M^2 calculation failed. Returning penalty.")
                return 10.0
            
            self.m2_history.append(M2)
            self.useful_history.append(useful_mean)
            objective_value = M2 / useful_mean
            self.objective_history.append(objective_value)  # Track objective for early stopping
            current_time = datetime.datetime.now().strftime('%H:%M:%S.%f')
            self.current_time_history.append(current_time)

            np.save("m2_iteration_history.npy", np.array(self.m2_history))
            np.save("useful_iteration_history.npy", np.array(self.useful_history))
            np.save("objective_iteration_history.npy", np.array(self.objective_history))
            np.save("current_time_history.npy", np.array(self.current_time_history))

            print(f"Resulting M^2/useful_mean: {objective_value:.4f}")

            # --- UPDATE INTERNAL STATE ---
            self.ic_knob_positions = np.array([ic_x, ic_y])

            return objective_value

        # Setup early stopper if threshold provided (checks objective value, not M^2)
        callbacks = []
        if self.objective_threshold is not None:
            callbacks.append(StopAtObjectiveThreshold(self.objective_threshold, self.objective_history))

        # Run Bayesian Optimization
        print(f"Starting Bayesian Optimization (max_iter={self.max_iter})...")
        result = gp_minimize(
            func=obj_function_cavity_bas_intensity,
            dimensions=self.space,
            n_calls=self.max_iter,
            callback=callbacks if callbacks else None,
            random_state=42,
            acq_func='EI',  # Expected Improvement
        )
        
        print(f"\nOptimization Complete!")
        print(f"Best parameters: IC_x={result.x[0]:.2f}, IC_y={result.x[1]:.2f}")
        print(f"Best objective value: {result.fun:.4f}")
        
        # Move to best position (already done in last iteration, but ensure we're there)
        best_targets = result.x
        relatives = [best_targets[i] - self.ic_knob_positions[i] for i in range(2)]
        if any(abs(r) > 0.1 for r in relatives):
            print("Moving to final best position...")
            controller2.move_motor(3, -np.sign(relatives[0]), wait_completion=False)
            controller2.move_motor(3, relatives[0], wait_completion=False)
            controller2.move_motor(1, -np.sign(relatives[1]), wait_completion=False)
            controller2.move_motor(1, relatives[1], wait_completion=False)
            time.sleep(5)