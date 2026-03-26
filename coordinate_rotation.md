# Lab vs robot table coordinates

The optical table **as seen in the UI and on the overhead / side cameras** uses **lab frame** axes: horizontal and vertical on screen match the real bench and the laser line fit (`laser_line_fit.npy`). The **robot controller** uses a **robot frame** whose X and Y are rotated by a small angle relative to that view.

**Primary calibration (sign-sensitive):** If you move in a **straight line in the lab** (e.g. along the vertical you see on camera), a step that corresponds to about **Δy_robot = +100 mm** in the robot frame also has **Δx_robot = +2.5 mm** in the robot frame (your measured ratio and sign). In the code, a pure **+100 mm displacement along lab Y** maps to robot via a 2D rotation θ with **sin θ ≈ −2.5/100**, i.e. **`LAB_ROBOT_TABLE_ROTATION_RAD = atan2(-2.5, 100)`**.

*Equivalent picture:* a move with **only** Δy_robot (no Δx) does **not** follow a lab-straight line; to stay on that line you need the **(2.5, 100)** robot coupling above.

## Definitions

| Frame | Role |
|--------|------|
| **Lab** | What the operator sees: UI canvas, saved state JSON, vision-derived poses from top cameras, `laser_line_fit.npy`, grid overlay. Axes are **not** rotated in the UI. |
| **Robot** | Arguments and feedback from the motion stack (`place_component_wo_home_specific_xy_cloudlab`, Newton placement ticks, etc.) in the controller’s native table XY. |

## Transform (2D, about table Z)

Let θ be `LAB_ROBOT_TABLE_ROTATION_RAD` in `backend/lab_communicator/real.py` (negative θ so that a +Δy_lab step yields **+**Δx_robot and **+**Δy_robot as in your measurement).

**Lab → robot** (before sending a move to the robot):

\[
\begin{bmatrix} x_R \\ y_R \end{bmatrix}
=
\begin{bmatrix} \cos\theta & -\sin\theta \\ \sin\theta & \cos\theta \end{bmatrix}
\begin{bmatrix} x_L \\ y_L \end{bmatrix}
\]

**Robot → lab** (after reading a robot-reported XY into UI state):

\[
\begin{bmatrix} x_L \\ y_L \end{bmatrix}
=
\begin{bmatrix} \cos\theta & \sin\theta \\ -\sin\theta & \cos\theta \end{bmatrix}
\begin{bmatrix} x_R \\ y_R \end{bmatrix}
\]

Implementation: `lab_table_xy_to_robot_xy` and `robot_table_xy_to_lab_xy` in `real.py`.

## Where we apply / skip the transform

| Data path | Frame | Action |
|-----------|--------|--------|
| UI drag / API move with `target_x`, `target_y` | Lab | **Lab → robot** inside `move_component` before `place_component_wo_home_specific_xy_cloudlab`. |
| `current_state["components"][*]["pose"]` x, y exposed to UI | Lab | Store **lab** x, y after moves and in saved state. |
| Loaded snapshot `set_lab_state` → in-memory UI state | Lab | Keep JSON as lab; no change. |
| `set_lab_state` → `component_map` / `Pose` for the automation library | Robot | **Lab → robot** when writing `inventory_location` / `current_location`. |
| `_initialize_state` / rescan from **top cameras** (vision) | Lab | **No** rotation (already lab). |
| Newton / cloudlab placement callback `target_x`, `target_y` | Robot | **Robot → lab** in `_ui_pose_for_placement_tick` before updating ghost / solid pose. |
| Table camera images, laser overlay, grid | Lab | **No** rotation in software (already lab-aligned). |
| Component **rotation** (`rotation` / yaw / angle) | — | Unchanged by this note; only table **X/Y** are rotated here. |

## Tuning θ

Adjust **`LAB_ROBOT_TABLE_ROTATION_RAD`** at the top of `backend/lab_communicator/real.py`. Default is **`atan2(-2.5, 100)`** so lab-straight motion matches **(+2.5 mm, +100 mm)** in robot X/Y for that step. If your measured Δx is the opposite sign, flip the sign of the first argument (e.g. `atan2(2.5, 100)`).

## Mock mode

`MockLabCommunicator` does not apply this transform (lab and robot are the same in simulation).
