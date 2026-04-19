# `lab_automation`: what needs to land for the new in-air primitives

**Audience:** maintainers of the **`lab_automation`** repository (the hardware-bound truth — `OpticalExperiment`, `OpticalComponent`, robot + camera drivers). This document is **not** a spec for `cloud-labs` (that is `new_primitives.md` next to it).

**Why this file exists:** Stages 0–7 of the `new_primitives.md` roadmap are complete in `cloud-labs`. The four in-air primitives (**`PICK_COMPONENT`**, **`HOVER`**, **`PLACE_FROM_HOVER`**, **`SCAN_ROTATE_IN_PLACE`**) plus the operator helper **`CONFIRM_HOLDING_TAG`** are fully wired into `lab_primitives`, `backend/main.py`, the mock lab, the UI, and the recipe / golden-state pipeline. What is left is the **real hardware** side. Until `lab_automation` grows the methods below, `RealLabCommunicator` only logs `[REAL LAB] PLACEHOLDER: …` and (when `HOVER_PLACEHOLDER_STATE=1`) mutates state without moving the robot.

This is the hand-off list for **Stage 8** of `new_primitives.md`.

---

## 1. Context refresher (cloud-labs side)

Cloud-labs expects `lab_automation` to expose a motion API that `RealLabCommunicator` can call from each of the five new `async def` methods. The **canonical** lab-state contract (what the UI consumes) already works:

- **`system_status`** adds **`HOLDING`** (and the transient **`HOLDING` + `holding.requires_operator_confirm == true`**).
- **Top-level `holding` field** (`{tag_id, nominal_pose: {x, y, rotation, z}, requires_operator_confirm}`).
- **`tunables.nominal_pose`** and **`measurables.pose`** may carry **`z`** while holding. `z` is **omitted** on placed parts (table pose is implicit in the placement pipeline).

All safety gating (reject `PICK` while `HOLDING`, `HOVER` only for the held tag, block everything while `HOLDING_UNCONFIRMED`, etc.) is already enforced by `_enforce_holding_rules` in `backend/main.py`. **`lab_automation` does not need to re-implement those checks**; it only needs to do the motion.

---

## 2. New methods to add on `OpticalExperiment` (or its robot manager)

Names below are **suggestions** so cloud-labs can bind cleanly in `real.py`. What matters is the behavior; the final names can differ as long as the wiring in `RealLabCommunicator` is updated to match.

### 2.1 `pick_component_cloudlab(component, *, safe_z=None)` — **grasp macro**

Pipeline:

1. **Approach** above `component.inventory_location` (or most recent scan pose). The table XY comes from the `cloud-labs` call; `lab_automation` is responsible for converting that back to robot-table mm if its API doesn't already (see `lab_table_xy_to_robot_xy` in `real.py`).
2. **Descend to pick Z** (the "touch" Z used by `place_component_wo_home_specific_xy_cloudlab` today — whatever that pipeline uses is fine).
3. **Close gripper.**
4. **Retract to safe Z** (`safe_z` or a lab-defined default, e.g. `DEFAULT_HOVER_Z_MM = 40 mm` in cloud-labs; negotiate a single constant that matches the real table).
5. Update `component.is_placed = False` and keep the held component reference around so subsequent `hover` / `place_from_hover` can pick up where this ended.

Fail modes: if the approach can't be planned (e.g. no IK solution), raise — `cloud-labs` will surface it.

### 2.2 `hover_component_cloudlab(component, *, target_x, target_y, rotation, z)` — **in-air move**

Pipeline:

1. Assume gripper is **already closed** on `component` (cloud-labs guarantees this with the `HOLDING` gate).
2. Move in a **collision-free** cartesian/joint path to `(target_x, target_y, rotation, z)`.
3. Do **not** re-grasp, do **not** call the place pipeline. This is the critical design rule from `new_primitives.md` §1: **HOVER must never contain a grasp**.
4. Leave the gripper closed at the target pose.

This method is distinct from `place_component_wo_home_specific_xy_cloudlab` — it should **not** descend to table Z at the end.

### 2.3 `place_from_hover_cloudlab(component, *, target_x, target_y, rotation)` — **release onto the table**

Pipeline:

1. Assume gripper is **already closed** on `component`.
2. Move to hover pose **above** `(target_x, target_y)` (re-use your existing approach path).
3. Descend to place Z.
4. **Open gripper.**
5. Retract to safe Z.
6. Update `component.is_placed = True` and populate `component.current_location` with the final table pose.

Semantic equivalent of a successful `place_component_wo_home_specific_xy_cloudlab`, minus the re-pick step (the part is already in the gripper). If the existing place routine is written as "approach → (optional pick) → place", the cleanest refactor is probably to expose a `skip_pick=True` kwarg rather than duplicate the whole pipeline.

### 2.4 `scan_rotate_*_cloudlab(component, *, theta_min, theta_max, speed_deg_per_s, axis="z")` — **constant-rate sweep, two hardware paths**

**User intent is the same in both cases** ("sweep θ at constant rate") and cloud-labs exposes **one** primitive (`SCAN_ROTATE_IN_PLACE`) and **one** UI button. But the physical sequence depends on whether the part is already in the gripper or sitting on the breadboard, so `lab_automation` should provide **two separate functions**. `RealLabCommunicator.scan_rotate_in_place` picks the right one at dispatch time — see §5 below.

#### 2.4.a `scan_rotate_held_cloudlab(component, *, theta_min, theta_max, speed_deg_per_s, axis="z")` — **in-air sweep**

Precondition: the gripper is already closed on `component` (cloud-labs guarantees this via the `HOLDING` gate in `backend/main.py`).

Pipeline:

1. Sweep the angle parameter named by `axis` (`"z"` only in v1) from `theta_min` to `theta_max` at `|dθ/dt| ≈ speed_deg_per_s`.
2. **Keep XY and Z of the held pose constant** — the point of the primitive is "rotate while hovering so the camera can see the beam sweep past".
3. **Do not open the gripper** at any point. On completion, leave the part still in the gripper at `theta_max`; cloud-labs keeps the system in `HOLDING`.

This is the method closest to a classic "wrist rotate while still gripped" routine — the gripper orientation is what changes, not the arm XY.

#### 2.4.b `scan_rotate_placed_cloudlab(component, *, theta_min, theta_max, speed_deg_per_s, axis="z")` — **on-table sweep via a transient grip**

Precondition: `component` is on the breadboard (not stored, not held). Cloud-labs guarantees the "not held" half (it refuses `SCAN_ROTATE_IN_PLACE` while holding a *different* tag); `lab_automation` is responsible for refusing components that are not on the table.

Pipeline:

1. **Approach** `component.current_location` (same path `PICK_COMPONENT` / `place_component_wo_home_specific_xy_cloudlab` already use).
2. **Descend and close the gripper** on the part (same grasp sub-step as `PICK_COMPONENT`). `component.is_placed` stays `True` — this is *not* a `PICK` session.
3. **Rotate the arm's end-effector / wrist** from `theta_min` to `theta_max` at `|dθ/dt| ≈ speed_deg_per_s`, keeping `(x, y, z)` of the grasped pose locked. The whole point of this path over a per-part motor is that the **arm's rotation range is much larger** than what an onboard motor typically gives, and it works for any component in the catalog regardless of whether it has a motorized mount.
4. **Open the gripper** and **retract** back to the same safe clearance your place pipeline uses.
5. `component.current_location` is updated with the final rotation (`theta_max`); XY stays as it was before the sweep.

This is **not** a `PICK_COMPONENT` + `HOVER` + `PLACE_FROM_HOVER` sequence in disguise. From cloud-labs' point of view the whole primitive is atomic and `system_status` is `BUSY` throughout; at the end the system is back in `IDLE` with the same part on the table at the new rotation. **Do not set `HOLDING` and do not populate `holding`** during the sweep — the transient grip is a hardware implementation detail, not a user-facing holding session.

**Why split this from 2.4.a:**
- **2.4.a (held-mode)** runs inside an existing user-owned `HOLDING` session. The user already called `PICK_COMPONENT`, the gripper is already closed, and the user expects the system to stay in `HOLDING` after the sweep so they can still call `HOVER` / `PLACE_FROM_HOVER`.
- **2.4.b (placed-mode)** starts and ends with the part on the table, gripper empty, `system_status == IDLE`. The grip is momentary and owned by this primitive.

Collapsing them into one function leads to either (a) sneaking a `pick → place` into the held-mode path (which breaks the `HOVER` vs. `PICK` separation by leaving the gripper empty when the user thinks they're still holding), or (b) forcing the user to always `PICK` before scanning, which is awkward when the part is already well-placed and just needs a coarse θ sweep for alignment. Having two entry points keeps each contract unambiguous even though both will likely share the same low-level θ-ramp helper internally.

#### Camera sync — deferred in both branches

`new_primitives.md` §7.3 and Stage 10 defer camera exposure / frame sync / debug image output. v1 of both functions **must not** block waiting for camera frames or exposures — just advance the rotation axis and return. A later milestone will add `exposure`, `frame_sync`, and debug image output, modelled on how `optimize` already drops PNGs into `Camera_Images/`. When that arrives, the two functions stay two functions (camera pipelines can be shared).

### 2.5 `get_gripper_status()` — **boot-time reconciliation hook** (read-only)

Cloud-labs' `RealLabCommunicator._reconcile_holding_on_boot` (see §6.3 of `new_primitives.md`) polls this on every FastAPI startup. Expected return:

```python
{"closed": bool, "confidence": float | None, "source": str}
```

or a plain `bool` — cloud-labs normalizes both. The **`closed`** flag is the only thing the reconcile logic inspects today: if `True` and the persisted snapshot doesn't already declare a confirmed `HOLDING`, cloud-labs forces `HOLDING_UNCONFIRMED` and blocks the UI until the operator sends `CONFIRM_HOLDING_TAG`.

If **no** hardware sensor exists, returning `closed=False` (the cloud-labs default) is safe — the reconcile is a no-op and cloud-labs trusts the persisted state. So landing this method is **only required once a gripper-closed sensor is wired up on the robot**; until then the rest of the new primitives work without it.

Cloud-labs currently probes these names in order before giving up:

1. `experiment.get_gripper_status()` (preferred — dict or bool)
2. `experiment.robot.get_gripper_status()`
3. `experiment.is_gripper_closed()` (bool accessor)
4. `experiment.robot.gripper_closed` (plain attribute)

Any of those four being truthy is enough to trigger the boot reconcile.

---

## 3. `OpticalComponent` / state additions (optional but recommended)

These are not strictly required — `RealLabCommunicator` can compute them from its own `current_state` — but surfacing them on `OpticalComponent` keeps the two repos in sync:

- **`component.is_held: bool`** — mirrors `holding.tag_id == component.tag_id` on the cloud-labs side. Cleared by `place_from_hover_cloudlab` / raised by `pick_component_cloudlab`.
- **`component.last_hover_pose: Pose | None`** — updated by `hover_component_cloudlab` so external tools can read the current in-air pose without walking into cloud-labs' state.
- **`component.current_location.z`** — already exists via `Pose`, but needs to be populated by the in-air methods so downstream code has a consistent source of truth.

---

## 4. Coordinate / safety conventions to nail down

These are the **open questions** copied from `new_primitives.md` §11. They must be answered in the `lab_automation` PR:

1. **Z origin:** Is `z = 0` the **table surface** or the **robot base**? `DEFAULT_HOVER_Z_MM = 40` in cloud-labs assumes "mm above the table". The two repos must agree; otherwise the UI "safe Z" input and the robot's safe clearance diverge.
2. **Safe clearance:** What is the minimum safe Z that guarantees no post on the breadboard hits a held component mid-hover? That number should live next to `LAB_ROBOT_TABLE_ROTATION_RAD` in `real.py` (or be exposed via `lab_automation`).
3. **E-stop recovery:** If the operator e-stops mid-hover, the gripper is still closed. After releasing the e-stop, the boot reconciliation in §2.5 takes over — **`lab_automation` must not silently resume motion**. Confirm that clearing the e-stop brings the robot to a non-moving state that still reports `closed=True` on the gripper sensor.
4. **SCAN\_ROTATE\_IN\_PLACE on placed parts:** Cloud-labs now allows the primitive in both **`HOLDING`** and **`IDLE` + on-breadboard** states (see §2.4 above); dispatch in `real.py` binds to either `scan_rotate_held_cloudlab` or `scan_rotate_placed_cloudlab` at runtime. `lab_automation` must not collapse them into a single function — the two hardware paths are distinct even if they share helpers.
5. **Coordinate transform:** The lab↔robot rotation (`LAB_ROBOT_TABLE_ROTATION_RAD`) currently lives only in `real.py`. Either move it into `lab_automation` (best), or document it here so both sides apply it consistently for `pick_component_cloudlab` / `hover_component_cloudlab` arguments.

---

## 5. How cloud-labs will consume these once they land

When the `lab_automation` side is ready, the **cloud-labs** change is minimal (this block is the "what to do in `real.py` when Stage 8 arrives"):

1. In `backend/lab_communicator/real.py`, replace the placeholder method bodies for `pick_component`, `hover_component`, and `place_from_hover` with calls into `lab_automation`:

   ```python
   async def pick_component(self, target_id: str, params: Dict[str, Any]):
       comp = self.component_map[target_id]
       await asyncio.to_thread(
           self.experiment.pick_component_cloudlab,
           component=comp,
           safe_z=params.get("safe_z"),
       )
       # then update self.current_state via set_holding(...), same as the placeholder already does.
   ```

   The placeholder state-mutation block (the part inside `if self._hover_placeholder_state:`) already models the correct state transition; the real implementation just replaces the `asyncio.sleep(0.25)` token delay with the real `to_thread(...)` call.

2. `scan_rotate_in_place` already dispatches between the two `lab_automation` functions at runtime — the real.py placeholder probes `experiment.scan_rotate_held_cloudlab` when the target is currently held and `experiment.scan_rotate_placed_cloudlab` when it is on the table, and falls back to the state-only simulation when neither is defined. As soon as either of those methods lands on the automation side the corresponding branch lights up **without cloud-labs code changes**. Example shape of the dispatch:

   ```python
   # In RealLabCommunicator.scan_rotate_in_place (abridged):
   if is_holding(self.current_state) and held_tag(self.current_state) == target_id:
       real_fn = getattr(self.experiment, "scan_rotate_held_cloudlab", None)
       final_status = SYSTEM_STATUS_HOLDING
   else:
       real_fn = getattr(self.experiment, "scan_rotate_placed_cloudlab", None)
       final_status = SYSTEM_STATUS_IDLE
   if callable(real_fn):
       await asyncio.to_thread(real_fn, component=comp,
                               theta_min=..., theta_max=..., speed_deg_per_s=..., axis="z")
   ```

   The two functions may share internal helpers in `lab_automation` (e.g. a common θ-ramp), but **must stay as two entry points** so the held vs. placed contract is unambiguous.

3. Remove (or keep as a debug escape hatch) the `HOVER_PLACEHOLDER_STATE` environment flag.
4. Wire `get_gripper_status()` to the real sensor — the probe order in §2.5 means no further changes are needed on the cloud-labs side once `lab_automation` exposes any of those four attributes.
5. Run the same recipe / golden test used in Stage 4 mock (`PICK → HOVER → HOVER → PLACE_FROM_HOVER`) against the real table, plus a `SCAN_ROTATE_IN_PLACE` against both a held part and an on-table part. The golden comparison in `backend/main.py` already captures `z` and `holding` (Stage 7).

---

## 6. Out of scope for this milestone

Copied from `new_primitives.md` Stage 10 so nothing surprises the `lab_automation` side:

- **Camera exposure / frame sync / debug image output for `SCAN_ROTATE_IN_PLACE`.** First sweep is motion-only.
- **Pick-from-storage / store-from-hover** compound flows.
- **Live ghost updates per scan angle** on the real table (cloud-labs already does this in mock; real can follow later without an API change).

Anything beyond those is welcome but not required to close Stage 8.
