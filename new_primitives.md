# New primitives: `PICK_COMPONENT`, `HOVER`, `PLACE_FROM_HOVER`, and `SCAN_ROTATE_IN_PLACE`

Design note for **cloud-labs** (this repository): **atomic** motion primitives that extend—but do not replace—the **`MOVE_COMPONENT`** pick-and-place path. Today, **`MOVE_COMPONENT`** moves in the table plane with implicit “place on breadboard” semantics (fixed approach / table **z** in **`lab_automation`**). The **holding** workflow is split so that **grasp** and **in-air motion** are never conflated.

This document is the specification for implementation. It sits beside **`primitives.md`**, **`model.md`**, and **`backend/lab_model/README.md`**.

---

## 1. Why these are separate from `MOVE_COMPONENT`

| Concept | `MOVE_COMPONENT` (today) | `PICK_COMPONENT` | `HOVER` | `PLACE_FROM_HOVER` | `SCAN_ROTATE_IN_PLACE` |
|--------|---------------------------|------------------|---------|-------------------|-------------------------|
| Primary intent | Move to **(x, y, rotation)** and **place** on the table (implicit table **z**) | Approach, **close gripper**, retract to a **safe default Z** — establishes **HOLDING** | Move **already held** part to **(x, y, rotation, z)** in the air — **no grasp** | Release held part onto the table at **(x, y, rotation)** — ends **HOLDING** | **Sweep θ** from **θ_min → θ_max** at **constant rate** in place (same **x, y, z**) |
| Gripper | Places / moves placed parts | **Closes** on the part | Already closed | Opens after place | Closed (held) or on table — product rule for table case |
| Session | Single command → **BUSY** → **IDLE** | **BUSY** → **HOLDING** | **BUSY** → **HOLDING** | **BUSY** → **IDLE** | **BUSY** / **`SCANNING`** → prior session restored |

**Critical rule:** **`HOVER` must never include a grasp.** If **`HOVER` were defined to “pick and move,”** you would eventually ask for a **height change** on an **already held** part and the stack might try to run **grasp-on-empty-air** logic. **`PICK_COMPONENT`** is the **only** primitive that performs **close gripper + establish holding** (plus retract to safe clearance). **`HOVER`** only repositions while **`holding.tag_id`** is already set.

---

## 2. Relationship to the existing `SCAN` stub

The codebase currently defines a **`SCAN`** action in **`lab_primitives`** with **`handler: None`**, a **no-op** in **`_invoke_atomic`**, and **no background work** in **`schedule_validated_command`**. Do **not** overload it.

- Add **`PICK_COMPONENT`**, **`HOVER`**, **`PLACE_FROM_HOVER`**, and **`SCAN_ROTATE_IN_PLACE`** as new `PrimitiveId` / `action` values with explicit Pydantic models.
- **Deprecate** the legacy **`SCAN`** stub (remove or alias with a warning) when the new primitives land.

---

## 3. Primitive: `PICK_COMPONENT`

### 3.1 Purpose

**Atomic grasp sequence:** arm moves to a pick pose above/near the component on the table, **gripper closes**, arm retracts to a **default safe clearance Z** (lab-defined). On success, **`system_status` → `HOLDING`** with **`holding.tag_id === target_id`**. This is **not** a table placement; it is the **entry** into the holding session.

### 3.2 Suggested HTTP shape

Parameters are intentionally minimal at first; extras can use **`extra="allow"`** if **`lab_automation`** needs vendor fields.

```json
{
  "action": "PICK_COMPONENT",
  "target_id": "tag_22",
  "parameters": {}
}
```

Optional later: **`approach_z`**, **`safe_z`**, or **`pre_pick_xy`** — only if the real API cannot infer them from catalog + vision.

### 3.3 Preconditions

- **`system_status`** must be **`IDLE`** (nothing held). If already **`HOLDING`**, reject (**409** / **400**) — use **`HOVER`** or **`PLACE_FROM_HOVER`** instead.
- Target component must be **on the table** per policy (e.g. **`PRESENCE_BREADBOARD`**, not **STORAGE**), unless a future workflow allows pick-from-storage (out of scope here).

### 3.4 Postconditions

- **`HOLDING`** with nominal pose including **x, y, rotation, z** at the **safe hover** after retract.
- **`tunables` / `measurables`:** Update poses for the held tag; **`placement.mode`** may be **`PICK`** or **`HOVER`**-family as you standardize.

---

## 4. Primitive: `HOVER`

### 4.1 Purpose

Reposition an **already held** component to **(x, y, rotation, z)** in **lab frame**, **without** releasing. **Does not close the gripper** and **must not** execute pick macros.

### 4.2 Suggested HTTP shape

```json
{
  "action": "HOVER",
  "target_id": "tag_22",
  "parameters": {
    "target_x": 120.0,
    "target_y": -40.0,
    "rotation": 15.0,
    "z": 85.0
  }
}
```

### 4.3 Preconditions

- **`system_status`** must be **`HOLDING`** and **`holding.tag_id === target_id`**. If **`IDLE`**, reject — user must run **`PICK_COMPONENT`** first (not **`HOVER`**).

### 4.4 Postconditions

- Remain **`HOLDING`**; update **`nominal_pose`** / **`measurables.pose`** including **z**.

---

## 5. Primitive: `PLACE_FROM_HOVER`

### 5.1 Purpose

**Definitive exit from holding:** place the held part on the breadboard at **(x, y, rotation)** with implicit table **z** / place pipeline — same *semantic outcome* as a successful **`MOVE_COMPONENT`** for that tag, but **only valid while `HOLDING`**.

**Design decision:** **`PLACE_FROM_HOVER`** is a **separate** primitive from **`MOVE_COMPONENT`**. We do **not** overload **`MOVE_COMPONENT`** when **`HOLDING`** is true. That keeps validation, recipes, and docs unambiguous.

### 5.2 Suggested HTTP shape

Uses the same **`PoseTargetParameters`** shape as **`MOVE_COMPONENT`** (**`target_x`**, **`target_y`**, **`rotation`**).

```json
{
  "action": "PLACE_FROM_HOVER",
  "target_id": "tag_22",
  "parameters": {
    "target_x": 10.0,
    "target_y": 20.0,
    "rotation": 0.0
  }
}
```

### 5.3 Preconditions

- **`HOLDING`** and **`holding.tag_id === target_id`**.

### 5.4 Postconditions

- **`holding`** cleared; **`system_status` → `IDLE`** (unless another subsystem sets **BUSY** briefly — follow existing move semantics).

---

## 6. Session state: `HOLDING` and restart safety (power outage / crash)

### 6.1 Why `HOLDING` exists

**`system_status`** today includes **`IDLE`**, **`BUSY`**, **`OPTIMIZING`**, etc. **`HOLDING`** means: **no trajectory is running**, but the **gripper is closed on a part** (or the software believes it is). The UI and **409** logic must forbid dangerous commands (e.g. **`MOVE_COMPONENT`** on a **different** tag) while this is true.

### 6.2 Recommended lab-state fields

```json
{
  "system_status": "HOLDING",
  "holding": {
    "tag_id": "tag_22",
    "nominal_pose": { "x": 120.0, "y": -40.0, "rotation": 15.0, "z": 85.0 }
  }
}
```

Use **`system_status: "HOLDING"`** whenever **`holding`** is semantically active — easier than **`IDLE` + `holding`** for badges and gating.

**Rejected while `HOLDING` (examples):** **`MOVE_COMPONENT`**, **`PICK_COMPONENT`**, **`STORE_COMPONENT`**, **`PLACE_FROM_STORAGE`** on **other** tags — must be **409** / **400** with a clear message. **`HOVER`** / **`PLACE_FROM_HOVER`** / **`SCAN_ROTATE_IN_PLACE`** only for the **held** tag (subject to scan rules in §8).

### 6.3 Catch: persisted JSON vs physical reality (“power outage” mismatch)

**Problem:** If **FastAPI** crashes or the **server restarts** while the **physical** robot still has **glass in the gripper**, **`mock_lab_state.json`** (or an in-memory default) may say **`IDLE`** with **no `holding`**. A user could then issue **`MOVE_COMPONENT`** for **another** tag; the arm might attempt a **second pick** and **collide** two components.

**Fix — mandatory for `RealLabCommunicator` startup:**

1. After the robot / **`OpticalExperiment`** is reachable, **poll hardware gripper state** (e.g. **`gripper_state = robot.get_gripper_status()`** or the **`lab_automation`** equivalent — exact API **TBD**).
2. If the gripper reports **closed** (holding *something*), the software **must not** initialize as a carefree **`IDLE`** with empty **`holding`**.
3. Set **`system_status`** to **`HOLDING`** (or a dedicated **`HOLDING_UNCONFIRMED`** — see below) **regardless** of what was in the last JSON snapshot.
4. **Tag identity:** Vision / last-known state may be **stale**. Recommended fields:

   ```json
   "holding": {
     "tag_id": null,
     "nominal_pose": { ... best effort from last scan ... },
     "requires_operator_confirm": true
   }
   ```

   Or **`system_status: "HOLDING_UNCONFIRMED"`** until the operator confirms which **`tag_id`** is in the hand.

5. **UI lock:** Until **`requires_operator_confirm`** is cleared (dedicated **HTTP** action or modal that POSTs **`CONFIRM_HOLDING_TAG`** — implementation detail), the client should **disable** any command that could **pick** or **move another part** (`**MOVE_COMPONENT**` on other tags, **`PICK_COMPONENT`**, etc.). Show a **blocking** banner: *“Gripper closed — confirm which component is held before continuing.”*

6. **Mock mode:** No physical gripper; **`mock_lab_state.json`** remains authoritative **unless** you add a dev flag to simulate **“restart with closed gripper”** for UI testing.

**Principle:** **Trust the robot sensors on boot more than persisted JSON** for **`HOLDING`**.

### 6.4 Ghost / solid (frontend)

- **`tunables.nominal_pose`** should carry **x, y, rotation, z** while holding.
- If **`tag_id`** is unknown (**`HOLDING_UNCONFIRMED`**), show **no** or **dimmed** ghost for specific parts until confirmed.

---

## 7. Primitive: `SCAN_ROTATE_IN_PLACE`

### 7.1 Purpose

Rotate through **[θ_min, θ_max]** at **constant angular speed** so camera + laser logic can detect alignment. **Not** **`OPTIMIZE` / NEWTON`**; a **deterministic sweep**.

### 7.2 Suggested HTTP shape

```json
{
  "action": "SCAN_ROTATE_IN_PLACE",
  "target_id": "tag_22",
  "parameters": {
    "theta_min": 10.0,
    "theta_max": 170.0,
    "speed_deg_per_s": 2.0,
    "axis": "z"
  }
}
```

### 7.3 Camera / exposure / frame sync (deferred)

Later we may need **exposure**, **frame sync** with the sweep, or **debug image output** (similar to **`OPTIMIZE`**). That is **explicitly out of scope for the first implementation** — no requirement to add those parameters or pipelines yet. The first version can advance angle in **`lab_automation`** / mock **without** synchronized vision.

### 7.4 Preconditions & postconditions

- **Allowed in two states**, same user intent / same HTTP primitive:
  - **`HOLDING`** with **`holding.tag_id === target_id`** → in-air sweep. System stays **`HOLDING`** on completion.
  - **`IDLE`** with the target **on the breadboard** → on-table sweep via a **transient arm grip** (arm closes on the part, rotates the wrist through the sweep, then releases and retracts; XY stays locked, part stays on the table at the new rotation). `holding` is **not** populated during the sweep — the grip is an atomic implementation detail, not a user-visible holding session. System returns to **`IDLE`** on completion. Works for components regardless of whether they have a motorized mount, since the rotation comes from the arm's wrist (which has far more range than typical per-component motors).
- Rejected when in **`HOLDING`** with a **different** held tag (must place/hover first), or when the target is off-table / stored in the `IDLE` branch.
- The two paths dispatch to **different `lab_automation` functions** (`scan_rotate_held_cloudlab` vs. `scan_rotate_placed_cloudlab`) — see **`labautomation_new_primitives.md`** §2.4 and the dispatch in `RealLabCommunicator.scan_rotate_in_place`. Both mock and real implement the branching; the UI shows one button and infers the branch from current state.
- During sweep: **`BUSY`** (held-mode keeps rendering the ghost pose so the UI can follow the rotation); after: return to **`HOLDING`** or **`IDLE`** per §10.

---

## 8. Changes by layer (implementation checklist)

### 8.1 `lab_primitives`

| Item | Action |
|------|--------|
| **`PrimitiveId`** | Add **`PICK_COMPONENT`**, **`HOVER`**, **`PLACE_FROM_HOVER`**, **`SCAN_ROTATE_IN_PLACE`**. |
| **`schemas.py`** | Typed **`parameters`** for each. |
| **`registry.py`** | **`ATOMIC`**, **`handler`** → **`LabCommunicator`** methods. |
| **`dispatch.py`** | **`await lab.pick_component`**, **`hover_component`**, **`place_from_hover`**, **`scan_rotate_in_place`**. |
| **Optional** | **`CONFIRM_HOLDING_TAG`** as a small command or separate route for §6.3. |

### 8.2 `LabCommunicator` (`base.py`)

Abstract methods (names illustrative):

- **`async def pick_component(self, target_id: str, params: Dict[str, Any])`**
- **`async def hover_component(self, target_id: str, params: Dict[str, Any])`**
- **`async def place_from_hover(self, target_id: str, params: Dict[str, Any])`**
- **`async def scan_rotate_in_place(self, target_id: str, params: Dict[str, Any])`**
- Optional: **`confirm_holding_tag(self, tag_id: str)`** for operator resolution after restart.

### 8.3 `RealLabCommunicator` (`real.py`)

1. **Startup (§6.3):** After robot init, **gripper poll** → set **`HOLDING`** / **`HOLDING_UNCONFIRMED`**; **never** trust JSON alone.
2. **Placeholders** until **`lab_automation`** implements motion: log **`[REAL LAB] PLACEHOLDER: …`**, optional flag-driven fake state for UI wiring.
3. **Do not** route **`HOVER`** through **`place_component_wo_home_specific_xy_cloudlab`** — that is table place semantics.

### 8.4 `MockLabCommunicator` (`mock.py`)

- **`PICK_COMPONENT`:** Transition **`IDLE` → BUSY → HOLDING`** with posed **`holding`** (file-backed state).
- **`HOVER`:** Only if mock is **`HOLDING`** same tag; update poses.
- **`PLACE_FROM_HOVER`:** **`HOLDING → IDLE`**, table pose + noise.
- **`SCAN_ROTATE_IN_PLACE`:** Time-based simulation; no camera sync required for v1.
- Optional: **simulate restart mismatch** for QA (e.g. env **`MOCK_GRIPPER_CLOSED_ON_BOOT=1`**).

---

## 9. Frontend (cloud-labs)

- **Badge:** **`HOLDING`** / **`HOLDING_UNCONFIRMED`**.
- **Blocking modal / banner** when **`requires_operator_confirm`** or unconfirmed tag (§6.3); **lock** pick and cross-part moves until resolved.
- **Context panel:** **Pick** (when eligible) → **`PICK_COMPONENT`**; while holding → **Hover** form, **Place** → **`PLACE_FROM_HOVER`**; **Scan** → **`SCAN_ROTATE_IN_PLACE`**.
- **Command Console:** Shorthand for all four + confirm flow if added.

---

## 10. State machine summary

```text
IDLE --(PICK_COMPONENT)--> BUSY --(success)--> HOLDING

HOLDING --(HOVER)--> BUSY --(success)--> HOLDING

HOLDING --(PLACE_FROM_HOVER)--> BUSY --(success)--> IDLE

IDLE    --(SCAN_ROTATE_IN_PLACE, placed-mode)--> BUSY|SCANNING --(success)--> IDLE
HOLDING --(SCAN_ROTATE_IN_PLACE, held-mode)  --> BUSY|SCANNING --(success)--> HOLDING
```

**Note:** **`HOVER`** is **not** on the path from **`IDLE`** — only **`PICK_COMPONENT`** establishes **`HOLDING`**. **`SCAN_ROTATE_IN_PLACE`** is the one primitive allowed from both **`IDLE`** (placed-mode) and **`HOLDING`** (held-mode); the two branches dispatch to different `lab_automation` functions (see §7.4 and `labautomation_new_primitives.md` §2.4), but the UI / HTTP shape is identical.

---

## 10.5 Z / coordinate convention (`z_lab` vs. `z_robot`)

Cloud-labs (this repo), the UI, the HTTP API, `lab_model`, and the mock communicator **all** speak a single vertical coordinate we call **`z_lab`**:

- **`z_lab` = height of the component's *base* above the breadboard surface, in millimeters.**
- `z_lab = 0` ⇔ part is resting on the breadboard (post-`PLACE_FROM_HOVER`).
- `z_lab = DEFAULT_HOVER_Z_MM` (40) ⇔ default safe clearance after `PICK_COMPONENT`.

All of these fields are `z_lab`: `tunables.nominal_pose.z`, `measurables.pose.z`, `holding.nominal_pose.z`, the `z` in `HOVER` / `PLACE_FROM_HOVER` payloads, every z input in the UI.

The robot controller speaks **`z_robot`** — a raw robot-frame z (flange / gripper-tip, depending on the setup). `RealLabCommunicator` is the **only** component that sees both frames; it converts at every boundary with `lab_automation` (mirrors the existing XY `lab_table_xy_to_robot_xy` transform).

**Forward transform** (outgoing cloud-labs → `lab_automation`):

```
z_robot = TABLE_Z0_ROBOT_MM + c.height_mm − GRASP_OFFSET_MM + z_lab
```

**Inverse transform** (incoming `lab_automation` → cloud-labs):

```
z_lab = z_robot − TABLE_Z0_ROBOT_MM − c.height_mm + GRASP_OFFSET_MM
```

Three inputs, each owned by exactly one thing so they don't drift out of sync:

| Input | Owner | Meaning | Override |
|---|---|---|---|
| `TABLE_Z0_ROBOT_MM` | Per-setup calibration | Robot-frame z reading when the **empty gripper fingers** are just touching the breadboard (one-time touch-off) | Env `TABLE_Z0_ROBOT_MM` in `backend/lab_communicator/real.py` (default `500.0`) |
| `GRASP_OFFSET_MM` | Gripper design constant | Distance from the **top of the component's housing** DOWN to the point where the gripper fingers close. `0` ⇒ grips at the very top; `10` ⇒ grips 10 mm below the top | Env `GRASP_OFFSET_MM` (default `0.0`) |
| `c.height_mm` | Per-component catalog | Total physical height of the part, base-to-top, in mm | New top-level field in `schemas/component_catalog.real.json` (distinct from `size.height`, which is the 2D UI footprint). Fallback `DEFAULT_COMPONENT_HEIGHT_MM` (60 mm) with a warning if missing |

Plus one cloud-labs-side safety knob: **`MAX_SAFE_HOVER_Z_LAB_MM`** (default 200 mm). `RealLabCommunicator.hover_component` rejects any payload with `z_lab` outside `[0, MAX_SAFE_HOVER_Z_LAB_MM]` before forward-transforming, catching runaway robot-frame values that leaked into an HTTP payload.

### How `PICK_COMPONENT` populates `z_lab`

Cloud-labs does **not** send a z to `pick_component_cloudlab` — the `lab_manager` chooses its own `safe_z_retract_robot`. To stay consistent with the round-trip invariant, after pick succeeds `RealLabCommunicator` asks `lab_automation` for the intent height via a math-only helper:

```python
lab_automation.compute_intent_hover_z_lab(component) -> float   # returns z_lab
```

This is a **pure function** with no hardware I/O: `lab_manager` applies the inverse transform to its own `safe_z_retract_robot`. The result is written straight into `tunables.nominal_pose.z` and `holding.nominal_pose.z`. If `lab_automation` does not yet expose the helper, `RealLabCommunicator` falls back to `DEFAULT_HOVER_Z_MM` (40 mm) and logs a warning.

### Round-trip invariant

If the user picks a part and then clicks **Hover** without editing the z field:

1. Post-pick: `tunables.nominal_pose.z = z_lab_intent` (from `compute_intent_hover_z_lab`).
2. UI sends `HOVER` with that same `z_lab`.
3. `RealLabCommunicator._z_lab_to_robot` forward-transforms → `z_robot` = exactly what the robot is already at.
4. Robot produces **no vertical motion**.

This is robust to calibration error in `TABLE_Z0_ROBOT_MM` because the same constant appears on both sides of the transform. Wrong calibration only makes the displayed `z_lab` physically inaccurate (UI says 40 but the part is actually 35 above the table) — it does **not** cause spurious motion on a no-op HOVER.

### Tunables vs. measurables for the new primitives

Convention: every primitive that commands robot motion writes the commanded pose into **both** `tunables.nominal_pose` (intent) **and** `measurables.pose` (current estimated position). This mirrors `MOVE_COMPONENT`'s long-standing behavior — robot placement accuracy is higher than what the top camera can resolve (especially through the gripper during a HOLDING session), so the commanded pose is the best available estimate of where the part actually is. A downstream vision pipeline can always overwrite `measurables.pose` later when it has a more precise read.

| Primitive | Writes `tunables.nominal_pose` | Writes `measurables.pose` |
|---|---|---|
| `MOVE_COMPONENT` | ✅ (x/y/rotation) | ✅ (x/y/rotation) — existing behavior |
| `PICK_COMPONENT` | ✅ (x/y/rotation carried over from previous measurables, z from `_intent_hover_z_lab`) | ✅ (same x/y/rotation/z as tunables) |
| `HOVER` | ✅ (x/y/rotation/z from the user-supplied z_lab) | ✅ (same x/y/rotation/z) |
| `PLACE_FROM_HOVER` | ✅ (x/y/rotation; z stripped, since `z_lab = 0` is implicit on placed parts) | ✅ (same x/y/rotation; z omitted) |
| `SCAN_ROTATE_IN_PLACE` (held) | ✅ (`rotation` updated; XY/Z locked) | ✅ (`rotation` updated on the held pose) |
| `SCAN_ROTATE_IN_PLACE` (placed) | ✅ (`rotation` updated) | ✅ (`rotation` updated) |

### Where this is implemented

- **Constants + transforms:** top of `backend/lab_communicator/real.py` (module-level comment; helpers `_component_height_mm`, `_z_lab_to_robot`, `_z_robot_to_lab`, `_intent_hover_z_lab`).
- **PICK path:** `RealLabCommunicator.pick_component` — no z is sent to `lab_automation`; `_intent_hover_z_lab(target_id)` populates `tunables.nominal_pose.z`, `measurables.pose.z`, and `holding.nominal_pose.z` on success.
- **HOVER path:** `RealLabCommunicator.hover_component` — bounds-checks `z_lab`, forward-transforms to `z_robot`, passes it to `hover_component_cloudlab`, then commits intent to `tunables`, `measurables.pose`, and `holding` (same convention as `MOVE_COMPONENT`).
- **Mock reference:** `MockLabCommunicator` speaks `z_lab` natively — no transform, no catalog lookup; `DEFAULT_HOVER_Z_MM` is authoritative.
- **UI labels:** context panel z input reads "Z CLEARANCE (mm above table)" with a tooltip explaining z_lab = 0 semantics.
- **Lab-automation contract:** see `labautomation_new_primitives.md` §4 for what `lab_automation` must expose (`compute_intent_hover_z_lab`, robot-frame expectations for `hover_component_cloudlab`, etc.).

---

## 11. Remaining open questions (for `lab_automation`)

1. ~~**Z convention:** Table surface **z = 0** vs robot base — must match **`RealLabCommunicator`** and **`PICK`** safe clearance.~~ **Resolved, see §10.5.** Cloud-labs speaks `z_lab` (height of component base above table); `RealLabCommunicator` transforms to/from `z_robot` using `TABLE_Z0_ROBOT_MM`, per-component `height_mm`, and `GRASP_OFFSET_MM`.
2. **Safety:** E-stop mid-air — who clears **`holding`** and how does the UI recover (operator confirm only)?
3. **`SCAN_ROTATE_IN_PLACE`:** Allowed only when **`HOLDING`**, or also when part is **fixed on table**? (Deferred camera sync remains **out of scope** until a later milestone.)

---

## 12. Implementation roadmap

A staged plan. Each stage is reviewable on its own; merging one does not force the next. Stages below the **mock-complete** line can be wired into the UI immediately without hardware.

> **Status (2026-04-17): Stages 0–7 are complete — the full flow (pick → hover → scan-rotate → place, plus the boot-time gripper-closed recovery) is exercised end-to-end in mock mode through `/api/command` and the Cloud-Labs UI, and the real-lab communicator now exposes safe placeholder methods (no robot motion; state mutation gated by `HOVER_PLACEHOLDER_STATE=1`). Recipes accept short aliases (`PICK`, `PLACE_HOVER`, `SCAN_ROTATE`, `CONFIRM_HOLDING`) and golden snapshots now capture `z` + `holding` with full backwards compatibility. Stage 8 (real motion on the robot) is handed off to `lab_automation` — see `labautomation_new_primitives.md`.**

### Stage 0 — Agreement & naming (doc only) — ✅ done

- [x] Lock primitive names: **`PICK_COMPONENT`**, **`HOVER`**, **`PLACE_FROM_HOVER`**, **`SCAN_ROTATE_IN_PLACE`** (+ **`CONFIRM_HOLDING_TAG`**).
- [x] Canonical unconfirmed-holding representation chosen: **`system_status == "HOLDING"` + `holding.requires_operator_confirm == true`** (flag variant; see §6.3).
- [x] **`SCAN_ROTATE_IN_PLACE`** is **`HOLDING`-only** in v1 (validated by `_enforce_holding_rules` in `backend/main.py`).
- [x] Legacy **`SCAN`** kept as no-op alias with a deprecation warning logged in `lab_primitives/dispatch.py`.

### Stage 1 — Backend domain model (`lab_model`) — ✅ done

- [x] **`lab_model/component_model.py`:** **`z`** is allowed in **`nominal_pose`** / **`measurables.pose`** (optional, omitted on placed parts).
- [x] New placement mode constants: **`PLACEMENT_MODE_HOVER`**, **`PLACEMENT_MODE_PICK`**.
- [x] New **`lab_model/holding.py`** module: single source of truth for `holding` field, `SYSTEM_STATUS_HOLDING`, `DEFAULT_HOVER_Z_MM`, and helpers (`empty_holding`, `get_holding`, `is_holding`, `held_tag`, `requires_operator_confirm`, `set_holding`, `clear_holding`, `confirm_holding_tag`). Re-exported from `lab_model.__init__`.

### Stage 2 — Primitive layer (`lab_primitives`) — ✅ done

- [x] **`ids.py`:** added **`PICK_COMPONENT`**, **`HOVER`**, **`PLACE_FROM_HOVER`**, **`SCAN_ROTATE_IN_PLACE`**, **`CONFIRM_HOLDING_TAG`**.
- [x] **`schemas.py`:** `PickComponentBody` (free-form `parameters`), `HoverBody` with required `HoverParameters(target_x, target_y, rotation, z)`, `PlaceFromHoverBody` with `PoseTargetParameters`, `ScanRotateInPlaceBody` with `ScanRotateParameters(theta_min, theta_max, speed_deg_per_s>0, axis="z")`, `ConfirmHoldingTagBody`. All included in the discriminated `ValidatedCommand` union.
- [x] **`registry.py`:** all five registered as **`ATOMIC`** with handler names `pick_component` / `hover_component` / `place_from_hover` / `scan_rotate_in_place` / `confirm_holding_tag`.
- [x] **`dispatch.py`:** `_invoke_atomic` awaits each new method; `schedule_validated_command` background-schedules them with descriptive "accepted" messages.
- [x] Legacy **`SCAN`** logs a deprecation warning pointing to `SCAN_ROTATE_IN_PLACE`.
- [x] **Validation:** `_enforce_holding_rules` in `backend/main.py` rejects out-of-state commands with descriptive HTTP **409** (e.g. `MOVE_COMPONENT` while `HOLDING`, `HOVER` when not `HOLDING`, any command while `HOLDING_UNCONFIRMED`).

### Stage 3 — `LabCommunicator` interface (`base.py`) — ✅ done

- [x] Abstract async methods added: `pick_component`, `hover_component`, `place_from_hover`, `scan_rotate_in_place`, `confirm_holding_tag`.
- [x] `get_gripper_status()` hook (default `{"closed": False, ...}`) for startup reconciliation.
- [x] `get_lab_state()` is required to include the top-level **`holding`** field (enforced by mock; real will follow in Stage 6).

### Stage 4 — Mock implementation (`mock.py`) — **mock-complete line** — ✅ done

- [x] `system_status` and `holding` persist in **`mock_lab_state.json`** across restarts.
- [x] **`PICK_COMPONENT`:** `IDLE → BUSY → HOLDING`, pose at `DEFAULT_HOVER_Z_MM` safe-Z; refuses when already `HOLDING`.
- [x] **`HOVER`:** requires same-tag `HOLDING`; updates `nominal_pose` / `measurables.pose` including `z`; keeps `system_status == HOLDING` on completion.
- [x] **`PLACE_FROM_HOVER`:** `HOLDING → BUSY → IDLE`, clears `holding`, writes table pose with noise, strips `z` from the placed nominal pose.
- [x] **`SCAN_ROTATE_IN_PLACE`:** sweeps `nominal_pose.rotation` live from `theta_min` → `theta_max` in discrete steps with `asyncio.sleep(|Δθ|/speed)`; remains `HOLDING` on completion.
- [x] Dev flag **`MOCK_GRIPPER_CLOSED_ON_BOOT=1`** forces `HOLDING_UNCONFIRMED` on startup (§6.3) regardless of snapshot; cleared by `CONFIRM_HOLDING_TAG`.

> **After Stage 4** the entire frontend flow (pick → hover → scan rotate → place) is testable end-to-end **without hardware**. Verified via unit + HTTP smoke tests.

### Stage 5 — Frontend (cloud-labs UI) — ✅ done

- [x] **Status badge** renders `HOLDING <tag>` (purple) and `HOLDING <tag> UNCONFIRMED` (red).
- [x] **`component-model.js`:** added `SYSTEM_STATUS_HOLDING`, `getHolding`, `isHoldingState`, `isHeldTag`, `isHoldingUnconfirmed` helpers.
- [x] **Context panel:** when `HOLDING` the selected part → hides default *Move to Coordinates*, shows `Z clearance (mm)` input, **Hover to X/Y/Rot/Z**, **Place from hover**, and a **Scan Rotate In Place** sub-panel (`theta_min`, `theta_max`, `speed_deg_per_s`). When `IDLE` + on-table → **Pick up** button. When `HOLDING` a different part → message + disabled controls.
- [x] **Blocking banner (`#holding-banner`):** warning + `Confirm held tag` button while `HOLDING_UNCONFIRMED`; contextual pose info otherwise.
- [x] **Ghost / solid:** the held component's `ghostState` (including `z`) continuously mirrors `holding.nominal_pose` while `HOLDING`; scan-rotate follows live `nominal_pose.rotation`.
- [x] **Command Console:** shortcuts `pick`, `hover`, `placehover`, `scanrotate`, `confirmhold` parsed in `command-parse.js`; help text updated.
- [x] 409 detail from the backend is surfaced to the command log verbatim (so `MOVE` during `HOLDING` etc. give human-readable errors).

### Stage 6 — Real placeholders (`real.py`) — ✅ done

- [x] Each of the four primitive methods plus `confirm_holding_tag` lives on `RealLabCommunicator` (`backend/lab_communicator/real.py`) and logs `[REAL LAB] PLACEHOLDER: action=… target_id=…` with validated args before returning. No hardware motion is dispatched.
- [x] **Startup gripper reconciliation (§6.3):** `_reconcile_holding_on_boot` polls `get_gripper_status()` during `__init__`. The override probes (in order) `experiment.get_gripper_status`, `experiment.robot.get_gripper_status`, `experiment.is_gripper_closed`, `experiment.robot.gripper_closed` and falls back to `closed=False` when `lab_automation` does not yet expose any of those. If `closed=True` and the snapshot does not already declare a confirmed `HOLDING`, the state is forced to `HOLDING_UNCONFIRMED` with `requires_operator_confirm: true`.
- [x] Confirm flow: `real.confirm_holding_tag()` clears `requires_operator_confirm` and stamps `holding.tag_id` via `lab_model.holding.confirm_holding_tag`. Works regardless of the placeholder flag (no motion involved).
- [x] Feature flag **`HOVER_PLACEHOLDER_STATE=1`**: when set, each placeholder method mutates `current_state` using the same `set_holding` / `clear_holding` / pose-update path as mock, so the UI can drive the whole HOLDING workflow against real-lab mode without hardware motion. When unset, the placeholders log-and-return — safe default for a live table.
- [x] `get_lab_state()` on the real communicator always includes a normalized `holding` field, and `set_lab_state()` strips any inherited `holding` from loaded snapshots (boot reconcile is the single source of truth).

### Stage 7 — Recipes & golden snapshots — ✅ done

- [x] `RECIPE_ACTION_ALIASES` in `backend/lab_primitives/dispatch.py` resolves shorter recipe names before Pydantic validation: `PICK → PICK_COMPONENT`, `PLACE_HOVER → PLACE_FROM_HOVER`, `SCAN_ROTATE → SCAN_ROTATE_IN_PLACE`, `CONFIRM_HOLDING → CONFIRM_HOLDING_TAG` (alongside the pre-existing `PLACE → MOVE_COMPONENT`). Documented in `backend/lab_primitives/README.md`.
- [x] Golden snapshots (`recipes/<id>_golden.json`) now include the top-level `holding` + `system_status` alongside `components`, so recipes that end mid-HOLDING are reproducible.
- [x] `compare_golden_state` in `backend/main.py` extends the per-component drift to **xyz** when both sides carry `z`, and falls back to **xy** when the golden predates the in-air primitives. Each entry now reports a `drift_axes` field (`"xy"` or `"xyz"`) so the UI can surface which dimensions were compared.
- [x] `compare_golden_state` also diff-checks the `holding` field when the golden has one: emits `HOLDING_MISMATCH` if the held tag differs, or `HOLDING_DRIFTED` (with the xyz distance of `holding.nominal_pose`) if the same tag is held at a different in-air pose. Old goldens without `holding` skip this block unchanged.

### Stage 8 — Real implementation (hand-off to `lab_automation`)

The specification for `lab_automation` — what methods to add on `OpticalExperiment`, how `get_gripper_status` must behave, coordinate/safety conventions, and the minimal follow-up change needed in `real.py` once the motion lands — is documented separately in **`labautomation_new_primitives.md`** (repo root). That document is the authoritative contract for this stage; the bullets below are a brief recap only.

- [ ] Implement **pick** macro (approach → close → retract to safe Z) and wire into **`pick_component`**.
- [ ] Implement **in-air cartesian / joint move** for **`hover_component`** — distinct from **`place_component_wo_home_specific_xy_cloudlab`**.
- [ ] Implement **`place_from_hover`** by reusing the existing table-place pipeline with an already-held part (skip re-pick).
- [ ] Implement **`scan_rotate_in_place`** — constant-rate angular sweep around **`axis="z"`**; no camera sync required for v1.
- [ ] Document exact **`z`** frame + safe clearance in **`coordinate_rotation.md`** (or sibling note).
- [ ] Wire **`get_gripper_status()`** / **`confirm_holding_tag`** calls to actual hardware signals.

### Stage 9 — Tests & docs

- [ ] Unit tests on **`lab_primitives`** validation (preconditions, reject-while-holding, etc.).
- [ ] Mock integration test: **`PICK → HOVER → HOVER → PLACE_FROM_HOVER`** sequence via **`/api/command`**.
- [ ] UI smoke test: restart with `MOCK_GRIPPER_CLOSED_ON_BOOT=1` shows the confirm banner and blocks cross-part commands.
- [ ] Update **`primitives.md`** (quick reference), **`backend/lab_primitives/README.md`**, **`backend/lab_model/README.md`** (z / holding), **`model.md`** (if relevant), and **`ROADMAP.md`**.

### Stage 10 — Follow-ups (explicitly deferred)

- **Camera exposure / frame sync / debug output for `SCAN_ROTATE_IN_PLACE`** (mirrors **`OPTIMIZE`** image dirs). Not required for v1.
- **Pick-from-storage**, **store-from-hover**, or similar compound flows.
- **Real-mode ghost updates per scan angle** (live sweep progress).

---

## 13. Document history

- **Initial draft:** `HOVER` + `SCAN_ROTATE_IN_PLACE` only.
- **Revision:** Added **`PICK_COMPONENT`** (grasp ≠ hover), **`PLACE_FROM_HOVER`** (Option A only), **§6.3 gripper poll on boot**, **`HOLDING_UNCONFIRMED` / operator confirm**, deferred **scan** camera sync.
- **Roadmap added:** Stages 0–10 implementation plan.
- **2026-04-17:** Stages 0–5 marked complete (backend primitives, mock implementation, and full Cloud-Labs UI integration all landed; real-hardware stages 6–10 still pending).
- **2026-04-17 (later):** Stages 6–7 marked complete — `RealLabCommunicator` now carries safe placeholder methods + boot-time gripper reconciliation + `HOVER_PLACEHOLDER_STATE=1` flag, recipe JSON accepts shorter aliases (`PICK`, `PLACE_HOVER`, `SCAN_ROTATE`, `CONFIRM_HOLDING`), and golden-state comparison covers `z` and `holding` with backwards compatibility. Stage 8 (real motion) spec handed off in `labautomation_new_primitives.md`.
- **2026-04-17 (later still):** `SCAN_ROTATE_IN_PLACE` widened to cover **both** `HOLDING` (held-mode) and `IDLE` + on-breadboard (placed-mode). Still **one** primitive / one UI button. `RealLabCommunicator.scan_rotate_in_place` now dispatches at runtime to `scan_rotate_held_cloudlab` vs. `scan_rotate_placed_cloudlab` (both documented in `labautomation_new_primitives.md` §2.4). Validation in `_enforce_holding_rules` updated; mock + UI mirror the branching.
- **2026-04-17 (z convention):** Wrote down the `z_lab` / `z_robot` convention explicitly (§10.5) and added the transform to `RealLabCommunicator`: new module-level constants `TABLE_Z0_ROBOT_MM`, `GRASP_OFFSET_MM`, `DEFAULT_COMPONENT_HEIGHT_MM`, `MAX_SAFE_HOVER_Z_LAB_MM` (all env-overridable); per-component `height_mm` field added to `schemas/component_catalog.real.json` + `.mock.json`; helpers `_component_height_mm`, `_z_lab_to_robot`, `_z_robot_to_lab`, `_intent_hover_z_lab` introduced. `PICK_COMPONENT` no longer sends a hardcoded `DEFAULT_HOVER_Z_MM` to `lab_automation` — it asks `compute_intent_hover_z_lab(component)` after pick completes (falls back to `DEFAULT_HOVER_Z_MM` if the helper is absent). `HOVER` forward-transforms the user's `z_lab` to `z_robot` before dispatch and bounds-checks `[0, MAX_SAFE_HOVER_Z_LAB_MM]`. UI z label updated to "Z CLEARANCE (mm above table)". Mock is unchanged — it was already the reference `z_lab` implementation. Open question #1 in §11 marked resolved.
- **2026-04-17 (measurables parity):** `PICK_COMPONENT` and `HOVER` now also write `measurables.pose` (x/y/rotation/z = commanded intent), matching the long-standing `MOVE_COMPONENT` convention. Rationale: robot placement accuracy is higher than the top camera's read through the gripper, so the commanded pose is the best available estimate of the current pose. A future vision pipeline can still overwrite `measurables.pose` later when it has a more precise reading. Docs in §10.5 updated accordingly.
- **2026-04-17 (fixing.md audit):** A broader audit (`fixing.md` in the repo root) found that cloud-labs was reaching across the wall into `lab_automation`-owned state in `backend/lab_communicator/real.py` — patching up `inventory_location`, `current_location`, and `is_placed` after scans and picks. Decision: cloud-labs will **ignore `inventory_location`** entirely and treat `current_location` as the single canonical "where is this part now" field; `is_placed` stays pushed by cloud-labs only in `set_lab_state` (load-state is ground truth). The audit also surfaced two latent safety bugs in `set_lab_state`: (a) `z_lab` was being loaded verbatim into `z_robot` for snapshots that captured hover z, and (b) `rotation` was being passed directly as `yaw_robot` even though `move_component` applies a `-rot` transform. See `fixing.md` §§2–3 for the full audit and Stage A/B/C roadmap.
- **2026-04-17 (Stage A landed):** `fixing.md` Stage A implemented in `backend/lab_communicator/real.py`: `set_lab_state` now routes all three axes through helpers (`lab_table_xy_to_robot_xy`, `_z_lab_to_robot`, new `lab_rotation_to_robot_yaw` / `robot_yaw_to_lab_rotation`); `hover_component` also routes rotation through the new helper. `lab_rotation_to_robot_yaw` is identity today — the one-line fix point for when the lab↔robot rotational offset is measured. `move_component`'s empirically-correct `angle=[-180, 0, -rot]` is left unchanged with a comment pointing at `fixing.md` §3.1 (reconciliation deferred to next physical-robot session). Regression test for the save→load round-trip (A6) deferred: `RealLabCommunicator` isn't importable in the current test env without `lab_automation`, and mocking it for one test is more cost than value right now.
- **2026-04-17 (Stage B handoff):** `fixing.md` Stage B written up as a `lab_automation`-facing contract in `labautomation_new_primitives.md` §5 (new section). Seven contract items for the `lab_automation` maintainer: `pick_component_cloudlab` must write `current_location`; `scan_components_cloudlab` must write `current_location`; no `_cloudlab` function reads or writes `inventory_location`; per-primitive read/write matrix to be filled in on the automation side; `Pose` frame docs; optional `is_registered(comp)` helper; plus `compute_intent_hover_z_lab` carried over from Stage 8. Q1–Q5 decisions from `fixing.md` §8 inlined so they stay locked. Stage B and Stage 8 can ship independently. Stage C (cloud-labs cleanup — drop all `inventory_location` reads/writes from `real.py`) stays blocked on Stage B landing in `lab_automation`.
- **2026-04-17 (Stage 8 wiring live):** `lab_automation` shipped all five `*_cloudlab` methods (`pick_component_cloudlab`, `hover_component_cloudlab`, `place_from_hover_cloudlab`, `scan_rotate_held_cloudlab`, `scan_rotate_placed_cloudlab`), plus `compute_intent_hover_z_lab` / `get_intent_hover_z_lab`, `get_gripper_status`, `OpticalComponent.is_registered`, and `height_mm` / `is_held` / `last_hover_pose` fields on `OpticalComponent`. The real paths in `RealLabCommunicator` (`pick_component` / `hover_component` / `place_from_hover` / `scan_rotate_in_place`), which were written as dormant `asyncio.to_thread(...)` calls back in Stages 6–7, now activate automatically because the `getattr(self.experiment, "scan_rotate_*_cloudlab", None)` probes succeed. Signature compatibility confirmed across all four primitives. Polish: (a) `_holding_placeholder_log` now says `DISPATCH:` when the real path is active and `PLACEHOLDER:` only when `HOVER_PLACEHOLDER_STATE=1` is forcing the state-only stub; (b) `scan_rotate_in_place` pre-flight refusals dropped the misleading "PLACEHOLDER:" tag (they're legitimate input-validation errors, not placeholder-mode behavior); (c) `hover_component`, `place_from_hover`, and `scan_rotate_in_place` now emit completion log lines matching the existing `pick_component` pattern. `HOVER_PLACEHOLDER_STATE=1` escape hatch preserved per `labautomation_new_primitives.md` §6 item 3 (still useful for UI testing without a real robot). What remains for Stage 8: item 5 of that §6 — an end-to-end recipe/golden-state test against the live robot (`PICK → HOVER → HOVER → PLACE_FROM_HOVER` plus `SCAN_ROTATE_IN_PLACE` held + placed), which can only be done on the physical setup.
- **2026-04-17 (Stage C landed):** `fixing.md` Stage C swept `inventory_location` out of `backend/lab_communicator/real.py` in four call sites (`_initialize_state` reads, `set_lab_state` write, `move_component` sentinel, `pick_component` sentinel) and added a CI lint to prevent regressions. `_initialize_state` now reads `comp.current_location` directly (the `comp.current_location = comp.inventory_location` compatibility shim deleted); `set_lab_state` now writes only `current_location` + `is_placed`; both sentinels flipped to `if not comp.current_location:` with matching operator-log updates. `rg "\.inventory_location" backend/lab_communicator/real.py` returns zero matches — the two remaining prose mentions live inside comments explaining *why* cloud-labs stays away from the field (intentional documentation). CI lint: new `StageCInvariantsTests` class in `backend/tests/test_lab_primitives.py` with 3 tests that parse `real.py` into method blocks and fail on any `.inventory_location` attribute access, on `.current_location =` writes outside `set_lab_state`, or on `.is_placed =` writes outside `{set_lab_state, affirm_placed_at_current}`. The `affirm_placed_at_current` exemption is Stage C6's option (a) — that primitive is the manual-place flow and legitimately writes `is_placed = True` without going through a snapshot load. Regex parsing is used instead of `ast` so the test survives without `lab_automation` importable in the test environment. All 9 tests in `test_lab_primitives` pass. No functional change to hover/pick/place/move flows — Stage C is purely a cleanup + a guard rail. This closes the architectural thread started by `fixing.md`; what remains is the physical-robot test described in the Stage 8 entry above.
