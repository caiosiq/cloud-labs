# Backends hub snapshot — `mock.default`

Static dump of the live Wiki **Backends** hub for the teaching simulator **`mock.default`**: gallery card fields, overview, every catalog component, and kernels. No live server required to read this pack.

## Overview

- **Label:** Mock bench (default)
- **Availability:** `ready`
- **Description:** In-process teaching simulator — full Twin, scripts, and closed-loop without hardware. Soft session leases unless CLOUDLABS_STRICT_LEASE=1.
- **Communicator / mode:** `mock`
- **Component count:** 11
- **Control repos:** default, experiment, laser-cavity

```python
from cloudlabs import connect

with connect("mock.default") as lab:
    ...
```

## Components (index)

| Tag | Name | Type | On bench |
|-----|------|------|----------|
| `tag_9` | ND Filter | `OPTICAL_FILTER` | on bench |
| `tag_22` | Gripper Camera 1 | `OPTICAL_CAMERA` | on bench |
| `tag_21` | Gripper Camera 2 | `OPTICAL_CAMERA` | on bench |
| `tag_18` | Curved Mirror (OC) | `OPTICAL_MIRROR` | on bench |
| `tag_2` | Beam Block | `OPTICAL_BEAM_BLOCK` | on bench |
| `tag_10` | Beam Splitter (BS) | `OPTICAL_BEAMSPLITTER` | on bench |
| `tag_11` | Main Lens | `OPTICAL_LENS` | on bench |
| `tag_20` | Planar Mirror (IC) | `OPTICAL_MIRROR` | on bench |
| `tag_3` | Filter | `OPTICAL_FILTER` | library |
| `tag_19` | Crystal | `OPTICAL_CRYSTAL` | on bench |
| `tag_8` | Silver Mirror | `OPTICAL_MIRROR` | library |
| `tag_99` | Table-top camera | `OPTICAL_CAMERA` | on bench |
| `tag_50` | Main Laser | `LASER_SOURCE` | on bench |

## Component: ND Filter

- **Tag:** `tag_9`
- **Type:** `OPTICAL_FILTER`
- **Presence:** on bench

### Physical interpretation

ND Filter attenuates or spectrally selects light. Treat placement as part of the optical recipe, not only a mechanical pose.

### Tunables (1)

#### `tunables.nominal_pose`

- **Widget:** `TablePose` · **Unit:** —
  - `tunables.nominal_pose.x` (mm): `lab.move_component("tag_9", "tunables.nominal_pose.x", <value>)`
  - `tunables.nominal_pose.y` (mm): `lab.move_component("tag_9", "tunables.nominal_pose.y", <value>)`
  - `tunables.nominal_pose.rotation` (deg): `lab.move_component("tag_9", "tunables.nominal_pose.rotation", <value>)`

### Measurables (0)

_No measurables declared._

### Primitives (10)

- `MOVE_COMPONENT`
- `STORE_COMPONENT`
- `PLACE_FROM_STORAGE`
- `PICK_COMPONENT`
- `HOVER`
- `PLACE_FROM_HOVER`
- `RECORD_MEASURABLES`
- `START_TELEOP`
- `END_TELEOP`
- `TELEOP_GOTO`

## Component: Gripper Camera 1

- **Tag:** `tag_22`
- **Type:** `OPTICAL_CAMERA`
- **Presence:** on bench

### Physical interpretation

Gripper Camera 1 is an eye on the experiment - gripper or table camera. Images are the raw material for kernels (centroids, scores) and for human supervision.

### Tunables (2)

#### `tunables.nominal_pose`

- **Widget:** `TablePose` · **Unit:** —
  - `tunables.nominal_pose.x` (mm): `lab.move_component("tag_22", "tunables.nominal_pose.x", <value>)`
  - `tunables.nominal_pose.y` (mm): `lab.move_component("tag_22", "tunables.nominal_pose.y", <value>)`
  - `tunables.nominal_pose.rotation` (deg): `lab.move_component("tag_22", "tunables.nominal_pose.rotation", <value>)`

#### `tunables.exposure_time_ms`

- **Widget:** `FloatRange` · **Unit:** ms

### Measurables (1)

#### `measurables.camera_image`

- **What it measures:** Still frame from this camera. Kernels and lab.measurable(...).resolve() use BGR uint8 HxWx3; PNG is storage/wire only.
- **Tensor:**
```
uint8 · bgr_hwc_uint8 · (H, W, 3)
H: rows (y), pixels top→bottom
W: cols (x), pixels left→right
3: BGR channels (OpenCV / kernel layout)
wire: png
```
- **Script:** `lab.measurable("tag_22", "camera_image").resolve(record=True)`

### Primitives (13)

- `MOVE_COMPONENT`
- `SET_EXPOSURE`
- `STORE_COMPONENT`
- `PLACE_FROM_STORAGE`
- `PICK_COMPONENT`
- `HOVER`
- `PLACE_FROM_HOVER`
- `RECORD_MEASURABLES`
- `START_TELEOP`
- `END_TELEOP`
- `TELEOP_GOTO`
- `START_LIVE_FEED`
- `END_LIVE_FEED`

## Component: Gripper Camera 2

- **Tag:** `tag_21`
- **Type:** `OPTICAL_CAMERA`
- **Presence:** on bench

### Physical interpretation

Gripper Camera 2 is an eye on the experiment - gripper or table camera. Images are the raw material for kernels (centroids, scores) and for human supervision.

### Tunables (2)

#### `tunables.nominal_pose`

- **Widget:** `TablePose` · **Unit:** —
  - `tunables.nominal_pose.x` (mm): `lab.move_component("tag_21", "tunables.nominal_pose.x", <value>)`
  - `tunables.nominal_pose.y` (mm): `lab.move_component("tag_21", "tunables.nominal_pose.y", <value>)`
  - `tunables.nominal_pose.rotation` (deg): `lab.move_component("tag_21", "tunables.nominal_pose.rotation", <value>)`

#### `tunables.exposure_time_ms`

- **Widget:** `FloatRange` · **Unit:** ms

### Measurables (1)

#### `measurables.camera_image`

- **What it measures:** Still frame from this camera. Kernels and lab.measurable(...).resolve() use BGR uint8 HxWx3; PNG is storage/wire only.
- **Tensor:**
```
uint8 · bgr_hwc_uint8 · (H, W, 3)
H: rows (y), pixels top→bottom
W: cols (x), pixels left→right
3: BGR channels (OpenCV / kernel layout)
wire: png
```
- **Script:** `lab.measurable("tag_21", "camera_image").resolve(record=True)`

### Primitives (13)

- `MOVE_COMPONENT`
- `SET_EXPOSURE`
- `STORE_COMPONENT`
- `PLACE_FROM_STORAGE`
- `PICK_COMPONENT`
- `HOVER`
- `PLACE_FROM_HOVER`
- `RECORD_MEASURABLES`
- `START_TELEOP`
- `END_TELEOP`
- `TELEOP_GOTO`
- `START_LIVE_FEED`
- `END_LIVE_FEED`

## Component: Curved Mirror (OC)

- **Tag:** `tag_18`
- **Type:** `OPTICAL_MIRROR`
- **Presence:** on bench

### Physical interpretation

Curved Mirror (OC) (OPTICAL_MIRROR) participates in the optical layout. Tunables are DOFs you control via primitives; measurables are captured observations without a 1:1 tunable.

### Tunables (2)

#### `tunables.nominal_pose`

- **Widget:** `TablePose` · **Unit:** —
  - `tunables.nominal_pose.x` (mm): `lab.move_component("tag_18", "tunables.nominal_pose.x", <value>)`
  - `tunables.nominal_pose.y` (mm): `lab.move_component("tag_18", "tunables.nominal_pose.y", <value>)`
  - `tunables.nominal_pose.rotation` (deg): `lab.move_component("tag_18", "tunables.nominal_pose.rotation", <value>)`

#### `tunables.nominal_motor_positions`

- **Widget:** `JsonInspector` · **Unit:** deg
  - `tunables.nominal_motor_positions.1`: `lab.move_component("tag_18", "tunables.nominal_motor_positions.1", <value>)`
  - `tunables.nominal_motor_positions.3`: `lab.move_component("tag_18", "tunables.nominal_motor_positions.3", <value>)`

### Measurables (1)

#### `measurables.last_optimization_score`

- **What it measures:** Last closed-loop / optimize scalar for this part. Captured, not commanded.
- **Tensor:**
```
float64 · scalar · ()
no axes (0-D scalar)
```
- **Script:** `lab.measurable("tag_18", "last_optimization_score").resolve(record=True)`

### Primitives (16)

- `MOVE_COMPONENT`
- `SET_MOTOR_SETPOINT`
- `MOVE_MOTOR`
- `MOTOR_SEND_HOME`
- `MOTOR_SET_ZERO`
- `STORE_COMPONENT`
- `PLACE_FROM_STORAGE`
- `PICK_COMPONENT`
- `HOVER`
- `PLACE_FROM_HOVER`
- `RECORD_MEASURABLES`
- `OPTIMIZE`
- `SCAN_ROTATE_IN_PLACE`
- `START_TELEOP`
- `END_TELEOP`
- `TELEOP_GOTO`

## Component: Beam Block

- **Tag:** `tag_2`
- **Type:** `OPTICAL_BEAM_BLOCK`
- **Presence:** on bench

### Physical interpretation

Beam Block (OPTICAL_BEAM_BLOCK) participates in the optical layout. Tunables are DOFs you control via primitives; measurables are captured observations without a 1:1 tunable.

### Tunables (1)

#### `tunables.nominal_pose`

- **Widget:** `TablePose` · **Unit:** —
  - `tunables.nominal_pose.x` (mm): `lab.move_component("tag_2", "tunables.nominal_pose.x", <value>)`
  - `tunables.nominal_pose.y` (mm): `lab.move_component("tag_2", "tunables.nominal_pose.y", <value>)`
  - `tunables.nominal_pose.rotation` (deg): `lab.move_component("tag_2", "tunables.nominal_pose.rotation", <value>)`

### Measurables (0)

_No measurables declared._

### Primitives (10)

- `MOVE_COMPONENT`
- `STORE_COMPONENT`
- `PLACE_FROM_STORAGE`
- `PICK_COMPONENT`
- `HOVER`
- `PLACE_FROM_HOVER`
- `RECORD_MEASURABLES`
- `START_TELEOP`
- `END_TELEOP`
- `TELEOP_GOTO`

## Component: Beam Splitter (BS)

- **Tag:** `tag_10`
- **Type:** `OPTICAL_BEAMSPLITTER`
- **Presence:** on bench

### Physical interpretation

Beam Splitter (BS) (OPTICAL_BEAMSPLITTER) participates in the optical layout. Tunables are DOFs you control via primitives; measurables are captured observations without a 1:1 tunable.

### Tunables (1)

#### `tunables.nominal_pose`

- **Widget:** `TablePose` · **Unit:** —
  - `tunables.nominal_pose.x` (mm): `lab.move_component("tag_10", "tunables.nominal_pose.x", <value>)`
  - `tunables.nominal_pose.y` (mm): `lab.move_component("tag_10", "tunables.nominal_pose.y", <value>)`
  - `tunables.nominal_pose.rotation` (deg): `lab.move_component("tag_10", "tunables.nominal_pose.rotation", <value>)`

### Measurables (0)

_No measurables declared._

### Primitives (10)

- `MOVE_COMPONENT`
- `STORE_COMPONENT`
- `PLACE_FROM_STORAGE`
- `PICK_COMPONENT`
- `HOVER`
- `PLACE_FROM_HOVER`
- `RECORD_MEASURABLES`
- `START_TELEOP`
- `END_TELEOP`
- `TELEOP_GOTO`

## Component: Main Lens

- **Tag:** `tag_11`
- **Type:** `OPTICAL_LENS`
- **Presence:** on bench

### Physical interpretation

Main Lens (OPTICAL_LENS) participates in the optical layout. Tunables are DOFs you control via primitives; measurables are captured observations without a 1:1 tunable.

### Tunables (2)

#### `tunables.nominal_pose`

- **Widget:** `TablePose` · **Unit:** —
  - `tunables.nominal_pose.x` (mm): `lab.move_component("tag_11", "tunables.nominal_pose.x", <value>)`
  - `tunables.nominal_pose.y` (mm): `lab.move_component("tag_11", "tunables.nominal_pose.y", <value>)`
  - `tunables.nominal_pose.rotation` (deg): `lab.move_component("tag_11", "tunables.nominal_pose.rotation", <value>)`

#### `tunables.nominal_motor_positions`

- **Widget:** `JsonInspector` · **Unit:** deg
  - `tunables.nominal_motor_positions.1`: `lab.move_component("tag_11", "tunables.nominal_motor_positions.1", <value>)`
  - `tunables.nominal_motor_positions.3`: `lab.move_component("tag_11", "tunables.nominal_motor_positions.3", <value>)`

### Measurables (1)

#### `measurables.last_optimization_score`

- **What it measures:** Last closed-loop / optimize scalar for this part. Captured, not commanded.
- **Tensor:**
```
float64 · scalar · ()
no axes (0-D scalar)
```
- **Script:** `lab.measurable("tag_11", "last_optimization_score").resolve(record=True)`

### Primitives (16)

- `MOVE_COMPONENT`
- `SET_MOTOR_SETPOINT`
- `MOVE_MOTOR`
- `MOTOR_SEND_HOME`
- `MOTOR_SET_ZERO`
- `STORE_COMPONENT`
- `PLACE_FROM_STORAGE`
- `PICK_COMPONENT`
- `HOVER`
- `PLACE_FROM_HOVER`
- `RECORD_MEASURABLES`
- `OPTIMIZE`
- `SCAN_ROTATE_IN_PLACE`
- `START_TELEOP`
- `END_TELEOP`
- `TELEOP_GOTO`

## Component: Planar Mirror (IC)

- **Tag:** `tag_20`
- **Type:** `OPTICAL_MIRROR`
- **Presence:** on bench

### Physical interpretation

Planar Mirror (IC) (OPTICAL_MIRROR) participates in the optical layout. Tunables are DOFs you control via primitives; measurables are captured observations without a 1:1 tunable.

### Tunables (2)

#### `tunables.nominal_pose`

- **Widget:** `TablePose` · **Unit:** —
  - `tunables.nominal_pose.x` (mm): `lab.move_component("tag_20", "tunables.nominal_pose.x", <value>)`
  - `tunables.nominal_pose.y` (mm): `lab.move_component("tag_20", "tunables.nominal_pose.y", <value>)`
  - `tunables.nominal_pose.rotation` (deg): `lab.move_component("tag_20", "tunables.nominal_pose.rotation", <value>)`

#### `tunables.nominal_motor_positions`

- **Widget:** `JsonInspector` · **Unit:** deg
  - `tunables.nominal_motor_positions.1`: `lab.move_component("tag_20", "tunables.nominal_motor_positions.1", <value>)`
  - `tunables.nominal_motor_positions.3`: `lab.move_component("tag_20", "tunables.nominal_motor_positions.3", <value>)`

### Measurables (1)

#### `measurables.last_optimization_score`

- **What it measures:** Last closed-loop / optimize scalar for this part. Captured, not commanded.
- **Tensor:**
```
float64 · scalar · ()
no axes (0-D scalar)
```
- **Script:** `lab.measurable("tag_20", "last_optimization_score").resolve(record=True)`

### Primitives (16)

- `MOVE_COMPONENT`
- `SET_MOTOR_SETPOINT`
- `MOVE_MOTOR`
- `MOTOR_SEND_HOME`
- `MOTOR_SET_ZERO`
- `STORE_COMPONENT`
- `PLACE_FROM_STORAGE`
- `PICK_COMPONENT`
- `HOVER`
- `PLACE_FROM_HOVER`
- `RECORD_MEASURABLES`
- `OPTIMIZE`
- `SCAN_ROTATE_IN_PLACE`
- `START_TELEOP`
- `END_TELEOP`
- `TELEOP_GOTO`

## Component: Filter

- **Tag:** `tag_3`
- **Type:** `OPTICAL_FILTER`
- **Presence:** library only

### Physical interpretation

Filter attenuates or spectrally selects light. Treat placement as part of the optical recipe, not only a mechanical pose.

### Tunables (1)

#### `tunables.nominal_pose`

- **Widget:** `TablePose` · **Unit:** —
  - `tunables.nominal_pose.x` (mm): `lab.move_component("tag_3", "tunables.nominal_pose.x", <value>)`
  - `tunables.nominal_pose.y` (mm): `lab.move_component("tag_3", "tunables.nominal_pose.y", <value>)`
  - `tunables.nominal_pose.rotation` (deg): `lab.move_component("tag_3", "tunables.nominal_pose.rotation", <value>)`

### Measurables (0)

_No measurables declared._

### Primitives (10)

- `MOVE_COMPONENT`
- `STORE_COMPONENT`
- `PLACE_FROM_STORAGE`
- `PICK_COMPONENT`
- `HOVER`
- `PLACE_FROM_HOVER`
- `RECORD_MEASURABLES`
- `START_TELEOP`
- `END_TELEOP`
- `TELEOP_GOTO`

## Component: Crystal

- **Tag:** `tag_19`
- **Type:** `OPTICAL_CRYSTAL`
- **Presence:** on bench

### Physical interpretation

Crystal (OPTICAL_CRYSTAL) participates in the optical layout. Tunables are DOFs you control via primitives; measurables are captured observations without a 1:1 tunable.

### Tunables (1)

#### `tunables.nominal_pose`

- **Widget:** `TablePose` · **Unit:** —
  - `tunables.nominal_pose.x` (mm): `lab.move_component("tag_19", "tunables.nominal_pose.x", <value>)`
  - `tunables.nominal_pose.y` (mm): `lab.move_component("tag_19", "tunables.nominal_pose.y", <value>)`
  - `tunables.nominal_pose.rotation` (deg): `lab.move_component("tag_19", "tunables.nominal_pose.rotation", <value>)`

### Measurables (0)

_No measurables declared._

### Primitives (10)

- `MOVE_COMPONENT`
- `STORE_COMPONENT`
- `PLACE_FROM_STORAGE`
- `PICK_COMPONENT`
- `HOVER`
- `PLACE_FROM_HOVER`
- `RECORD_MEASURABLES`
- `START_TELEOP`
- `END_TELEOP`
- `TELEOP_GOTO`

## Component: Silver Mirror

- **Tag:** `tag_8`
- **Type:** `OPTICAL_MIRROR`
- **Presence:** library only

### Physical interpretation

Silver Mirror (OPTICAL_MIRROR) participates in the optical layout. Tunables are DOFs you control via primitives; measurables are captured observations without a 1:1 tunable.

### Tunables (1)

#### `tunables.nominal_pose`

- **Widget:** `TablePose` · **Unit:** —
  - `tunables.nominal_pose.x` (mm): `lab.move_component("tag_8", "tunables.nominal_pose.x", <value>)`
  - `tunables.nominal_pose.y` (mm): `lab.move_component("tag_8", "tunables.nominal_pose.y", <value>)`
  - `tunables.nominal_pose.rotation` (deg): `lab.move_component("tag_8", "tunables.nominal_pose.rotation", <value>)`

### Measurables (0)

_No measurables declared._

### Primitives (10)

- `MOVE_COMPONENT`
- `STORE_COMPONENT`
- `PLACE_FROM_STORAGE`
- `PICK_COMPONENT`
- `HOVER`
- `PLACE_FROM_HOVER`
- `RECORD_MEASURABLES`
- `START_TELEOP`
- `END_TELEOP`
- `TELEOP_GOTO`

## Component: Table-top camera

- **Tag:** `tag_99`
- **Type:** `OPTICAL_CAMERA`
- **Presence:** on bench

### Physical interpretation

Table-top camera is an eye on the experiment - gripper or table camera. Images are the raw material for kernels (centroids, scores) and for human supervision.

### Tunables (1)

#### `tunables.exposure_time_ms`

- **Widget:** `FloatRange` · **Unit:** ms

### Measurables (1)

#### `measurables.camera_image`

- **What it measures:** Still frame from this camera. Kernels and lab.measurable(...).resolve() use BGR uint8 HxWx3; PNG is storage/wire only.
- **Tensor:**
```
uint8 · bgr_hwc_uint8 · (H, W, 3)
H: rows (y), pixels top→bottom
W: cols (x), pixels left→right
3: BGR channels (OpenCV / kernel layout)
wire: png
```
- **Script:** `lab.measurable("tag_99", "camera_image").resolve(record=True)`

### Primitives (4)

- `SET_EXPOSURE`
- `RECORD_MEASURABLES`
- `START_LIVE_FEED`
- `END_LIVE_FEED`

## Component: Main Laser

- **Tag:** `tag_50`
- **Type:** `LASER_SOURCE`
- **Presence:** on bench

### Physical interpretation

Main Laser is a source. Output power and pointing couple into every downstream alignment loop.

### Tunables (1)

#### `tunables.output_power_mw`

- **Widget:** `FloatRange` · **Unit:** mW

### Measurables (1)

#### `measurables.output_power_readback_mw`

- **What it measures:** Measured optical output power (mW). A captured sensor summary — not a command.
- **Tensor:**
```
float64 · scalar · ()
unit: mW
no axes (0-D scalar)
```
- **Script:** `lab.measurable("tag_50", "output_power_readback_mw").resolve(record=True)`

### Primitives (2)

- `SET_LASER_OUTPUT`
- `RECORD_MEASURABLES`

## Kernels

### `builtin.gaussian_beam_fit`

- **Label:** Gaussian beam moments
- **Runtime:** `torchscript`
- **Description:** Moment-based Gaussian beam estimate on the full frame after a 10%-of-peak intensity mask: [amplitude, cx, cy, sigma_x, sigma_y]. Amplitude is peak normalized intensity; cx/cy are weighted first moments (px); sigma_* are sqrt of second central moments (px). Not a nonlinear least-squares fit — prefer for closed-loop defaults; author a session kernel for custom models.

### `builtin.roi_centroid`

- **Label:** ROI centroid (center half)
- **Runtime:** `torchscript`
- **Description:** Sub-pixel intensity-weighted centroid inside the center-half ROI (H/4..3H/4, W/4..3W/4), returned in full-frame pixel coordinates as features [cx, cy]. Threshold is 50% of the ROI peak. Pair with metric rms_distance and target_px for alignment. Arbitrary bbox parameters are not yet in the catalog schema — use a session kernel for custom ROIs.

### `demo.image_mean_score`

- **Label:** Demo image mean score
- **Runtime:** `torchscript`
- **Description:** Fixture scalar for CI/mock and Wiki demos. Converts the camera frame to float RGB in [0,1] (NCHW) and returns the mean intensity of all pixels. Bright images score near 1.0; dark images near 0.0. Not a physical beam metric — use curated feature kernels or a session kernel for centroid/radius work.

### `demo.peak_intensity`

- **Label:** Demo peak intensity
- **Runtime:** `torchscript`
- **Description:** Maximum channel intensity in the normalized RGB frame (max over C,H,W). Tracks the brightest pixel — a crude peak-power proxy for mock demos.

### `demo.roi_mean_score`

- **Label:** Demo center ROI mean
- **Runtime:** `torchscript`
- **Description:** Mean intensity over the center half of the frame (crop H/4..3H/4, W/4..3W/4) after RGB float normalization. Useful when you care about the middle of the camera FOV rather than the full sensor.

### `ensemble.eval.block_cobyla`

- **Label:** Ensemble block COBYLA
- **Runtime:** `builtin`
- **Description:** Default closed-loop optimizer kernel (session.py + communicator ensemble).

### `ensemble.eval.image_features`

- **Label:** Live image features → loss
- **Runtime:** `builtin`
- **Description:** Real closed-loop path: capture camera PNG → NumPy/OpenCV centroid & power → weighted_sum scalar loss → motor step. Default on real when no mock landscape.

### `ensemble.eval.mock_landscape`

- **Label:** Mock synthetic landscape
- **Runtime:** `builtin`
- **Description:** Mock-only coupled centroid + power landscape; writes measurables on each eval.

### `measurable.materialize.camera`

- **Label:** Camera measurable materialize
- **Runtime:** `builtin`
- **Description:** On each ensemble eval, persist camera_image PNG metadata into runtime measurables (MeasurableTensor-compatible) for UI/SDK.

### `objective.compile.weighted_sum`

- **Label:** Objective graph compiler
- **Runtime:** `builtin`
- **Description:** Phase E authoring → ObjectiveSpec + preflight validation.

### `stabilization.settle`

- **Label:** Settle / stabilization wait
- **Runtime:** `builtin`
- **Description:** Post-move settle before capture (honors solver.settle_ms on real).

