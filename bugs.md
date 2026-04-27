# Bugs and inconsistencies (real lab / lab_automation / cloud-labs)

This document captures issues found while tracing  
`RuntimeError: scan_rotate_placed_cloudlab: need is_placed and current_location (robot frame)`  
and the related “weird joint angles on first pick, better after pick & place” behavior.  
**No code changes yet** — analysis only.

---

## Executive summary: three concrete fixes

These are the issues worth fixing first; later sections add evidence and edge cases.

| # | Bug | Intent |
|---|-----|--------|
| **1** | **`is_placed` not set after table scan** | After `scan_components_cloudlab` finds a part on the table, set `is_placed = True` (or relax the placed scan-rotate guard) so **scan → scan/rotate before pick** matches cloud-labs + robot semantics. |
| **2** | **Hover / place-from-hover pass `+rot` where pick & place use `-rot`** | In the **`find_angle([…, table_rotation])`** call chain for hover and place-from-hover, use **`-rot` (negated table angle)** so the third Euler entry matches **`move_component`** / **`place_component_wo_home_specific_xy_cloudlab`**, which already use **`-rot`** in lab and behave correctly. **Minimal change:** flip the sign on the rotation fed into `find_angle`; treat any **`180` vs `-180`** on the first Euler component as a separate follow-up only if testing still shows mismatch. |
| **3** | **Pick / scan-rotate use `target_loc = current_location or inventory_location` without the “place” reconciliation** | `pick_grasp_from_inventory` chooses **`component.current_location or component.inventory_location`** (`robot_manager.py` ~437). If **`current_location` is missing** or still carries **angle notation that does not match what grasp / placed scan-rotate expect**, the fallback **`inventory_location`** (e.g. catalog or legacy pose) can differ from the convention established after **`place_component_wo_home_specific_xy_cloudlab`**. That method already contains **reconciliation logic**: pull **`init_pose` from `initial_positions`**, refresh **`inventory_location`**, and **fill `current_location` from `init_pose` or `inventory_location` if None** (`experiment_manager.py` ~523–534). **That same normalization should run before the first grasp or before placed scan-rotate** (or once up front before any primitive that reads `target_loc` / `loc` for orientation), so angles are consistent **even when the user has not placed via `place_component_wo_home_specific_xy_cloudlab` yet**. |

Supporting detail: `find_angle` behavior, lab vs robot state, and `Pose` field ambiguity remain in §3–§10 below.

---

## 1. Fix #1 — Scan-to-table never sets `is_placed` (primary cause of your stack trace)

**Symptom:** `SCAN_ROTATE_IN_PLACE` in **placed** mode (IDLE, part on breadboard) calls `scan_rotate_placed_cloudlab`, which raises if `not component.is_placed or component.current_location is None`.

**What happens:**

- `RealLabCommunicator.scan_rotate_in_place` chooses **placed** mode from **cloud-labs state** only: IDLE, target in `components`, `presence == PRESENCE_BREADBOARD` (`real.py`). It does **not** check `OpticalComponent.is_placed`.
- After a table scan, `experiment_manager.scan_components_cloudlab` sets `comp.current_location` (and `inventory_location`) when vision finds a tag, but it **never** sets `comp.is_placed = True` (see loop ~249–260 in `lab_automation/managers/experiment_manager.py`).
- `OpticalComponent` defaults `is_placed` to `False` (`lab_automation/objects/base.py`).
- After a successful tag find, **`current_location` and `inventory_location` are both set** from the same vision pose (`scan_components_cloudlab` ~257–260). The usual failure mode for **placed scan-rotate** is therefore **`is_placed` still `False`** (the guard is `not is_placed or current_location is None` — either fails). A workflow **scan → scan/rotate before pick** hits the **`is_placed`** branch today.

**Why pick & place “fixes” it:** `place_from_hover_cloudlab` sets `component.is_placed = True` (and writes a full `Pose`). After that, placed-mode scan-rotate passes the guard.

**Where to fix (conceptual):** Align semantics in one place:

- **lab_automation:** Set `is_placed = True` when `scan_components_cloudlab` records an on-table pose (and consider `False` when not found / off-table), **or**
- **lab_automation:** Relax `scan_rotate_placed_cloudlab` to treat “has `current_location` in robot frame on table” equivalently to `is_placed` for this path, **or**
- **cloud-labs:** Before dispatching placed scan-rotate, ensure `component_map` objects are synced (e.g. set `is_placed` from breadboard presence + valid pose).

The cleanest long-term fix is probably **lab_automation** keeping `is_placed` consistent with every path that establishes `current_location` on the table (scan vs place vs move).

---

## 2. Cloud-labs vs lab_automation precondition mismatch (design / validation gap)

**Symptom:** UI and HTTP layer allow an operation the robot layer rejects.

**Detail:** Cloud-labs infers “on breadboard” from **JSON state** (`presence`, etc.). `lab_automation` infers “placed” from **`OpticalComponent.is_placed`**. Those two are **not** updated from the same events today (see bug 1), so they can diverge.

**Recommendation:** Either duplicate the same predicate on both sides (with tests) or have a single sync step whenever state is refreshed from scan / snapshot / primitives.

---

## 3. What `find_angle` actually does (robot command space)

In `lab_automation/managers/experiment_manager.py`, **`find_angle(euler_angles)`** is **not** a passive frame rename. It:

1. Builds a scipy `Rotation` from **intrinsic / `'xyz'` Euler degrees** (`euler_angles` — typically meaning “gripper-down” style `[Rx, Ry, Rz]` with the third entry tied to table heading).
2. Converts to a rotation vector, applies the documented negations, and returns **`[rx, ry, rz]` with the third component forced to `0.0`** in the current implementation (see the docstring and `target_angle = [-inverted_place[0], -inverted_place[1], 0.0]`).

Those three numbers are what **`AssemblyManager.hover_gripped_to`** passes straight into **`driver.move_to(x, y, z, roll, pitch, yaw)`** — i.e. they are the **robot driver’s orientation triple**, not the same thing as “roll/pitch/yaw from the April tag pose” unless we deliberately map them.

**Implication:** Any path that aims the arm using **vision / scan `Pose.roll/pitch/yaw`** without going through the **same** mapping used for hover/place/move is mixing **two different orientation representations**.

---

## 4. Possible lab-frame vs robot-frame inconsistency in HTTP state after **camera** scan

**Detail:** `set_lab_state` documents that snapshot `measurables.pose` is **lab / UI frame** and converts XY + rotation + Z when writing `component_map` (`real.py` ~789–825).

**`_initialize_state`** (post-`scan_components_cloudlab`) builds `measurables.pose` from `comp.current_location` using **raw** `loc.x`, `loc.y`, and yaw-derived `rotation` without `robot_table_xy_to_lab_xy` / `_z_robot_to_lab` (`real.py` ~660–705).

If the rest of the app assumes **lab coordinates** in `measurables.pose`, the initial scan path may be **writing robot-frame numbers into the same fields** that snapshots treat as lab-frame. That would skew the digital twin / any client that only reads state, even when the physical `OpticalComponent` on the server is correct for motion.

**Verify:** Confirm whether the frontend and APIs expect lab or robot units in `measurables.pose` after `refresh_pose_from_camera` / boot scan.

---

## 5. Vision scan fallbacks and Z

**Mono ceiling fallback** in `scan_inventory_cloudlab` sets `pose_result.z = 0` with a comment “default table height” (`robot_manager.py` ~148–151). Stereo path returns a full pose. Inconsistent Z sources can interact badly with any code path that trusts `current_location.z` before RealSense refinement (though pick uses `fine_adjust_adjustable` for height when `use_vision` is true).

---

## 6. `calc_rotation` uses `yaw` only; falsy yaw becomes 0

In `_initialize_state`, `calc_rotation = getattr(loc, "yaw", None) or 0` (`real.py` ~663). If yaw were ever **legitimately 0.0** vs **missing**, behavior is the same; if a pose used a different primary field for table heading, UI `rotation` could be wrong.

---

## 7. Fix #2 — Use **`-rot`** in hover / place-from-hover (match working pick & place)

**Lab intent:** The working path is already **`move_component` → `place_component_wo_home_specific_xy_cloudlab`** with **`angle=[-180, 0, -rot]`** → **`find_angle`**. Pick & place flows that go through that stack match what you run on the hardware.

**What’s wrong today:** **Hover** and **place-from-hover** build **`find_angle([180.0, 0.0, +trot])`** — same UI angle **`trot`**, but **positive** third Euler entry instead of **negative** (`experiment_manager.hover_component_cloudlab` ~974; `real.py` place-from-hover ~2153).

**Intended fix (minimal):** Change **`+rot` → `-rot`** in those **`find_angle`** inputs (i.e. third argument **`-float(rotation_deg)`** / **`-trot`**), so table heading lines up with **pick / place / move** notation. That is the main correction; it directly targets the “**~360° for no reason**” class of bug when the commanded spin disagrees with the convention the rest of the lab stack already uses.

**Note:** **`move`** also uses **`-180`** on roll where **hover** uses **`+180`**. If negating **`rot` alone** fixes hover in practice, leave the first Euler entry as-is until a retest says otherwise; if not, then consider aligning **`[180, 0, -trot]`** fully with **`[-180, 0, -rot]`** as a second step.

---

## 8. Fix #3 — `target_loc = current_location or inventory_location` vs reconciliation only inside `place_component_wo_home_specific_xy_cloudlab`

### 8.1 Where the bad angles come from (pick)

**`pick_grasp_from_inventory`** (`lab_automation` / `AssemblyManager`) resolves the grasp target as **`target_loc = component.current_location or component.inventory_location`** (see `robot_manager.py` around the start of `pick_grasp_from_inventory`).

Approach **`move_to`** uses **`target_loc.roll`, `target_loc.pitch`, `target_loc.yaw` directly** — **no** `find_angle` (same method, vision path). Whatever triple is on the chosen **`Pose`** is what the arm tries to honor.

If **`current_location` is unset** (or cleared somewhere), **`inventory_location`** is used. That pose may come from a **different source or convention** (catalog defaults, older files, or a path that never ran the same normalization as “full place”). Even when both are set from scan, **vision RPY** is still not the same representation as **`find_angle` output** used after hover/place (see §3, §7).

### 8.2 Logic today that “fixes” poses — but only when that place API runs

**`place_component_wo_home_specific_xy_cloudlab`** (used by **move** from cloud-labs) does more than `find_angle` + motion: it **reconciles** `OpticalComponent` state before `pick_and_place_wo_home_from_current`:

- If **`tag_id in self.initial_positions`**: take **`init_pose`**, assign **`component.inventory_location = init_pose`**.
- If **`component.current_location is None`**: set it from **`init_pose`** or, failing that, **`component.inventory_location`**.

(See `experiment_manager.py` ~523–534.)

That keeps **`current_location`** aligned with the tracked scan origin **`initial_positions`** before a pick-and-place-from-current run. **Grasp-only** (`pick_component_cloudlab` → `pick_grasp_from_inventory`) and **placed scan-rotate** (`scan_rotate_placed_on_table` using **`loc` from `component.current_location`**) **do not** run this block first.

### 8.3 What “makes sense” to implement

**Run the same reconciliation (or a shared helper extracted from `place_component_wo_home_specific_xy_cloudlab`) before any primitive that depends on a consistent `current_location` / orientation for the part on the table** — at minimum **before `pick_grasp_from_inventory`** and **before `scan_rotate_placed_on_table`’s first `move_to`**, and optionally **once** after scan / when loading experiment state so every later call sees one convention.

Optionally still apply a **single orientation mapping** (e.g. via `find_angle` from agreed table-heading Euler) for those `move_to` calls so pick/scan-rotate match move/hover — but **the inventory vs `initial_positions` vs `current_location` ordering issue is independent** and matches the “**we fall back to `inventory_location` with wrong angles**” hypothesis.

### 8.4 Relation to `find_angle` (§3, §7)

Even after reconciliation, **pick** and **placed scan-rotate** still use **raw RPY on `move_to`** unless you add an explicit mapping. Reconciliation fixes **which `Pose` wins** and **sync from `initial_positions`**; **Fix #2** fixes **hover vs move**; together they address “weird angles” from different root causes.

---

## 9. `OpticalComponent.current_location` RPY mixes semantics (saved pose is ambiguous)

- **`place_component_wo_home_specific_xy_cloudlab`** writes **`Pose(..., float(angle[0]), float(angle[1]), float(angle[2]))` using the raw `angle` argument**, not `transformed_angle` (`experiment_manager.py` ~554–555). So after **move**, stored RPY can be the **logical** `[-180, 0, -rot]`, **not** the numbers actually sent to `move_to`.

- **`place_from_hover_cloudlab`** stores **`Pose(..., float(angle[0]), ...)`** from the `angle` passed in (`~940–941`); from cloud-labs that is **`find_angle` output** (driver-style triple with third component `0` from `find_angle`).

So the same three fields sometimes mean **“UI / Euler intent”** and sometimes mean **“post–find_angle driver command”**. Any consumer that assumes one meaning (e.g. reinverting with `get_rotation_from_angle` in `real.py`) will be wrong depending on how the pose was last written.

---

## 10. `get_rotation_from_angle` only lines up if `current_location` came from `find_angle`’s forward family

`RealLabCommunicator.get_rotation_from_angle` (`real.py` ~1046+) assumes the robot triple is recoverable via **`Rotation.as_euler('xyz')`** and reads **`euler[2]`** as the table rotation. That is only trustworthy when the orientation was produced by the **same** forward pipeline as **`find_angle([180,0,θ])`** (or whatever single convention we standardize on). It does **not** apply to **raw vision RPY** on the component after scan/pick.

---

## Suggested fix order (for a later implementation pass)

1. **Fix #1 — `is_placed` after scan** (or relax `scan_rotate_placed_cloudlab` + document) — removes the placed scan-rotate crash when the part is visibly on the table.
2. **Fix #2 — Hover / place-from-hover** — use **`-rot`** in **`find_angle`** (third Euler entry), matching **move / pick & place**; treat **`180` vs `-180`** on roll only if needed after that.
3. **Fix #3 — Location reconciliation** — extract or call the **`place_component_wo_home_specific_xy_cloudlab`** `initial_positions` / `inventory_location` / `current_location` block **before pick and before placed scan-rotate** (or once after scan / boot), so **`pick_grasp_from_inventory`** does not silently use a mismatched **`inventory_location`** when **`current_location`** is missing or stale.

**Follow-ups (still valuable, not in the top three):** normalize what **`Pose.roll/pitch/yaw`** stores after move vs place (§9); fix **`get_rotation_from_angle`** assumptions (§10); audit **`measurables.pose`** lab vs robot after `_initialize_state` (§4).

---

## Reference: call chain for the reported error

1. `dispatch.execute_validated_command` → `lab.scan_rotate_in_place` (`cloud-labs/backend/lab_primitives/dispatch.py`).
2. `RealLabCommunicator.scan_rotate_in_place` → `mode == "placed"` → `experiment.scan_rotate_placed_cloudlab` (`cloud-labs/backend/lab_communicator/real.py`).
3. Guard in `OpticalExperiment.scan_rotate_placed_cloudlab` (`lab_automation/managers/experiment_manager.py` ~1038–1040).
