# Clip-on Motor Control & COBYLA Roadmap

## 1. Backend Implementation

### 1.1. Data Model Update
- [x] **Component Catalog**: Update `schemas/component_catalog.json` to include `motor_ids` for relevant components.
    - [x] Add `"motor_ids": [1, 3]` to `tag_11` (Main Lens) for debugging/testing.

### 1.2. Lab Communicator API
- [x] **Base Class**: Add `move_motor(self, target_id: str, motor_id: int, distance: float)` to `LabCommunicator`.
- [x] **Mock Implementation**: 
    - [x] Implement `move_motor` to simulate delay and log the action.
    - [x] Implement `optimize_component` for COBYLA (mock simulation).
- [x] **Real Implementation**:
    - [x] Implement `move_motor` in `RealLabCommunicator`.
        - [x] Logic to select correct controller (Stepper1 vs Stepper2) based on component ID (e.g., "IC" uses Stepper2, others Stepper1).
        - [x] Call `experiment.wifi_stepperX.move_motor`.
    - [x] Implement `CobylaAlignmentStrategy` in `optimize_component`.
        - [x] Instantiate `CobylaAlignmentStrategy` with `motor_ids` from catalog/params.
        - [x] Execute strategy.

### 1.3. Server API
- [x] **Command Endpoint**: Update `POST /api/command` in `backend/main.py` to handle a new action `MOVE_MOTOR`.
    - [x] Payload: `{ "action": "MOVE_MOTOR", "target_id": "...", "parameters": { "motor_id": 1, "distance": 10.0 } }`

## 2. Frontend Implementation

### 2.1. Visual Indicators
- [x] **Sidebar**: Add an icon (e.g., `settings_input_component`) to components in the list that have `motor_ids`.
- [x] **Context Panel**: When a component with `motor_ids` is selected, show a "Motor Control" section.

### 2.2. Motor Control UI
- [x] **Controls**: Add UI elements in the Context Panel:
    - [x] Dropdown/Buttons for Motor ID selection (e.g., Motor 1, Motor 3).
    - [x] Input for Step Size / Distance.
    - [x] Forward/Backward buttons.
- [x] **Logic**: Connect buttons to `sendCommand({ action: "MOVE_MOTOR", ... })`.

### 2.3. COBYLA Strategy
- [x] **Strategy Parameter Modal**: Update the COBYLA strategy parameters to allow selecting/confirming motor IDs (or auto-filling from catalog).
- [x] **Execution**: Ensure "Optimize" button sends the correct COBYLA command.

## 3. Verification
- [ ] **Mock Test**: Verify UI buttons trigger logs in Mock Lab.
- [ ] **Real Test**: Verify "Move Motor" actually moves the physical motor.
- [ ] **Strategy Test**: Run COBYLA optimization and verify it tries to minimize beam distance.
