# Rotation Mismatch Analysis (Revised)

## New Understanding
The user has clarified that the backend logic has changed. We are no longer using the `find_angle` method to calculate `target_angle` vector manually.

Instead, in `RealLabCommunicator.move_component`, the code calls:

```python
self.experiment.place_component_wo_home_specific_xy_cloudlab(
    component=comp,
    target_x=tx,
    target_y=ty,
    angle=[-180, 0, rot]
)
```

The `angle` parameter is passed as `[-180, 0, rot]`.

### The Observation
- **UI Intent (rot)**: 85°
- **Physical Reality**: 95°

### Analysis of `[-180, 0, rot]`
- The robot is receiving an angle vector where the Z-component is `rot`.
- The X-component is `-180`, which implies the gripper is pointing down (standard 6DOF convention often uses Rx=180 or -180).
- If the robot's coordinate system defines positive rotation as Counter-Clockwise (CCW) around the Z-axis *of the base frame*, but the gripper is flipped 180° around X, the Z-axis of the *tool frame* points downwards.
- A positive rotation about the *Tool Z* (which points down) would look Clockwise (CW) when viewed from above (the base frame perspective).

If the UI assumes CCW is positive (standard math), and the robot interprets `rot` as a rotation around the flipped Z-axis:
- UI sends +85 (CCW from East).
- Robot receives +85.
- Because of the flip, +85 around Tool Z is -85 (CW) around World Z.
- 85° CW from East (0) is -85°, or 275°.
- Wait, the observation is 95°.

Let's assume the reference zero is different.
If 90° (North) is the reference:
- UI 85° is 5° CW from North.
- Robot 95° is 5° CCW from North.
This is a sign flip relative to the Y-axis.

If the robot's `place_component...` function takes `angle` as `[Rx, Ry, Rz]`:
- We are passing `rot` directly into `Rz`.
- If `Rz` behaves oppositely to the UI's rotation due to the gripper flip, we need to negate it.

### Conclusion
The mirroring (85 vs 95) around 90 suggests that `rot` is indeed being interpreted with the opposite sign (or chirality) than expected.

To fix this, we should negate the rotation value passed to the robot command.

**Proposed Change in `backend/lab_communicator/real.py`**:

Inside `move_component`:
```python
self.experiment.place_component_wo_home_specific_xy_cloudlab(
    component=comp,
    target_x=tx,
    target_y=ty,
    angle=[-180, 0, -rot]  # <--- Negate rotation here
)
```

This will invert the direction of rotation sent to the robot, aligning the UI's CCW expectation with the physical result.
