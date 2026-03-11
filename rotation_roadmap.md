# Rotation Implementation Roadmap

## 1. Frontend Interaction (UI)
**Goal**: Allow users to intuitively rotate components using mouse controls and manual input, ensuring orientation persists across all movement types.

- [x] **State Persistence**
    - [x] **Clarification**: Rotation is a persistent property of the `ghostState`. Whether moving via drag, manual coordinates, or scroll, the *current* `ghostState.rotation` must always be sent.
    - [x] **Initialization**: Ensure `ghostState` initializes rotation from `labState` (physical `yaw`) when a component is first interacted with, preventing jumps to 0 degrees.

- [x] **Mouse Scroll to Rotate**
    - [x] **Implementation**: Added `wheel` event listener to the canvas.
    - [x] **Logic**: When `isDragging` is true:
        - Detect scroll direction (`deltaY`).
        - Increment/Decrement `ghostState[draggingComponent].rotation` (±5 degrees).
        - Call `render()` and `updateContextPanel()` to update the visual ghost immediately.
    - [x] **Note**: Scrolling only updates the ghost state locally. The actual command is sent on `mouseup` (drop), which includes the final (X, Y, Rotation).

- [x] **Manual Input Validation**
    - [x] **Implementation**: Verified `ctxMoveBtn` handler correctly sends the updated `ctxRot.value`.
    - [x] **Payload**: Ensured the "Move" command payload includes the new rotation.

## 2. Backend Logic (Real Lab Communicator)
**Goal**: Translate the simple Z-rotation angle (0-360) into the robot's specific 3D orientation format `(rx, ry, rz)`.

- [x] **Implement `find_angle(rot)` Helper**
    - [x] **Implementation**: Created `find_angle(self, rotation_degrees: float) -> List[float]` in `RealLabCommunicator`.
    - [x] **Logic**: Currently returns `[127.28, 127.28, rot]` as a placeholder.
    - [x] **To-Do**: Internal math marked as TODO for future kinematic updates.
- [x] **Update `move_component`**
    - [x] **Implementation**: Replaced hardcoded list with `self.find_angle(rot)`.

## 3. Communication & Verification
**Goal**: Ensure the rotation data flows correctly from UI to Robot.

- [ ] **Verify Command Payload**
    - [ ] Check `POST /api/command` logs to confirm `rotation` is being sent correctly.
- [ ] **Safe Testing (Mock Mode)**
    - [ ] Test the UI scroll feature in Mock mode first to ensure the ghost state updates smoothly.
- [ ] **Physical Testing (Real Mode)**
    - [ ] Once `find_angle` is defined, test with the robot to ensure it rotates around the correct axis without tilting.

## 4. Future Work (Kinematics)
- [ ] Determine the exact relationship between the component's Z-rotation and the robot's end-effector `(rx, ry, rz)`.
- [ ] Update `find_angle` with the correct mathematical formula.
