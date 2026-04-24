# `lab_automation`: what needs to land for the new in-air primitives

**Audience:** maintainers of the **`lab_automation`** repository (the hardware-bound truth — `OpticalExperiment`, `OpticalComponent`, robot + camera drivers). This document is **not** a spec for `cloud-labs` (that is `new_primitives.md` next to it).

**Why this file exists:** Stages 0–7 of the `new_primitives.md` roadmap are complete in `cloud-labs`. The four in-air primitives (**`PICK_COMPONENT`**, **`HOVER`**, **`PLACE_FROM_HOVER`**, **`SCAN_ROTATE_IN_PLACE`**) plus the operator helper **`CONFIRM_HOLDING_TAG`** are fully wired into `lab_primitives`, `backend/main.py`, the mock lab, the UI, and the recipe / golden-state pipeline. What is left is the **real hardware** side. Until `lab_automation` grows the methods below, `RealLabCommunicator` only logs `[REAL LAB] PLACEHOLDER: …` and (when `HOVER_PLACEHOLDER_STATE=1`) mutates state without moving the robot.

This is the hand-off list for **Stage 8** of `new_primitives.md` **and** for **Stage B** of `fixing.md`. Those two roadmaps are bundled here because both of them need the same set of eyes — the `lab_automation` maintainer — and they touch overlapping surfaces. Concretely:

- **§§1–4** specify the new methods + conventions needed for the Stage 8 in-air primitives (`PICK`, `HOVER`, `PLACE_FROM_HOVER`, `SCAN_ROTATE_IN_PLACE`).
- **§5** (new) specifies **contract fixes on the _existing_ `_cloudlab` functions** — the ones that already ship today (`scan_components_cloudlab`, `pick_component_cloudlab`, `place_component_wo_home_specific_xy_cloudlab`). The `fixing.md` audit found that `cloud-labs` and `lab_automation` disagree today about which of `inventory_location` / `current_location` is authoritative, and that `pick_component_cloudlab` currently doesn't update either. §5 makes that contract explicit so the two repos can stop reaching into each other's state.
- **§§6–7** describe how cloud-labs will consume all of the above once it lands, and what is out of scope for this milestone.

Both milestones can ship independently — Stage 8 doesn't block Stage B and vice versa — but they're scheduled together here because the same person is likely to land them.

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
4. **Retract to safe Z.** Use `safe_z` if the caller supplies it; otherwise use `lab_automation`'s own `safe_z_retract_robot` constant. **Cloud-labs does not need to know this value** — see §2.6 below: as long as the same constant is used internally here and in `compute_intent_hover_z_lab`, the round-trip with cloud-labs is exact.
5. Update `component.is_placed = False` and keep the held component reference around so subsequent `hover` / `place_from_hover` can pick up where this ended.

Fail modes: if the approach can't be planned (e.g. no IK solution), raise — `cloud-labs` will surface it.

### 2.2 `hover_component_cloudlab(component, *, target_x, target_y, rotation, z)` — **in-air move**

Pipeline:

1. Assume gripper is **already closed** on `component` (cloud-labs guarantees this with the `HOLDING` gate).
2. Move in a **collision-free** cartesian/joint path to `(target_x, target_y, rotation, z)`.
3. Do **not** re-grasp, do **not** call the place pipeline. This is the critical design rule from `new_primitives.md` §1: **HOVER must never contain a grasp**.
4. Leave the gripper closed at the target pose.

This method is distinct from `place_component_wo_home_specific_xy_cloudlab` — it should **not** descend to table Z at the end.

**Coordinate convention for the arguments:** `target_x`, `target_y` arrive already in **robot-table mm** (cloud-labs applies `lab_table_xy_to_robot_xy` before calling). `z` arrives as **`z_robot`**, i.e. the raw robot-frame z command that the controller should drive the flange/tip to — cloud-labs applies the `z_lab → z_robot` transform in `_z_lab_to_robot` before calling this method. `rotation` is in degrees. See §4 below for the full convention spec.

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

### 2.6 `compute_intent_hover_z_lab(component) -> float` — **post-pick intent query** (pure math, no motion)

Cloud-labs calls this **after** `pick_component_cloudlab` returns, to find out which `z_lab` (height of the component's base above the breadboard, in mm) the robot intends to settle at. The value is written straight into `tunables.nominal_pose.z` and `holding.nominal_pose.z` on the cloud-labs side so the UI round-trips cleanly (HOVER without editing z produces **no vertical motion**).

Hard requirements:

- **Pure function. No motion, no hardware I/O.** This is a property of the `lab_automation` configuration only.
- Returns `z_lab` in mm (not `z_robot`). Use whatever `safe_z_retract_robot` the pick macro actually retracts to, and apply the inverse transform from §4 below:

  ```python
  z_lab = safe_z_retract_robot - TABLE_Z0_ROBOT_MM - (component.height_mm - GRASP_OFFSET_MM)
  ```

  `TABLE_Z0_ROBOT_MM`, `GRASP_OFFSET_MM`, and `component.height_mm` are the three convention inputs described in §4 — the same numbers cloud-labs uses in `_z_lab_to_robot`.
- If your retract height is pose-dependent (e.g. different per part), just substitute the appropriate `safe_z_retract_robot` for the given `component`.

If `lab_automation` does not implement this method yet, cloud-labs falls back to `DEFAULT_HOVER_Z_MM = 40.0` and logs a warning. The fallback is safe (the robot won't move anywhere wrong; only the displayed `z_lab` in the UI will be approximate until the helper lands).

Cloud-labs probes these names in order:

1. `experiment.compute_intent_hover_z_lab(component)` (preferred)
2. `experiment.get_intent_hover_z_lab(component)`

Either returning a float is sufficient.

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

## 4. Coordinate / safety conventions

### 4.1 Z convention — **resolved** (`z_lab` vs. `z_robot`)

Cloud-labs / UI / HTTP speak **`z_lab`**: height of the component's *base* above the breadboard, in mm (`0` = resting on the table, `40` = default safe hover). `lab_automation` speaks **`z_robot`**: the raw robot-frame z command. `RealLabCommunicator` is the **only** place they meet.

Forward transform (cloud-labs → `lab_automation`, applied in `real.py` before every call):

```
z_robot = TABLE_Z0_ROBOT_MM + component.height_mm − GRASP_OFFSET_MM + z_lab
```

Inverse transform (`lab_automation` → cloud-labs, applied on any z read-back):

```
z_lab = z_robot − TABLE_Z0_ROBOT_MM − component.height_mm + GRASP_OFFSET_MM
```

Three independent inputs — each owned by exactly one thing so they don't drift:

| Input | Owner | Meaning |
|---|---|---|
| `TABLE_Z0_ROBOT_MM` | Per-setup calibration (cloud-labs env var in `real.py`, mirror the value on the `lab_automation` side) | Robot-frame z reading when the **empty gripper fingers** are just touching the breadboard. One-time touch-off calibration. |
| `GRASP_OFFSET_MM` | Gripper design constant (cloud-labs env var; `lab_automation` should use the same number) | Distance from the **top of the component housing** *down* to the point where the gripper fingers close. `0` ⇒ grips at the very top; `10` ⇒ grips 10 mm below the top. |
| `component.height_mm` | Per-component catalog (`schemas/component_catalog.real.json`; also on `OpticalComponent` if you surface it there) | Total physical height of the part, base-to-top, in mm. Distinct from `size.height` (which is the 2D UI footprint). |

**What this means for the `*_cloudlab` methods:**
- `hover_component_cloudlab(component, *, target_x, target_y, rotation, z)` receives `z` as `z_robot`. Drive the flange there directly; do not re-add any offset.
- `pick_component_cloudlab(component, *, safe_z=None)` owns its retract height internally (`safe_z_retract_robot`). Cloud-labs does **not** command a z during pick.
- `place_from_hover_cloudlab(component, *, target_x, target_y, rotation)` is z-less in its signature because `z_lab = 0` is implicit on placed parts; internally descend to table Z using whatever your existing place pipeline does.
- `compute_intent_hover_z_lab(component)` (§2.6) is the **pure-math helper** cloud-labs calls post-pick to learn which `z_lab` it should display. It applies the inverse transform to `safe_z_retract_robot` internally.

**Safety knobs on the cloud-labs side** (documented here so `lab_automation` doesn't have to duplicate them):
- `MAX_SAFE_HOVER_Z_LAB_MM` (default 200 mm): `RealLabCommunicator.hover_component` rejects any HOVER with `z_lab` outside `[0, MAX_SAFE_HOVER_Z_LAB_MM]` before forward-transforming. So `lab_automation` does not need to re-validate this range — cloud-labs has already rejected anything unsafe.

### 4.2 Other conventions / open questions

1. **Safe clearance (hardware side):** What is the minimum safe Z that guarantees no post on the breadboard hits a held component mid-hover? If your `safe_z_retract_robot` is chosen with that already in mind, no additional work is needed. If there's a per-component minimum, surface it on `OpticalComponent` so cloud-labs can show it in the UI.
2. **E-stop recovery:** If the operator e-stops mid-hover, the gripper is still closed. After releasing the e-stop, the boot reconciliation in §2.5 takes over — **`lab_automation` must not silently resume motion**. Confirm that clearing the e-stop brings the robot to a non-moving state that still reports `closed=True` on the gripper sensor.
3. **`SCAN_ROTATE_IN_PLACE` on placed parts:** Cloud-labs now allows the primitive in both **`HOLDING`** and **`IDLE` + on-breadboard** states (see §2.4 above); dispatch in `real.py` binds to either `scan_rotate_held_cloudlab` or `scan_rotate_placed_cloudlab` at runtime. `lab_automation` must not collapse them into a single function — the two hardware paths are distinct even if they share helpers.
4. **Coordinate transform (XY):** The lab↔robot rotation (`LAB_ROBOT_TABLE_ROTATION_RAD`) currently lives only in `real.py`. Either move it into `lab_automation` (best), or document it here so both sides apply it consistently for `pick_component_cloudlab` / `hover_component_cloudlab` arguments.

---

## 5. Contract fixes on existing `_cloudlab` functions (from `fixing.md` Stage B)

This section is **not about the new in-air primitives** — those are §§1–4. It's about a set of contract fixes to the `_cloudlab`-suffixed methods that already ship in `lab_automation` today. The full context (with line numbers, audit table, and decision rationale) lives in `fixing.md` in the cloud-labs repo. This section is the condensed hand-off.

### 5.1 One-sentence summary

Inside every `_cloudlab`-suffixed function, **`component.current_location` is the canonical "where is this part now" field.** Every `_cloudlab` primitive that moves a part must keep it accurate. `inventory_location` is ignored by cloud-labs — cloud-labs will neither read it nor write it from any `_cloudlab` code path. Whatever `inventory_location` means elsewhere in `lab_automation` is outside this contract.

### 5.2 Why the fix is needed (very short)

The audit in `fixing.md` §2 found that cloud-labs currently reaches across the wall in three places in `backend/lab_communicator/real.py` to patch up `lab_automation`-owned fields (`inventory_location`, `current_location`, `is_placed`), and in three sentinel read-sites. Digging in, the two symptoms are:

1. **`pick_component_cloudlab` doesn't update `current_location`.** After a successful pick, `current_location` still points at the pre-pick pose — the robot "thinks" the part is on the table while it's in the gripper. Every other motion primitive (`place_component_wo_home_specific_xy_cloudlab` etc.) updates `current_location` on the way out; `pick_component_cloudlab` is the odd one out.
2. **`scan_components_cloudlab` writes `inventory_location` but not `current_location`.** Cloud-labs currently patches that up manually with `comp.current_location = comp.inventory_location` right after the scan. We want to stop doing that and read `current_location` directly — which only works if the scan writes it.

Plus the broader point that `inventory_location` has drifted into three overlapping semantics across the codebase (scan result, pick source, "is this tag registered?"). The fix is not to unify the two fields or enforce an invariant between them — it's to **stop caring about `inventory_location` inside `_cloudlab` paths** entirely, so the question of what it means elsewhere becomes somebody else's problem.

### 5.3 Contract items (the work)

In priority order.

1. **`pick_component_cloudlab` must update `comp.current_location` on success** (post-retract). Pose to write:
   - `x_robot`, `y_robot` = actual pick-approach XY (robot frame).
   - `z = safe_z_retract_robot` (whatever height your pipeline actually retracts to — the same constant that `compute_intent_hover_z_lab` in §2.6 uses for its inverse transform).
   - `yaw` = the pick pose yaw (robot frame).
   - `roll = 180, pitch = 0` if that's your convention for held parts; otherwise whatever matches your other primitives.
   *Rationale:* parity with `place_component_wo_home_specific_xy_cloudlab`; closes the "robot thinks the part is on the table while it's in the gripper" class of bugs.
   *Acceptance:* after `pick_component_cloudlab` returns successfully, `comp.current_location` reflects the actual retract pose, byte-for-byte.

2. **`scan_components_cloudlab` must write `comp.current_location`** — today it only writes `inventory_location`. Whether it *also* writes `inventory_location` is outside our scope; cloud-labs won't read that field.
   *Acceptance:* after `scan_components_cloudlab` returns, every discovered component has a non-None `current_location` populated with the scanned pose.

3. **No `_cloudlab` function reads or depends on `inventory_location`.** Audit the existing ones — `pick_component_cloudlab` likely reads `inventory_location` today as "where to pick from". Switch it to read `current_location`. After item 2 above lands, post-scan the two fields mean the same thing for us, so the switch should be behavior-preserving; please confirm that's actually the case before shipping.
   *Acceptance:* `rg -n "inventory_location" lab_automation/**/*_cloudlab*.py` returns either zero matches or only clearly-commented exceptions we've signed off on.

4. **Publish a per-primitive read/write matrix** in a short table at the top of the `lab_automation` repo (or appended to the bottom of this doc). See §5.4 below for the cloud-labs-view template — fill in the "written by lab_automation" column.

5. **Document coordinate frames on `Pose` attributes.** In a single paragraph near the `OpticalComponent` definition: `current_location.x`, `.y`, `.z`, `.yaw` are always in **robot frame**. Cloud-labs handles the inverse transforms if/when it needs to surface these in the UI. This is a pure doc item but blocks future confusion; see `fixing.md` §3.1 for why it matters.

6. **(Optional) Expose `is_registered(comp) -> bool`.** Equivalent to `comp.current_location is not None` under the new rule. Nice to have so cloud-labs' sentinel reads don't hard-code the attribute-name check. If you don't ship it, cloud-labs will just check the attribute directly.

7. **(Already tracked above)** `compute_intent_hover_z_lab(component) -> float` — see §2.6. Not re-described here; just noting it's part of the same hand-off.

### 5.4 Per-primitive read/write matrix

Cloud-labs-view template. The **"cloud-labs expects"** column is the contract from this doc. Fill in the **"today's lab_automation behavior"** column on your side so we can diff them.

| `_cloudlab` primitive | Cloud-labs expects: writes `current_location`? | Cloud-labs expects: writes `is_placed`? | Cloud-labs expects: reads `inventory_location`? | Today's `lab_automation` behavior (fill in) |
|---|---|---|---|---|
| `scan_components_cloudlab` | ✅ (must write, per §5.3 item 2) | ✅ (`True` for parts found on table) | No | *(TODO)* |
| `pick_component_cloudlab` | ✅ (post-retract pose, per §5.3 item 1) | ✅ (`False` on success) | No (switch source to `current_location`, per §5.3 item 3) | *(TODO)* |
| `place_component_wo_home_specific_xy_cloudlab` | ✅ (post-place pose — unchanged, already does this) | ✅ (`True`) | No | *(already correct)* |
| `place_from_hover_cloudlab` (Stage 8) | ✅ (post-place pose) | ✅ (`True`) | No | *(new method)* |
| `hover_component_cloudlab` (Stage 8) | ✅ (in-air pose; `is_placed` unchanged) | No write (stays `False`) | No | *(new method)* |
| `scan_rotate_held_cloudlab` (Stage 8) | ✅ (update yaw at end; `is_placed` unchanged) | No write (stays `False`) | No | *(new method)* |
| `scan_rotate_placed_cloudlab` (Stage 8) | ✅ (update yaw at end; XY/Z unchanged) | ✅ (stays `True` — transient grip only, see §2.4.b) | No | *(new method)* |

Reads of `current_location` are allowed everywhere and aren't tracked in this matrix (they're how primitives find the part to operate on).

### 5.5 Decisions already locked (from `fixing.md` §8)

These answered open questions before the contract fix was written. Listed here so nobody reopens them mid-implementation.

- **Q1 — `is_placed` ownership.** Cloud-labs keeps pushing `is_placed` in `set_lab_state` (load-state). The saved JSON is ground truth because camera-derived startup poses are noisy and "save a good run, restart later, reload" is the main use case. Any future refactor toward `set_lab_pose`-only is out of scope here.
- **Q2 — Unify `inventory_location` and `current_location`?** No. Cloud-labs **ignores** `inventory_location` in `_cloudlab` paths. No setter, no invariant, no runtime check. `current_location` is the one canonical field. Outside `_cloudlab` functions, `inventory_location` can keep whatever meaning non-cloudlab callers want.
- **Q3 — Scan semantics.** Resolved by §5.3 item 2: scan must write `current_location`. Whether it *also* writes `inventory_location` is not our concern.
- **Q4 — "HTTP snapshot".** Clarified as "saved lab-state JSON" (the thing `set_lab_state` consumes). Load-state continues to reset `holding` to empty and `system_status` to `IDLE`; a `HOLDING` state is never restored from a snapshot. The physical gripper sensor reconciliation in §2.5 above is the single source of truth for "is something held on boot?".
- **Q5 — Homing.** Homing is a macro composed of atomic primitives (`PICK` → `PLACE_FROM_HOVER` or `MOVE_COMPONENT`), not a new `home_component_cloudlab`. Because every atomic primitive keeps `current_location` coherent, homing falls out for free once items 1–3 above land.

### 5.6 Not in scope (explicitly)

An earlier draft of `fixing.md` proposed a few things that got scoped out. Listing them so they don't resurface as "suggestions" during implementation:

- ❌ `Component.set_location(pose, *, is_placed=None)` atomic setter that writes both `inventory_location` and `current_location`. Dropped — the "ignore `inventory_location`" decision made it unnecessary.
- ❌ Any `inventory_location == current_location` invariant, in `_cloudlab` code or elsewhere.
- ❌ Rename of `inventory_location` or merging it with `current_location`. If non-cloudlab callers want to keep using it for "home pose" semantics, that's fine and unaffected by this work.

### 5.7 Sequencing with Stage 8

Stage 8 (new in-air primitives, §§1–4) and Stage B (this section) can ship independently — neither blocks the other. When both are done, cloud-labs will do its own Stage C cleanup (remove all `inventory_location` references from `real.py`, swap sentinel reads to `current_location`, narrow `set_lab_state` writes). That's tracked in `fixing.md` Stage C and is a pure cloud-labs change once this section's items 1–3 land.

---

## 6. How cloud-labs will consume these once they land

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

## 7. Out of scope for this milestone

Copied from `new_primitives.md` Stage 10 so nothing surprises the `lab_automation` side:

- **Camera exposure / frame sync / debug image output for `SCAN_ROTATE_IN_PLACE`.** First sweep is motion-only.
- **Pick-from-storage / store-from-hover** compound flows.
- **Live ghost updates per scan angle** on the real table (cloud-labs already does this in mock; real can follow later without an API change).

Anything beyond those is welcome but not required to close Stage 8.
