# Why Component Yaw Is Always 0 (Rotation Not Populated)

This document explains why the UI shows **rotation/angle 0** for all components even when they are physically rotated. No code changes are proposed here—only the cause.

---

## 1. Where the pose comes from

When the real lab starts, `RealLabCommunicator._initialize_state()` calls:

- `experiment.scan_components(components_to_scan, force_rescan=True)`

That eventually calls:

- **`lab_automation.managers.experiment_manager.OpticalExperiment.scan_components()`**  
  → for each component: **`self.assembly_manager.scan_inventory(tag_id, marker_length=...)`**
- **`lab_automation.managers.robot_manager.AssemblyManager.scan_inventory()`**  
  → uses ceiling camera(s) and returns a **`Pose(x, y, z, roll, pitch, yaw)`** that is stored in **`comp.inventory_location`**.

So the rotation you see in the UI comes **only** from whatever `scan_inventory()` returns. If that Pose always has `yaw=0` (and a fixed `roll=-180` in your setup), the UI will always show 0 for angle.

---

## 2. Two code paths in `scan_inventory`

`AssemblyManager.scan_inventory()` (in **`lab_automation/managers/robot_manager.py`**) can return a pose from:

1. **Stereo path** (if `ceiling_secondary` is available):  
   `pose_result = self.vision.estimate_stereo_pose(img1, img2, tag_id, marker_length, self.calibration)`  
   → return value is used as-is.

2. **Mono path** (fallback, or if no second camera):  
   `detection = self.vision.detect_aruco_tag(img1, tag_id)`  
   → only **`detection['center']`** (pixel `cx`, `cy`) is used.  
   → `cam_pose = Pose(x=cx, y=cy, z=0)` (no roll/pitch/yaw passed, so they default to **0**).  
   → `pose_result = self.calibration.map_ceiling_to_robot(cam_pose)`  
   → calibration just forwards **`roll, pitch, yaw`** from `camera_pose`, so they stay **0**.

So in both paths, **yaw (and effectively “angle” in the UI) is never set from the actual tag orientation**.

---

## 3. Stereo path: orientation is hardcoded, not from the tag

In **`lab_automation/managers/vision_manager.py`**, `estimate_stereo_pose()`:

- Uses ArUco to get **translation** (`tvec`) and **rotation** (`rvec`) for the tag in 3D.
- Averages **`avg_rvec`** from both cameras.
- Uses **only** the averaged translation to build the returned Pose:
  - `x`, `y`, `z` come from `avg_tvec` (converted to mm, etc.).
  - **Roll, pitch, yaw are not derived from `avg_rvec`.**  
    The code returns:  
    **`return Pose(x, y, z, -180, 0, 0)`**  
    with the comment: *"Rotation handling needed if critical"*.

So:

- **Roll = -180** is a **hardcoded** value (likely for a fixed gripper/camera convention), not the tag’s real orientation.
- **Pitch = 0** and **yaw = 0** are also **hardcoded**.
- The actual in-plane rotation of the tag (which would map to **yaw** for a top-down view) is **never computed or used**; `avg_rvec` is available but ignored for building the Pose.

That is why you always see **roll=-180, pitch=0, yaw=0** when the stereo path is used: the scan was only designed to provide **position**, not orientation.

---

## 4. Mono path: no 3D pose, so no orientation at all

In the mono fallback:

- **`detect_aruco_tag(img1, tag_id)`** is called **without** `camera_params` (no intrinsics).
- So the vision code only returns **`center`** and **`corners`**; it does **not** compute or return **`rvec`** / **`tvec`** (no 3D pose).
- The robot manager uses only **center**:  
  **`cam_pose = Pose(x=cx, y=cy, z=0)`**  
  so **roll, pitch, yaw** are all the `Pose` defaults: **0**.

So in the mono path, **no orientation is ever computed or passed**; only 2D pixel position is used, then mapped to robot x,y (and z set to 0). Yaw is always 0 because it is never set.

---

## 5. Summary: why yaw is always 0

| Path   | Position (x, y, z)        | Orientation (roll, pitch, yaw) |
|--------|---------------------------|---------------------------------|
| Stereo | From ArUco `tvec` (avg)  | **Hardcoded** `(-180, 0, 0)`; tag rotation (`rvec`) is **not** converted to roll/pitch/yaw. |
| Mono   | From ArUco center + calibration | **Never set**; `Pose(x, y, z)` uses default 0,0,0 for roll/pitch/yaw. |

So:

- **Yaw is always 0** because:
  1. **Stereo:** Orientation is hardcoded to `(-180, 0, 0)` and the tag’s rotation vector is not used.
  2. **Mono:** Only the tag center is used; no 3D pose and no orientation are computed or passed.

- **Roll appears as -180** when the stereo path is used, because that value is hardcoded in `estimate_stereo_pose()`, not read from the tag.

- Moving or rotating the component in the real world doesn’t change the reported angle because **the pipeline never updates orientation from the current ArUco pose**; it either keeps the hardcoded stereo values or leaves the mono defaults at zero.

---

## 6. Where in lab_automation this is decided

- **`lab_automation/managers/robot_manager.py`**  
  - `AssemblyManager.scan_inventory()`  
  - Chooses stereo vs mono and builds the Pose that becomes `inventory_location` (only position in mono; position + hardcoded orientation in stereo).

- **`lab_automation/managers/vision_manager.py`**  
  - `estimate_stereo_pose()`  
  - Returns `Pose(x, y, z, -180, 0, 0)` and does not use `avg_rvec` for roll/pitch/yaw.  
  - `detect_aruco_tag()`  
  - With `camera_params` can return `rvec`/`tvec`, but the mono path in `scan_inventory()` does not pass `camera_params`, so no 3D pose (and no orientation) is produced there.

- **`lab_automation/managers/calibration.py`**  
  - `map_ceiling_to_robot()`  
  - Forwards roll/pitch/yaw from the input Pose; it does not add or compute orientation.

So the reason the UI always shows **angle 0** is that **lab_automation’s scan path is built to fill only position (and a fixed roll in stereo); it never fills yaw (or pitch) from the actual tag orientation.** Fixing it would require changing lab_automation (and possibly cloud-labs) to compute and pass the tag’s in-plane rotation (e.g. from `rvec` or from 2D corners) and expose it as yaw in the Pose used for `inventory_location`.

---

## 7. Roadmap to fix this (plan only — no implementation yet)

Below is a concrete plan to populate **yaw** (and thus the UI "angle") from the real tag orientation. Implementation would happen only after you agree with this plan.

---

### 7.1 Goal

- **After the fix:** When a component is scanned, `inventory_location` (and thus the UI) should show the **actual in-plane rotation** of the ArUco tag (e.g. 0°, 45°, 90°) so that moving/rotating the component updates the displayed angle.
- **Convention:** Keep using **`Pose.yaw`** in degrees as the "rotation" shown in the UI (cloud-labs already maps `inv.yaw` → `pose.rotation`). So the fix is: **make lab_automation set `Pose.yaw` from the tag's orientation** in both stereo and mono paths.

---

### 7.2 Fix 1: Stereo path — use `avg_rvec` to set roll, pitch, yaw

**Where:** `lab_automation/managers/vision_manager.py` → `estimate_stereo_pose()`.

**Current:** Returns `Pose(x, y, z, -180, 0, 0)` and never uses `avg_rvec`.

**Plan:**

1. **Convert `avg_rvec` to Euler angles (roll, pitch, yaw) in degrees.**
   - Use `cv2.Rodrigues(avg_rvec)` to get a 3×3 rotation matrix `R`.
   - Convert `R` to a consistent Euler convention (e.g. XYZ or ZYX) using `scipy.spatial.transform.Rotation` (already used in lab_automation).
   - Extract roll, pitch, yaw in **degrees** to match `Pose` (which uses degrees for roll/pitch/yaw elsewhere).
2. **Return a Pose that uses these values.**  
   Replace  
   `return Pose(x, y, z, -180, 0, 0)`  
   with something like  
   `return Pose(x, y, z, roll, pitch, yaw)`  
   using the computed angles.
3. **Validate convention.**  
   For a ceiling-looking-down setup, the "angle" in the table plane is usually one of the three angles (often the third, yaw). If your robot/gripper convention uses a different angle for "rotation in the table plane", we can map that to `yaw` so the UI still shows it as "rotation". No change needed in cloud-labs if we always put the table-plane angle into `Pose.yaw`.

**Result:** Stereo scan will return real orientation; `inventory_location` will have non-zero yaw when the tag is rotated; the UI will show it.

---

### 7.3 Fix 2: Mono path — get orientation and pass it in the Pose

**Where:** `lab_automation/managers/robot_manager.py` → `scan_inventory()` (mono fallback), and optionally `vision_manager.py` → `detect_aruco_tag()`.

**Current:** `detect_aruco_tag(img1, tag_id)` is called **without** `camera_params`, so the result has only `center` and `corners`; no `rvec`/`tvec`. The code then builds `Pose(x=cx, y=cy, z=0)` with no orientation.

**Plan (choose one of two approaches):**

**Option A — Use ceiling camera intrinsics (recommended if available):**

1. In **`scan_inventory()`**, when calling the vision layer for the **ceiling** camera, pass the ceiling camera's intrinsics (e.g. from `self.calibration` or a dedicated ceiling calibration) into `detect_aruco_tag()` as `camera_params` (e.g. `mtx`, `dist`), plus `marker_length`.
2. Then `detect_aruco_tag()` will return **`rvec`** and **`tvec`** (it already does when `camera_params` is provided).
3. In **`scan_inventory()`** (or a small helper in vision_manager):
   - Convert the mono **`rvec`** to Euler angles (same method as in Fix 1).
   - Build **`cam_pose = Pose(x, y, z, roll, pitch, yaw)`** (e.g. after mapping 2D to robot x,y via calibration; z can stay 0 or use a default).
4. Call **`map_ceiling_to_robot(cam_pose)`**; calibration already forwards roll/pitch/yaw, so the returned Pose will now carry the computed yaw.

**Option B — No intrinsics: use 2D corner angle (fallback):**

1. In **`scan_inventory()`**, after `detection = self.vision.detect_aruco_tag(img1, tag_id)`:
   - Use **`detection['corners']`** (four corners of the tag in the image).
   - Compute the in-plane angle of the tag in the image (e.g. angle of the top edge relative to horizontal using `atan2`).
   - Treat this as **yaw** in the camera frame (valid if the ceiling camera looks roughly straight down; otherwise it's an approximation).
2. Build **`cam_pose = Pose(x=cx, y=cy, z=0, roll=0, pitch=0, yaw=computed_yaw_deg)`**.
3. Call **`map_ceiling_to_robot(cam_pose)`** so the returned Pose has the same yaw (calibration forwards it).

**Recommendation:** Prefer **Option A** if ceiling camera calibration/intrinsics exist; use **Option B** only as a fallback when intrinsics are not available.

**Result:** Mono scan will also set `inventory_location.yaw` from the tag, and the UI will show the correct angle after a scan.

---

### 7.4 Cloud-labs (real communicator)

**Where:** `cloud-labs/backend/lab_communicator/real.py` → `_initialize_state()`.

**Current:** Already uses **`inv.yaw`** as the fallback for `rotation` when there is no `angle` / `rx,ry,rz`. So once lab_automation fills `Pose.yaw`, the real communicator will automatically show it in the UI. **No code change required in cloud-labs** for the basic fix.

Optional later: if lab_automation ever uses a different convention (e.g. table-plane angle in `roll`), we could add a one-line mapping in real.py (e.g. `rotation = getattr(inv, 'yaw', None) or getattr(inv, 'roll', 0)`). For the roadmap we assume **yaw** remains the single source for UI rotation.

---

### 7.5 Order of implementation

1. **Fix 1 (stereo)** in `lab_automation/managers/vision_manager.py`: convert `avg_rvec` → roll, pitch, yaw and return them in `Pose`. Test with stereo ceiling setup; confirm UI shows changing angle when you rotate the component and rescan.
2. **Fix 2 (mono)** in `lab_automation/managers/robot_manager.py` (and optionally vision_manager): either pass ceiling intrinsics and use rvec→yaw (Option A), or compute 2D corner yaw (Option B), and pass yaw in the Pose. Test with mono ceiling; confirm UI updates.
3. **No change** in cloud-labs unless we later decide to support an alternate convention (e.g. roll as rotation).

---

### 7.6 Summary table (after fix)

| Path   | Position (x, y, z) | Orientation (roll, pitch, yaw) |
|--------|--------------------|---------------------------------|
| Stereo | From ArUco `tvec` (avg) | **From `avg_rvec`** → convert to Euler (roll, pitch, yaw) and return in `Pose`. |
| Mono   | From center + calibration | **Option A:** from `rvec` (with ceiling intrinsics); **Option B:** from 2D corner angle. Pass as `Pose.yaw`. |

Once you agree with this roadmap, we can implement it step by step (stereo first, then mono).
