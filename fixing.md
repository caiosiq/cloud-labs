# Fixing: `lab_automation` ↔ cloud-labs state wall

**Status:** proposal / not yet implemented (apart from one safety fix called out below). **Owners:** cloud-labs + lab_automation.

This document exists because while reviewing the new hover/pick primitives we discovered that the boundary between cloud-labs (this repo) and `lab_automation` (the hardware-bound experiment manager) is leaky in a few well-defined places, and the recent `z_lab` / `z_robot` split (see `new_primitives.md` §10.5) turned one of those leaks into a concrete safety bug. The scope of this doc is:

1. Write down the current architecture wall and where it leaks.
2. Flag the safety bug that the z convention just exposed.
3. Name the `current_location` / `inventory_location` confusion concretely, with line numbers, so the next person to touch this code has a map.
4. Propose a clean split of responsibility and the small number of helpers `lab_automation` would need to expose to make that split real.
5. Lay out a three-stage roadmap (safety fix now, docs/contracts next, code cleanup once `lab_automation` ships the helpers).

Nothing in this doc invents new primitives — it only tidies existing ones. If you're looking for the primitives themselves, see `new_primitives.md`.

---

## 1. Architecture wall, restated

There are two repos / process boundaries to keep straight. This is the same wall described in the top-level `README.md`, just viewed from the perspective of state management.

### `lab_automation` — the hardware-bound manager

- Owns `Pose` objects and the `component` object graph, including:
  - `component.inventory_location` — a `Pose` in **robot-frame** coordinates.
  - `component.current_location` — a `Pose` in **robot-frame** coordinates.
  - `component.is_placed` — boolean flag.
- Owns every physical motion primitive (`scan_components_cloudlab`, `pick_component_cloudlab`, `place_component_wo_home_specific_xy_cloudlab`, `hover_component_cloudlab`, `scan_rotate_*_cloudlab`, …).
- Owns the safe-Z policy, gripper, table calibration (`TABLE_Z0_ROBOT_MM` is a cloud-labs constant, but it describes the robot's world, not cloud-labs').

### cloud-labs (this repo) — the stable intent + HTTP + UI surface

- Owns `tunables` (intent), `measurables` (last-known state), `holding`, `system_status`.
- Speaks **`z_lab`** everywhere (height of component base above breadboard; see `new_primitives.md` §10.5).
- Exposes an HTTP API that is deterministic whether mock or real is behind it.
- Never does physics or safety reasoning — that's `lab_automation`'s job.

### The handshake today

Cloud-labs calls a primitive, passing intent in **robot-frame XY + z_robot**. The primitive does the motion and (should) keep `component.current_location` coherent. Cloud-labs updates its own `tunables` / `measurables` / `holding` independently. Neither side is supposed to reach into the other's state.

The reality is that cloud-labs **does** reach across in three places, and at least one of them is actively incorrect now.

---

## 2. Audit: every place cloud-labs touches `lab_automation`'s state today

All line numbers are `backend/lab_communicator/real.py` on the current branch.

### Writes across the wall

| # | Location | What happens | Justified? |
|---|---|---|---|
| W1 | `_initialize_state`, ~line 613 | `comp.current_location = comp.inventory_location` right after the scan. | **No.** `scan_components_cloudlab` should (or can be asked to) set `current_location` itself as part of executing the scan. Cloud-labs is fixing up lab_automation's internal state because the scan leaves it stale. |
| W2 | `set_lab_state`, ~lines 759–761 | Constructs a `Pose(x, y, z, roll, pitch, yaw)` and writes `comp.inventory_location = p`, `comp.current_location = p`. | **No** in the long run. This is legitimate "push external truth into the robot" work, but it should go through a single `lab_automation` helper rather than poke at attributes directly. |
| W3 | `set_lab_state`, ~line 763 | `comp.is_placed = is_on_table(entry)` | Same as W2 — legitimate push of external truth, but should go through a helper. |

### Reads across the wall

| # | Location | What we read | Used as | OK? |
|---|---|---|---|---|
| R1 | `_initialize_state`, ~lines 597, 611, 614 | `comp.inventory_location` | "Where did the scan find this part?" | OK in spirit, but coupled to W1. If W1 goes away, this should read `comp.current_location` instead. |
| R2 | `move_component`, ~line 1143 | `comp.inventory_location` truthy? | Sentinel "robot knows this tag". | OK in spirit, but relies on a field that may or may not be populated depending on scan flow. Should become `lab_automation.is_registered(comp)` or similar. |
| R3 | `pick_component`, ~lines 1873–1874 | `comp.inventory_location` truthy? | Same sentinel as R2. | Same as R2. |

No other spots in `real.py` touch these attributes — the wall is leaky in exactly 3 writes and 3 read-sites, all traceable to two functions (`_initialize_state`, `set_lab_state`) plus two sentinel checks.

**Frame-consistency addendum.** Separate from attribute-level leaks, the wall also has two places where coordinates *cross* it without going through a proper transform helper:

| # | Location | What crosses | Today's treatment |
|---|---|---|---|
| F1 | `set_lab_state`, ~line 748 | `z_lab` → `z_robot` | **Not transformed** — direct copy. Latent safety bug once `z_lab ≠ 500` (see §3). |
| F2 | `set_lab_state`, ~line 752; `move_component`, ~line 1152 | `theta_lab` → `yaw_robot` | **Inconsistently transformed** — `set_lab_state` passes through unchanged, `move_component` dispatches `-rot`. No single helper, no agreement on convention (see §3.1). |

Both get a helper in Stage A. XY is already correctly transformed via `lab_table_xy_to_robot_xy`.

---

## 3. The immediate consistency bugs (introduced / exposed by the z convention)

`set_lab_state`, current state on this branch:

```742:764:backend/lab_communicator/real.py
        # Apply to component_map so pick/place uses correct coordinates.
        # Snapshot poses are lab / UI mm; automation expects robot table mm.
        for tag_id, entry in components.items():
            pose = ((entry or {}).get("measurables") or {}).get("pose") or {}
            x_lab = float(pose.get("x", 0.0))
            y_lab = float(pose.get("y", 0.0))
            x, y = lab_table_xy_to_robot_xy(x_lab, y_lab)
            z = float(pose.get("z", 500.0))  # z isn't stored in current UI payload; default matches scan

            roll = 180
            pitch = 0
            yaw = float(pose.get("rotation"))

            comp = self.component_map.get(tag_id)
            if not comp:
                # If missing from map, skip (UI will still render, but robot may not know it).
                continue

            p = Pose(x=x, y=y, z=z, roll=roll, pitch=pitch, yaw=yaw)
            comp.inventory_location = p
            comp.current_location = p

            comp.is_placed = is_on_table(entry) if isinstance(entry, dict) else False
```

`x`/`y` are forward-transformed from lab frame to robot frame with `lab_table_xy_to_robot_xy`. Good.

**`z` is not transformed.** Before the hover work, no primitive ever wrote `pose["z"]`, so the `500.0` default always won and happened to be a valid `z_robot` (gripper rest height). That worked by accident.

After the hover work, `PICK` / `HOVER` write **`z_lab`** into `measurables.pose.z` (e.g. `40` for a default hover). If a user saves a snapshot while a component is in-air and then loads it later, we will write `z_lab = 40` straight into `comp.inventory_location.z`, where `lab_automation` reads it as **`z_robot = 40`** — well below the breadboard surface. The next primitive that moves this part will happily dispatch the robot to crash into the table.

XY is safe. Z is not. This is the smallest, bluntest fix in the doc and should land before anything else.

**Proposed fix (Stage A below):** forward-transform `z_lab` to `z_robot` via `_z_lab_to_robot(tag_id, z_lab)` before building the `Pose`. Handle the "tag not in catalog" case by falling back to `TABLE_Z0_ROBOT_MM` (the correct default rather than the current magic `500.0`).

**Rejected alternative:** stripping `z` from the snapshot on load and letting `lab_automation` pick a safe retract height. This breaks the "teach and repeat" promise of the digital twin: if a user spends an hour tuning a `HOVER` height for an alignment, saves the state, and then comes back tomorrow, loading that state has to reproduce the pose they saved — not snap back to a default plane and force them to retune. Save/load is explicitly for *not* losing work, so the transform is the only acceptable path.

### 3.1 The same shape, one level up: rotation / yaw

The same block of `set_lab_state` has a parallel, latent version of the same bug for rotation:

```752:752:backend/lab_communicator/real.py
            yaw = float(pose.get("rotation"))
```

`pose["rotation"]` is a **lab-frame** angle (what the UI shows, what `tunables.nominal_pose.rotation` / `measurables.pose.rotation` store). `yaw` is a **robot-frame** angle (what `comp.inventory_location.yaw` / `comp.current_location.yaw` store and what `lab_automation` dispatches against). These are not the same quantity in general — just as `x_lab, y_lab` differ from `x_robot, y_robot` by a rotation of the optical table relative to the robot base, `theta_lab` can differ from `yaw_robot` by an additive offset (and possibly a sign flip).

Today, by empirical accident, `yaw_robot == theta_lab` is probably close enough to "correct" that it hasn't caused visible misplacement. But this is the exact same class of "one coordinate frame wearing another frame's units" that caused the z bug. And the repo already has evidence of ad-hoc rotation fixes, which is the tell-tale sign the abstraction is missing:

```1152:1152:backend/lab_communicator/real.py
                    angle=[-180, 0, -rot],
```

`move_component` flips the sign of `rot` and bakes a `[-180, 0, ...]` roll/pitch in when dispatching `place_component_wo_home_specific_xy_cloudlab`. Meanwhile, `set_lab_state` passes `rotation` straight through as `yaw`. **These two paths disagree** about the lab→robot rotation convention right now. Nobody has noticed because the optical table happens to be close enough to aligned that the error is small, but the asymmetry is real.

**Proposed fix (Stage A below):** introduce a `lab_rotation_to_robot_yaw(theta_lab: float) -> float` helper alongside `lab_table_xy_to_robot_xy` and `_z_lab_to_robot`, mirroring the structure we already use for xy and z. **For now, this helper is the identity (`return theta_lab`)** — we have not physically calibrated an offset, and the empirical behavior in the lab today is "yaw == rotation". But having the helper in place means:

- Every cross-wall rotation passes through one place.
- If/when the rotation offset is measured, the fix is a one-line change to the helper body.
- The `set_lab_state` / `move_component` inconsistency gets forced into a single place where it can be resolved by reading one function.
- We get the inverse `robot_yaw_to_lab_rotation` for free when we need to display robot-reported angles in the UI.

Scope note: this is an architectural plumbing fix, not a calibration fix. We are not claiming the lab has a measurable rotation offset between frames; we are saying that assuming there's *never* one is an unsafe default for a lab that's going to evolve.

---

## 4. The `inventory_location` vs `current_location` mess

Across the codebase these two fields have drifted into overlapping, context-dependent semantics.

### What the names *should* mean (proposal, to be confirmed on the `lab_automation` side)

- **`inventory_location`**: where this part *lives* when off-table (storage rack slot, parking pose). Set once at system bring-up / scan-of-empty-rack. **Immutable** under normal operation; only updated when a part is re-homed.
- **`current_location`**: where the robot believes this part *is right now*, in real time. Updated by **every** primitive that moves the part (including `pick`, which takes the part into mid-air).

### What they actually do today

- `inventory_location` is used in at least three different roles depending on the caller:
  1. In `_initialize_state` it's treated as "where the scan *found* the part right now" — i.e. a *current* location, not a home.
  2. Inside `pick_component_cloudlab` it's read as "where to go pick the part up from" — consistent with either meaning, depending on whether the part was just scanned (current) or hasn't moved since homing (home).
  3. In cloud-labs' sentinel reads (R2, R3 above) it means "does the robot have any registration for this tag at all?" — a lifecycle flag, not a pose.
- `current_location` is updated by `place_component_wo_home_specific_xy_cloudlab` as a side effect of motion, **but not by `pick_component_cloudlab`**. After a pick, `current_location` keeps pointing at the pre-pick pose even though the part is now floating in the gripper. Whoever reads `current_location` next gets a stale lie.

This is the single highest-probability source of future bugs. A primitive that assumes `current_location` is fresh will quietly go to the wrong place.

---

## 5. Coordinate convention — one-page recap

(Extracted so this doc stands on its own. Authoritative source for z is `new_primitives.md` §10.5.)

The wall between cloud-labs and `lab_automation` has **three** frame transforms, one per axis of the pose. They should be applied symmetrically whenever a coordinate crosses the wall.

| Axis | Cloud-labs side (what UI / state stores) | `lab_automation` side | Forward transform (cloud-labs → lab) | Inverse |
|---|---|---|---|---|
| xy | `pose.x`, `pose.y` = **lab-frame mm** | `current_location.x`, `.y` = **robot-frame mm** | `lab_table_xy_to_robot_xy(x_lab, y_lab)` | `robot_xy_to_lab_table_xy(...)` (if/when needed) |
| z | `pose.z` = **`z_lab`** mm (base above breadboard) | `current_location.z` = **`z_robot`** mm | `_z_lab_to_robot(tag_id, z_lab)` | `_z_robot_to_lab(tag_id, z_robot)` |
| rotation / yaw | `pose.rotation` = **`theta_lab`** deg (table-frame) | `current_location.yaw` = **`yaw_robot`** deg (robot base-frame) | `lab_rotation_to_robot_yaw(theta_lab)` — **proposed; identity today** | `robot_yaw_to_lab_rotation(yaw_robot)` — **proposed; identity today** |

z-axis details (unchanged from `new_primitives.md` §10.5):
- `z_lab = 0` → part on the table; `z_lab = 40` → default hover.
- `z_robot = TABLE_Z0_ROBOT_MM + component.height_mm − GRASP_OFFSET_MM + z_lab`
- `z_lab  = z_robot − TABLE_Z0_ROBOT_MM − component.height_mm + GRASP_OFFSET_MM`

Rotation details:
- Today, `lab_rotation_to_robot_yaw` is the identity. Empirically, the optical table is close enough to aligned with the robot base that `yaw = theta_lab` hasn't caused visible misplacement.
- The helper exists to (a) centralize the convention so both `set_lab_state` and `move_component` agree, and (b) give us a one-line fix path if calibration ever reveals a real offset.
- The existing `move_component` dispatches as `angle=[-180, 0, -rot]` — that `-rot` sign flip is effectively an inline, ad-hoc `lab_rotation_to_robot_yaw`. Once the helper exists, that call should read `angle=[-180, 0, lab_rotation_to_robot_yaw(rot)]` and the helper body decides whether to negate or not.

Invariants:
- **Everything in cloud-labs state** (`measurables.pose.{x,y,z,rotation}`, `tunables.nominal_pose.{...}`, `holding.nominal_pose.{...}`, HTTP payloads, UI) is in **lab frame**: `x_lab`, `y_lab`, `z_lab`, `theta_lab`.
- **Everything in `lab_automation` state** (`comp.current_location.{x,y,z,yaw}`, `comp.inventory_location.{...}`) is in **robot frame**: `x_robot`, `y_robot`, `z_robot`, `yaw_robot`.
- Cloud-labs never copies a coordinate from one frame into the other without going through the matching helper.

The bugs in §3 / §3.1 are the only places in cloud-labs that currently violate this rule (z is wrong; rotation is right by accident because the helper is identity, but uses the wrong convention in one of the two spots that touch it).

---

## 6. Proposal: a clean wall

### 6.1 What each side owns

The core rule, restated: **cloud-labs pretends `inventory_location` does not exist.** It is never read and never written from `_cloudlab` code paths. `current_location` is the one canonical "where is this part now" field, and that's all cloud-labs cares about. This is the Q2 decision below — simpler and safer than the previous draft's "setter-enforced invariant" idea because it removes the coupling entirely: if `inventory_location` is outside our model, we can't accidentally lie about it.

**`lab_automation` side (hardware repo):**
- Owns `Pose`, `inventory_location`, `current_location`, `is_placed`, robot-frame coordinates, safe-Z policy.
- **Every `_cloudlab`-suffixed primitive maintains `current_location` correctly.** In particular:
  - `scan_components_cloudlab` must write `current_location` (not only `inventory_location` as it does today).
  - `pick_component_cloudlab` must write `current_location` on success — this is the main bug we flagged in §4; currently it writes neither field.
  - `place_component_wo_home_specific_xy_cloudlab` already writes `current_location` — keep as-is.
  - `hover_component_cloudlab`, `scan_rotate_held_cloudlab`, `scan_rotate_placed_cloudlab` — each updates `current_location` to match the pose they left the part at.
  - **None** of these functions are required to read or write `inventory_location`. The field is effectively dead weight inside `_cloudlab` paths. Outside them, `inventory_location` can mean whatever non-cloudlab users of `lab_automation` want it to mean — not our concern, and we explicitly don't touch it either way.
- Optional, for API cleanliness:
  - **`is_registered(comp) -> bool`** — equivalent to `comp.current_location is not None` under the new rule. Nice-to-have so cloud-labs doesn't hard-code the attribute-name check; if not shipped, cloud-labs just checks the attribute directly.
- Pure-math helper already committed in `new_primitives.md` §10.5:
  - **`compute_intent_hover_z_lab(component) -> float`** — no motion.
- **No `.set_location(...)` setter needed.** An earlier draft of this doc proposed one to atomically update both location fields; that's obsolete now that `inventory_location` is out of cloud-labs' model.

**cloud-labs side (this repo):**
- Owns `tunables`, `measurables`, `holding`, `system_status`, **lab-frame coordinates** (`x_lab`, `y_lab`, `z_lab`, `theta_lab`), UI.
- **Zero** reads or writes of `comp.inventory_location` — anywhere, ever, in `_cloudlab` paths.
- **Writes `comp.current_location`** when (and only when) it needs to push external truth into the robot's model. That's exactly one place: `set_lab_state` (snapshot load). **Writes `comp.is_placed`** in the same place, per the Q1 decision.
- **Reads `comp.current_location`** as the canonical "where is the part now" field when it needs a robot-frame starting pose. In practice this is rare — most primitives are dispatched with explicit target coordinates, not "start from current".
- Owns and maintains the three frame-transform helpers (see §5), all living in `backend/lab_communicator/real.py`:
  - `lab_table_xy_to_robot_xy(x_lab, y_lab)` — exists today.
  - `_z_lab_to_robot(tag_id, z_lab)` / `_z_robot_to_lab(tag_id, z_robot)` — exist today.
  - `lab_rotation_to_robot_yaw(theta_lab)` / `robot_yaw_to_lab_rotation(yaw_robot)` — **proposed, identity today, introduced in Stage A**.
- Applies these helpers at every cross-wall call. Every coordinate that crosses the wall passes through the appropriate helper — no raw `pose.get("rotation")` → `yaw` or `pose.get("z")` → `z` writes.

### 6.2 What this buys us

- The `_initialize_state` scan no longer does the `current_location = inventory_location` patch — `scan_components_cloudlab` sets `current_location` itself (Stage B contract fix), and cloud-labs reads it directly.
- `set_lab_state` collapses from "write 3 attributes on a foreign object (two of which might mean different things to different readers)" to "write `current_location` + `is_placed`, that's it". `inventory_location` writes go away entirely.
- `move_component` / `pick_component` sentinel checks stop depending on which attribute happens to be populated today; they check `current_location is not None` (directly or via `is_registered`).
- Cloud-labs stops depending on the `inventory_location` field existing at all. If the `lab_automation` team later renames, merges, or deletes it, nothing in cloud-labs breaks.
- No cross-function invariant to enforce, no setter to maintain, no runtime assertion to trip. The previous "setter + invariant" approach had the right instinct but added mechanism; this approach just removes the problem.

---

## 7. Contract fixes needed inside `lab_automation`

Separately from what cloud-labs does, these are items we'd like the `lab_automation` team to land (or at least confirm the semantics of). They belong in `labautomation_new_primitives.md` as a new "Contract fixes" section once this doc is agreed.

The organizing principle: inside every `_cloudlab`-suffixed function, `current_location` is the canonical "where is this part now" field. It must be kept accurate by every primitive that moves the part. `inventory_location` is ignored — neither read nor written — by `_cloudlab` functions.

1. **`pick_component_cloudlab` must update `comp.current_location` on success** (post-retract). The pose written: `x_robot`, `y_robot` from the pick approach; `z = safe_z_retract_robot`; `yaw` from the pick pose.
   *Rationale:* parity with `place_component_wo_home_specific_xy_cloudlab`; closes the "robot thinks the part is on the table while it's in the gripper" class of bugs.
   *Acceptance:* after `pick_component_cloudlab` returns successfully, `comp.current_location` reflects the actual retract pose, byte-for-byte.
2. **`scan_components_cloudlab` must write `current_location`** — today it only writes `inventory_location`, which is why cloud-labs currently patches it up manually at `_initialize_state` line 613. After this fix, cloud-labs can read `current_location` directly with no patch.
   *Acceptance:* after `scan_components_cloudlab` returns, every discovered component has a non-None `current_location` in the scanned pose. Whether it also writes `inventory_location` is outside our scope.
3. **No `_cloudlab` function reads or depends on `inventory_location`.** Today `pick_component_cloudlab` likely uses `inventory_location` internally as "where to pick from"; this needs to switch to `current_location`. Any other `_cloudlab` primitive that currently reads `inventory_location` should be audited and switched.
   *Acceptance:* `rg -n "inventory_location" lab_automation/**/*_cloudlab*.py` returns zero matches (or only assignments that we can verify nobody downstream cares about).
4. **Explicit per-primitive read/write matrix** — in `labautomation_new_primitives.md`, a small matrix listing which `_cloudlab` primitive reads / writes `current_location` and `is_placed`. No prose, just the matrix. Makes future audits trivial.
5. **Confirm:** `*.z` on `current_location` is always `z_robot` (robot-frame). Document it next to the matrix. Same for `*.yaw` being robot-frame yaw.
6. **Expose `is_registered(comp) -> bool`** — optional but nice; equivalent to `comp.current_location is not None` under the new rule. See §6.1.
7. **Expose `compute_intent_hover_z_lab(component) -> float`** (already agreed; tracked in `labautomation_new_primitives.md` §2.6).

**Not in scope (explicitly dropped from earlier draft):**
- `Component.set_location(...)` atomic setter.
- The `inventory_location == current_location` invariant.
- Any requirement that non-cloudlab parts of `lab_automation` change their use of `inventory_location`.

---

## 8. Open questions — decisions + analysis

All five questions are now answered (2026-04-17). The decisions are locked for the purposes of Stages A–C; anything labelled "future work" below is explicitly *not* on this roadmap.

### Q1. Is `is_placed` something cloud-labs needs to push?

`set_lab_state` currently writes `comp.is_placed`. If `lab_automation` could derive it from `current_location` + its own state machine, cloud-labs could stop pushing it. If `lab_automation` genuinely needs the external fact, the arg stays.

**Decision (2026-04-17):** Cloud-labs keeps pushing `is_placed`. When `set_lab_state` runs, the saved JSON is treated as ground truth for the system, and `is_placed` is part of that truth.

The operational reason: load-state exists mostly because top-camera scans at startup give bad positions, while a JSON written after a robot-placed run is accurate. Restarting the UI without moving anything should reproduce the pre-restart world, so load-state has to be allowed to assert "this part is on the table" without doing its own scan.

*Future work (not in this roadmap):* collapse `set_lab_state` into a narrower `set_lab_pose` that pushes only the pose (no presence/placement flags) and lets `lab_automation` derive the rest. That's a strictly better world, but it's a bigger refactor and blocked on the inventory/current unification below.

**Analysis / risks:**
- The decision is consistent with the rest of the system: `presence`, `placement.mode`, `holding` are all already "snapshot as ground truth" today. Making `is_placed` the odd one out would be more surprising, not less.
- Trust risk: nothing validates that the snapshot is physically plausible. If a user loads a snapshot that claims `is_placed=True` but somebody moved the part by hand in the meantime, the robot will happily try to pick "air" at the stored pose. The existing mitigation — `_reconcile_holding_on_boot` — only reconciles the gripper, not on-table parts. This is not a regression; just a pre-existing property of load-state worth calling out in docs.
- With the Q2 decision below, cloud-labs writes `current_location` + `is_placed` directly (both inside `set_lab_state`, nowhere else). No setter, no atomicity guarantee needed — the two are written on consecutive lines of the same function and nothing else in cloud-labs touches them.

### Q2. Can `inventory_location` and `current_location` be unified?

Two overlapping fields are the single largest source of confusion in the audit. The earlier draft of this section proposed enforcing `inventory_location == current_location` via a setter. The final answer is simpler and stronger.

**Decision (2026-04-17, revised):** Inside every `_cloudlab`-suffixed function and everywhere in cloud-labs, treat `inventory_location` as if it doesn't exist. Never read it, never write it. `current_location` becomes the single canonical field for "where is this part now", and every primitive that moves a part is responsible for updating it.

Our experiments don't use an "inventory" in the hardware sense — every component is placed by the robot on the breadboard, not stored in a rack slot that the robot picks from. So there's nothing for `inventory_location` to represent in cloud-labs' model. Trying to maintain an invariant between it and `current_location` is adding coupling for no gain.

Outside `_cloudlab` functions, other lab users may use `inventory_location` with its original "home pose" semantics; that's fine and unaffected by this decision.

**Analysis / risks:**
- This is strictly simpler than the setter approach. Fewer moving parts (no setter, no invariant, no runtime check), and the "ignore a field" rule is easier for future maintainers to follow than "always use this setter, because otherwise a silent invariant breaks".
- It also means a smaller contract surface on the `lab_automation` side: `pick_component_cloudlab` only needs to update one field (`current_location`), not coordinate two through a setter.
- **Real risk:** `scan_components_cloudlab` today writes only `inventory_location`. If we stop reading that field and `scan_components_cloudlab` doesn't migrate to `current_location`, cloud-labs will boot with `current_location is None` on every tag and everything downstream breaks. So Stage B absolutely must include "scan must write `current_location`" as a contract fix, and Stage C is blocked on it. Flagged as §7 item 2.
- **Real risk, 2:** today's `pick_component_cloudlab` probably consumes `inventory_location` as "where to pick from" internally. Switching it to read `current_location` means whoever implements the lab-automation fix must confirm the semantics are equivalent (post-scan, the two fields mean the same thing for us — the component's current pose). Should be fine, but worth naming.
- **Non-risk that earlier draft worried about:** "what if someone writes `comp.inventory_location = p` directly inside a `_cloudlab` function?" — under this revised rule, it doesn't matter. Cloud-labs doesn't read that field, so scribbles on it are invisible to us. That's the whole point of ignoring it.

### Q3. Scan semantics

Does `scan_components_cloudlab` update `current_location`, or only `inventory_location`? Today, cloud-labs patches this up manually (W1 in §2).

**Decision (2026-04-17):** Under the revised Q2, `scan_components_cloudlab` must write `current_location`. Whether it *also* writes `inventory_location` is outside cloud-labs' concern — we don't read that field. Once the scan writes `current_location`, cloud-labs no longer needs the W1 patch; `_initialize_state` reads `current_location` directly.

**Analysis / risks:**
- This becomes an acceptance criterion for Stage C: after Stage C, the W1 line disappears and the scan-reads pull `current_location`, not `inventory_location`, for every tag.
- If `lab_automation` doesn't ship this migration, Stage C is blocked — cloud-labs can't cut over to `current_location`-only reads if the scan never populates that field. Flagged as §7 item 2.
- Short-term coping: during the transition period between Stages A and C, cloud-labs can keep the `comp.current_location = comp.inventory_location` patch at `_initialize_state` as a compatibility shim, then delete it in Stage C once the scan is migrated.

### Q4. What is the "HTTP snapshot"? What is it used for?

Original wording was sloppy — "HTTP snapshot" isn't a thing that exists by that name anywhere in the repo. What I meant is: the JSON document produced by `GET /api/lab-state` (and equivalent endpoints) and consumed by the `set_lab_state` path — i.e. a **saved lab-state JSON**, the thing the "save state" / "load state" UI buttons write and read.

**Context (2026-04-17):** Load-state exists mostly because the top-camera scan at startup gives poor position estimates. A saved JSON, produced while the robot was actively placing parts, captures positions with robot precision instead of camera precision. So the usual workflow is: "do a good run → save → restart UI later → load, get accurate poses back without re-scanning".

That's why `set_lab_state` overwrites both `inventory_location` and `current_location` from the snapshot, and why `is_placed` is in the snapshot in the first place (Q1).

**Sub-question restated:** now that `z_lab` lives in `measurables.pose.z`, a saved snapshot will preserve the z. Do we want load-state to be allowed to restore a mid-HOVER state, or always reset to `IDLE` the way it does today?

**Decision (2026-04-17):** Keep the current behavior — load-state always drops `holding` to empty and sets `system_status = IDLE` (already done in `set_lab_state`, line 734-ish). A saved state that claims `HOLDING` is physically meaningless after a restart: the gripper may or may not be closed on boot, and `_reconcile_holding_on_boot` is the single source of truth for that.

**Analysis / risks:**
- This matches the existing mental model ("load = restore positions, not robot-internal state") and avoids a class of very bad bugs (load a stale `HOLDING` snapshot → robot thinks it's holding a part it isn't → next primitive tries to place thin air).
- Minor visual wart: a saved snapshot may carry a `z_lab = 40` on a `measurables.pose` for a part that was hovering. On load we drop `holding` but keep `measurables.pose.z = 40`, which after Stage A's forward-transform writes `z_robot ≈ 560` into `comp.current_location`. If the next action is `MOVE_COMPONENT`, that's fine — it overwrites z. If the next action is somehow a dry read before any move, the robot will believe a placed part is 40mm above the table. This is edge-y and probably not worth solving on its own; we can flag it as "saved states should be taken while idle, not mid-HOVER". Noted for the docs, not the roadmap.

### Q5. Homing events — who updates `inventory_location`?

If the robot returns a part to its storage rack, is there a dedicated `home_component_cloudlab`, or is it always done out-of-band via the catalog?

**Decision (2026-04-17):** Homing is a **macro** that composes existing atomic primitives — conceptually `PICK_COMPONENT` → `PLACE_FROM_HOVER` (or a sequence including `MOVE_COMPONENT`) targeting the storage slot pose. No dedicated `home_component_cloudlab` is introduced. Because every atomic primitive updates `current_location` on the `lab_automation` side (§7 item 1, 2, 3), homing falls out for free.

**Analysis / risks:**
- This is the right composition. Homing-as-macro fits the existing macro-vs-atomic split in `lab_primitives` (macros expand into sequences of atomic primitives).
- The macro needs to know *where* home is. Two sources today:
  1. For rack-owned parts, the storage slot `(i, j)` → lab xy is derived from `StorageRegion` geometry plus the `_stored_intent` file cloud-labs already maintains.
  2. For parts that don't live in the rack, "home" isn't well-defined and homing shouldn't be offered.
  The existing `PLACE_FROM_STORAGE` pipeline in reverse — `TAKE_TO_STORAGE` — is basically the same macro.
- Consequence for this roadmap: nothing. Because the atomic primitives each keep `current_location` coherent (Stage B), the homing macro inherits that behavior for free; we don't need new primitives or a `home_*_cloudlab`.
- Note: homing doesn't touch `inventory_location` either — under the revised Q2, cloud-labs doesn't care what that field is doing. If non-cloudlab code elsewhere in `lab_automation` cares about `inventory_location` for "home" semantics, that's their concern, outside this roadmap.

---

## 9. Roadmap

### Stage A — immediate consistency fix *(this repo only; do now)*

Scope: the minimum change that closes the "crash into the table on load-state after hover" bug **and** installs the rotation helper so the three cross-wall transforms (xy / z / rotation) have a consistent shape. No calibration work — rotation helper is identity for now.

**Work items:**
- A1. In `set_lab_state` (`backend/lab_communicator/real.py`, ~line 748), compute `z_robot` via `_z_lab_to_robot(tag_id, pose.get("z", 0.0))` instead of using `pose.get("z", 500.0)` directly. Handle the "tag not in catalog" case by falling back to the current `500.0` default (or better, `TABLE_Z0_ROBOT_MM` — which is the same thing).
- A2. **Introduce `lab_rotation_to_robot_yaw(theta_lab: float) -> float`** in `real.py`, body `return float(theta_lab)` (identity). Introduce the inverse `robot_yaw_to_lab_rotation(yaw_robot: float) -> float` alongside, same body. Both next to `lab_table_xy_to_robot_xy`. Add a docstring referencing §5 of this doc and explicitly stating "identity today; may become a calibrated affine when the lab measures a table/base offset".
- A3. In `set_lab_state` (~line 752), replace `yaw = float(pose.get("rotation"))` with `yaw = lab_rotation_to_robot_yaw(float(pose.get("rotation", 0.0)))`. Today this is a no-op at runtime; the point is to route the cross-wall rotation through the same single-point-of-truth as xy/z.
- A4. `move_component` (~line 1152): the inline `-rot` is **left unchanged** for now. Replacing it with `lab_rotation_to_robot_yaw(rot)` (identity) would flip the sign of the yaw actually dispatched and break physical placement, which we can't verify without the lab. Instead: added a pointed comment at the call site referencing this doc and stating "do NOT change this expression without a physical test". When someone next touches the robot, the test is simple — try a 10° rotation, see if it comes out at +10° or −10° in the lab frame, and either (a) fold the negation into `lab_rotation_to_robot_yaw` (and remove the `-` here), or (b) document why the two conventions must stay separate. The helper is still introduced in A2 so the plumbing is in place when that test happens.
- A5. In `hover_component`'s real path (in the same file, after the existing XY/Z forward-transform block), route the rotation through the helper for parity. Again a no-op today, but locks in the pattern.
- A6. **Deferred** — regression test for save-state → load-state round-trip. Adding one requires `lab_automation` to be importable in the test env (today it's flagged as unresolved by the linter; `RealLabCommunicator` can't be instantiated without it). Tracked here so we don't lose it; add once the test harness can stub or provide `lab_automation`.
- A7. Add a short comment above the load-state block pointing at `new_primitives.md` §10.5 and at this doc.

**Acceptance:**
- With `z_lab = 40` in a saved snapshot, after load-state `comp.inventory_location.z` ≈ `TABLE_Z0_ROBOT_MM + height_mm − GRASP_OFFSET_MM + 40`, not `40`.
- `backend/lab_communicator/real.py` contains `lab_rotation_to_robot_yaw` and `robot_yaw_to_lab_rotation` as module-level functions, and every cross-wall yaw write goes through the former (`rg` check).
- Existing tests still pass. Existing physical behavior is unchanged (helpers are identity), except where A4 resolves the pre-existing `set_lab_state` vs `move_component` disagreement — that's a behavior change, but it's picking one side of an already-inconsistent pair, not introducing new behavior.

**No lab_automation dependency.** This is entirely within `real.py`.

### Stage B — documentation / contract hand-off *(docs only — DONE 2026-04-17)*

Scope: lock in the plan with `lab_automation` maintainers so they can schedule the real work.

**Work items:**
- ✅ B1. Added "Contract fixes on existing `_cloudlab` functions" section (§5) to `labautomation_new_primitives.md`, capturing items 1–7 of §7 above with contract + acceptance criteria per item.
- ✅ B2. Per-primitive read/write matrix inlined as `labautomation_new_primitives.md` §5.4 (cloud-labs-view template; `lab_automation` fills in the "today's behavior" column on their side).
- ✅ B3. Q1–Q5 decisions from §8 inlined as `labautomation_new_primitives.md` §5.5 ("Decisions already locked") so the lab_automation maintainer doesn't have to re-read this doc to know which questions are settled. `§5.6 Not in scope` added explicitly (setter, invariant, rename — all dropped).
- ✅ B4. Cross-links added to `new_primitives.md` §13 history: one entry for the `fixing.md` audit and one entry for the Stage B handoff itself.

**Acceptance:**
- The `lab_automation` team can pick up `labautomation_new_primitives.md` and know exactly what API surface to add and what contract to enforce on existing primitives, without reading `real.py`. Stage 8 (new primitives) and Stage B (existing-primitive contract fixes) are bundled in the same doc because the same person is likely to land them.

**No code changes in this repo.** Stage C stays blocked on `lab_automation` landing the §5.3 contract items.

### Stage C — cloud-labs cleanup **DONE 2026-04-17**

Scope: stop touching `inventory_location` anywhere in `real.py`, narrow the load-state write to `current_location` + `is_placed` only, swap sentinel reads to `current_location`. End state: cloud-labs code contains zero references to `inventory_location` and writes nothing across the wall except `current_location` and `is_placed` (both only inside `set_lab_state`, plus the legacy `affirm_placed_at_current` manual-place flow — option (a) in C6 below).

**Work items:**
- [x] C1. **`_initialize_state`**: deleted the `comp.current_location = comp.inventory_location` compatibility shim. The pose extraction block (and its debug introspection prints) now reads `comp.current_location` directly — a single local alias `loc` replaces the old `inv`. After Stage B's scan migration, `scan_components_cloudlab` populates `current_location` itself, so the shim is dead code.
- [x] C2. **`set_lab_state`**: now writes only `comp.current_location = Pose(...)` and `comp.is_placed = ...` (no `inventory_location` write). All three cross-wall transforms (xy / z / rotation) continue to route through the dedicated helpers.
- [x] C3. **Sentinel reads**: `move_component` and `pick_component` both flipped from `if not comp.inventory_location:` to `if not comp.current_location:`. Kept as simple attribute checks rather than calling `comp.is_registered()` — they read identically today and stay useful if `is_registered` ever changes shape. The log messages were updated accordingly so operator output doesn't mention the legacy field.
- [x] C4. `rg "\.inventory_location" backend/lab_communicator/real.py` returns **zero matches**. Two prose-level mentions remain in comments (explaining *why* cloud-labs doesn't touch the field); those don't match the CI regex and are intentionally kept as documentation.
- [x] C5. The Stage-A comment block above `set_lab_state`'s cross-wall transform loop was rewritten to reflect the post-Stage-C state: "we write only `current_location` and `is_placed`; `inventory_location` is owned elsewhere in `lab_automation` and we no longer touch it from here." Cross-reference to `fixing.md` §6.1 / §9 and `labautomation_new_primitives.md` §5 kept.
- [x] C6. **CI regression test** landed as `StageCInvariantsTests` in `backend/tests/test_lab_primitives.py` (3 tests):
    - `test_no_inventory_location_attribute_access_in_real_py` — bans `\.inventory_location` in any method of `real.py`. Comment-only prose mentions are stripped before matching, so the two rationale comments are fine.
    - `test_current_location_writes_only_in_set_lab_state` — writes to `\.current_location` are allowed only inside `set_lab_state`. Regex guards against matching `==` comparisons.
    - `test_is_placed_writes_only_in_allowed_sites` — writes to `\.is_placed` are allowed only inside `set_lab_state` *and* `affirm_placed_at_current`. This is option (a) from the original Stage C write-up: the manual-place flow (the user telling cloud-labs "this part is already on the table, stop tracking it as stored") legitimately writes `is_placed = True` without going through a snapshot load, and forcing it through `set_lab_state` would require a pointless synthetic snapshot. The allow-list is two names, still greppable.

    A one-off sanity probe (now deleted) confirmed the test class fails all three assertions when a synthetic regressing method is injected into `real.py`'s source.

**Acceptance:**
- [x] `rg "\.inventory_location" backend/lab_communicator/real.py` returns zero matches (attribute access; prose mentions in comments are allowed).
- [x] Writes to `\.current_location` appear only inside `set_lab_state`; writes to `\.is_placed` appear only inside `set_lab_state` + `affirm_placed_at_current`.
- [x] Mock mode still loads cleanly (smoke-tested via existing test suite — 9/9 pass).
- [x] `labautomation_new_primitives.md` §5 Stage B contract has shipped upstream; the audit (§5.4 matrix) confirms `pick_component_cloudlab` and `scan_components_cloudlab` both write `current_location`. A non-blocking legacy `inventory_location` fallback inside `lab_automation.pick_component_cloudlab` is noted but doesn't affect cloud-labs (we always populate `current_location` on scan).

**Blocked on (historical):** items 1, 2, 3 of §7 landing in `lab_automation`. All unblocked as of 2026-04-17 — see the Stage B changelog entry and the `experiment_manager.py` audit documented in the 2026-04-17 history entry of `new_primitives.md` §13.

---

## 10. Summary

- The wall between cloud-labs and `lab_automation` leaks in exactly 3 writes and 3 reads, all in `real.py`, all traceable to `_initialize_state` + `set_lab_state`. Plus two frame-consistency leaks (z, rotation) in the same file.
- The z leak became a physical safety bug once the z convention landed (snapshot-load after hover would crash into the table); the rotation leak is latent — identity today but already inconsistent between `set_lab_state` and `move_component`. **Both fixed in Stage A, together.** Z is always forward-transformed (never stripped), because save/load must preserve user-tuned hover heights — the "teach and repeat" property of the digital twin.
- The deeper cause — overlapping semantics for `inventory_location` vs `current_location`, and `pick_component_cloudlab` not updating `current_location` — is a `lab_automation`-side contract fix, tracked in Stage B. **Chosen resolution: cloud-labs ignores `inventory_location` entirely.** `current_location` becomes the canonical "where is this part now" field; every `_cloudlab` primitive maintains it; cloud-labs never reads or writes `inventory_location` from any `_cloudlab` code path.
- Stage C is the sweep that removes every `inventory_location` reference from `real.py` and narrows load-state writes to `current_location` + `is_placed` only. Sentinel reads switch to checking `current_location`.
- We commit to never copying a coordinate (xy / z / rotation) between frames without going through the documented transform helper.

---

## Changelog

- **2026-04-17:** Document created. Stages A–C not yet started. No code touched.
- **2026-04-17 (decisions):** §8 open questions resolved. Q1: cloud-labs keeps pushing `is_placed` (load-state = ground truth). Q2: adopt a `Component.set_location(...)` setter that enforces `inventory_location == current_location` inside every `_cloudlab` function; cloud-labs reads `current_location` and writes only through the setter. Q3: resolved by Q2 once `scan_components_cloudlab` migrates. Q4: "HTTP snapshot" clarified as "saved lab-state JSON"; load-state keeps resetting `holding` to empty. Q5: homing is a macro of atomic primitives, no new `home_*_cloudlab` needed. §6.1, §7, and §9 Stage C updated to match: free-function `sync_component_from_observed_pose` replaced by method `comp.set_location(...)`, plus new contract item (#2) requiring all existing `_cloudlab` primitives to migrate to the setter.
- **2026-04-17 (rotation parity):** Raised the rotation / yaw cross-wall transform to first-class status. §3 extended with §3.1 documenting the latent `theta_lab ≠ yaw_robot` bug (plus the pre-existing `set_lab_state` / `move_component` disagreement at lines 752 and 1152). §5 rewritten as a full coordinate convention covering all three axes. §6.1 lists the three transform helpers cloud-labs owns. §9 Stage A expanded from 3 to 7 work items: introduces `lab_rotation_to_robot_yaw` (and inverse) as identity today, routes `set_lab_state`, `move_component`, and `hover_component` through it, and adds a regression test. Stage C's `set_lab_state` rewrite (C2) updated to use the new helper. No calibration work — identity-today is the contract.
- **2026-04-17 (Stage A implemented):** Landed in `backend/lab_communicator/real.py`:
  - New module-level helpers `lab_rotation_to_robot_yaw(theta_lab)` and `robot_yaw_to_lab_rotation(yaw_robot)` — both identity today, with a multi-line comment documenting the known convention disagreement with `move_component`'s inline `-rot`.
  - `set_lab_state` (~line 780ff): z now forward-transformed via `self._z_lab_to_robot(tag_id, z_lab)` (default `z_lab = 0.0` for "on the table" instead of the old magic `500.0`); rotation now routed through `lab_rotation_to_robot_yaw(theta_lab)`. All three cross-wall axes (xy, z, rotation) go through their dedicated helper. `inventory_location` + `current_location` + `is_placed` writes all preserved for Stage A (only Stage C removes the `inventory_location` write).
  - `move_component` (~line 1199ff): `angle=[-180, 0, -rot]` left unchanged. Added a pointed comment above the dispatch call explaining it's a deferred reconciliation item (fixing.md §3.1 / A4) and warning "do NOT change this expression without a physical test".
  - `hover_component` real path (~line 2047): rotation routed through `lab_rotation_to_robot_yaw(trot)` for parity with `set_lab_state` (no runtime change today).
  - A6 (regression test) marked deferred in the roadmap — requires `lab_automation` importable in the test env.
  - No linter regressions. Stages B and C remain untouched.
- **2026-04-17 (simpler Q2 + explicit z rationale):** Two further refinements, both simplifications:
  - **Q2 resolution revised.** Dropped the `Component.set_location(...)` setter + `inv == current` invariant. New rule: **cloud-labs ignores `inventory_location` entirely** inside `_cloudlab` code paths — never read, never written. `current_location` is the single canonical field. Strictly simpler, no invariant to enforce, no runtime check. Propagated through §6.1 (removed setter, narrowed cloud-labs' write surface to `current_location` + `is_placed` inside `set_lab_state` only), §7 (dropped items 1, 2, 8 of the old list; rewrote as 7 items organized around "`current_location` is the canonical field"), §8 Q2 and Q3 (new rationale), §9 Stage C (write directly to `current_location`, drop all `inventory_location` references, updated CI-grep rules to forbid `inventory_location` and restrict `current_location` writes to `set_lab_state`), and §10.
  - **Z fix rationale strengthened.** §3 no longer lists "strip z" as an alternative to the forward-transform — the teach-and-repeat property of save/load makes stripping z unacceptable. Added the "user spends an hour tuning a HOVER height, loads state tomorrow" motivation.
- **2026-04-17 (Stage B landed):** Stage B is docs-only and now complete. Contract hand-off delivered as `labautomation_new_primitives.md` §5 ("Contract fixes on existing `_cloudlab` functions"). The new section covers: a one-sentence summary (§5.1 — `current_location` canonical, `inventory_location` ignored by cloud-labs), why the fix is needed (§5.2 — the two concrete symptoms, `pick_component_cloudlab` not updating `current_location` and `scan_components_cloudlab` writing only `inventory_location`), seven contract items with acceptance criteria (§5.3), a cloud-labs-view per-primitive read/write matrix for `lab_automation` to fill in (§5.4), Q1–Q5 decision recap so they stay locked (§5.5), and an explicit "Not in scope" block (§5.6 — no setter, no invariant, no rename). Cross-links added in `new_primitives.md` §13 history: one entry describing the broader `fixing.md` audit and one entry describing the Stage B handoff. Top-of-doc framing of `labautomation_new_primitives.md` updated so the maintainer sees immediately that Stage 8 + Stage B are bundled. Stage C unchanged and still blocked on `lab_automation` landing §5.3 items 1–3.
- **2026-04-17 (Stage C landed):** Stage C implemented in `backend/lab_communicator/real.py` and guarded by a new `StageCInvariantsTests` class in `backend/tests/test_lab_primitives.py`. Changes:
  - `_initialize_state` (~line 640ff): dropped the `comp.current_location = comp.inventory_location` compatibility shim. The whole pose-extraction block now reads `comp.current_location` directly; the debug introspection prints renamed from `inventory_location` to `current_location` for operator-log clarity.
  - `set_lab_state` (~line 787ff): removed the `comp.inventory_location = p` write. Only `comp.current_location = Pose(...)` and `comp.is_placed = is_on_table(entry)` remain — the two canonical cross-wall sync writes. Comment block above the loop rewritten to point to §6.1 / §9 / `labautomation_new_primitives.md` §5 as the ownership rationale instead of the old "Stage C will drop this" forward reference.
  - `move_component` (~line 1203) and `pick_component` (~line 1958) sentinels: flipped from `if not comp.inventory_location:` to `if not comp.current_location:`. Log messages updated so operator output matches.
  - `rg "\.inventory_location" backend/lab_communicator/real.py` returns **zero matches**. The two remaining prose-level mentions of `inventory_location` live inside code comments explaining *why* cloud-labs stays away from the field; those are intentional documentation and don't match the CI regex.
  - CI lint: 3 new tests in `tests.test_lab_primitives.StageCInvariantsTests` parse `real.py` into method blocks (regex heuristic, no `ast` so the test survives without `lab_automation` importable) and fail on: any `.inventory_location` attribute access in any method; `.current_location =` writes outside `set_lab_state`; `.is_placed =` writes outside `{set_lab_state, affirm_placed_at_current}`. The `affirm_placed_at_current` exemption follows Stage C6's option (a) — that primitive is the manual-place flow and legitimately writes `is_placed = True` without going through a snapshot load. A throwaway regression probe (since deleted) confirmed the test trips all three assertions when a synthetic regressing method is injected.
  - All 9 tests in `test_lab_primitives` still pass. No functional change to hover/pick/place/move flows — Stage C is purely a cleanup + a lint to prevent regression.
  - `new_primitives.md` §13 history gets a matching "Stage C landed" entry cross-linking back here.
