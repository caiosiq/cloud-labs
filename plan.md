# Plan: Use roll, pitch, yaw in the UI for correct component rotation

## Context

- **lab_automation** (stereo) now returns a `Pose` with real **roll, pitch, yaw** from the ArUco tag (Euler angles from `avg_rvec`). No extra conversion — keep that as-is.
- The **backend** (real communicator) currently sends only a single **`rotation`** value (from `inv.yaw` or from `get_rotation_from_angle(rx,ry,rz)`) in the pose.
- The **UI** uses that single `rotation` as the angle for drawing components (e.g. `pose.rotation * (Math.PI / 180)` for the canvas).
- **Problem:** The correct orientation is the full (roll, pitch, yaw). Using only yaw as “rotation” is wrong if the UI’s expected “rz” should be derived from all three. So the fix is: **send roll, pitch, yaw from backend and let the UI compute the correct display rz from them.**

---

## Goal

1. **Do not change** how lab_automation computes or returns roll, pitch, yaw (no extra degree/radian conversion; keep current stereo code).
2. **Backend:** Send **roll, pitch, yaw** in the pose payload to the frontend (when available from `inventory_location`).
3. **Frontend:** Use **roll, pitch, yaw** to compute the **display rotation (rz)** used for drawing the component, so the angle shown in the UI matches what you see in the lab. Keep supporting a single `rotation` when roll/pitch/yaw are not present (fallback).

---

## Steps (implementation plan)

### 1. Backend (cloud-labs) — send roll, pitch, yaw in pose

**Where:** `backend/lab_communicator/real.py` (in `_initialize_state()`, where we build `pose` for each component).

**Current:** We set `pose = { "x": inv.x, "y": inv.y, "rotation": calc_rotation }` and only pass a single `rotation` (from yaw or from angle_vector).

**Change:**

- Add **roll, pitch, yaw** to the pose when we have `inventory_location`:
  - `pose["roll"]` = `getattr(inv, "roll", None)`
  - `pose["pitch"]` = `getattr(inv, "pitch", None)`
  - `pose["yaw"]` = `getattr(inv, "yaw", None)`
- Keep sending **`rotation`** for backward compatibility (e.g. still set from yaw or from `get_rotation_from_angle` when we don’t have roll/pitch/yaw). The frontend will prefer roll/pitch/yaw when present to compute display rz.

**Result:** Each component’s `pose` in the state can contain `x, y, rotation` and optionally `roll, pitch, yaw`.

---

### 2. Frontend — derive display rotation (rz) from roll, pitch, yaw

**Where:** `frontend/app.js` — everywhere we use `pose.rotation` for **drawing** or for the **displayed rotation value** (e.g. canvas rotate, rotation input field).

**Current:** We use `pose.rotation` (degrees) and do `pose.rotation * (Math.PI / 180)` for `ctx.rotate()`.

**Change:**

- Add a small helper, e.g. **`getDisplayRotation(pose)`**:
  - If `pose` has **roll, pitch, yaw** (all numbers): compute the **display rz in degrees** from them. For a **top-down 2D lab view**, the in-plane angle is **yaw** (rotation around the vertical axis), so: **display rz = pose.yaw** (in degrees). If your convention is different (e.g. roll is the “angle we see”), we use that instead.
  - Else: use **`pose.rotation`** as today (fallback).
- Use **`getDisplayRotation(pose)`** wherever we need the angle for:
  - Drawing: `ctx.rotate(getDisplayRotation(pose) * (Math.PI / 180))`
  - Any UI field that shows “rotation” for the component (e.g. the rotation input), so the user sees the same rz that is used for drawing.

**Result:** The component is drawn and displayed with the correct rz derived from (roll, pitch, yaw) when available; otherwise we keep using `pose.rotation`.

---

### 3. Optional: same for ghost / nominal pose

If the frontend stores or uses **ghost state** or **nominal_pose** with a single `rotation`, consider storing **roll, pitch, yaw** there too when we receive them from the backend, and using **`getDisplayRotation(pose)`** when reading from ghost/nominal pose for drawing. That keeps behavior consistent.

---

## Summary

| Layer            | Action |
|------------------|--------|
| **lab_automation** | No change. Keep returning Pose(roll, pitch, yaw) from stereo as-is (Euler from rvec, degrees from scipy). |
| **Backend (real)** | Add roll, pitch, yaw to pose in state when available; keep rotation for fallback. |
| **Frontend**       | Add getDisplayRotation(pose): rz = pose.yaw when roll/pitch/yaw present, else pose.rotation. Use it for drawing and for the rotation display. |

---

## Convention note

The plan assumes that for your **top-down lab view**, the angle you see on the lab (the “rz” for the UI) is **yaw** (rotation around the vertical axis). If in your setup the visible angle is actually **roll** or a combination of roll/pitch/yaw, we only need to change the formula inside `getDisplayRotation(pose)` (e.g. `return pose.roll` or a small conversion). The structure (backend sends roll/pitch/yaw, UI derives rz) stays the same.

Once you’re happy with this plan, we can implement it step by step (backend first, then frontend).
