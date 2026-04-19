# Schemas

JSON fixtures under this directory back **mock lab state**, **component catalogs**, and **motor rotation** tracking.

## Component catalogs (real vs. mock)

`GET /api/catalog` serves the catalog for the **currently running** `LAB_MODE`:

- **`component_catalog.real.json`** — the physical inventory on the real table (tags, sizes, optional `motor_ids`, `motor_controller`). Keep this in sync with what is actually bolted down.
- **`component_catalog.mock.json`** — a richer catalog used only when `LAB_MODE=MOCK`. Freely extend to showcase parts in the UI without needing the physical hardware.

Both are re-read from disk on every request, so edits are picked up without restarting the backend (restart only when switching modes). See `backend/lab_communicator/base.py::LabCommunicator.get_catalog` for the resolution logic.

## `mock_lab_state.json` and `camera_image`

Per-component **`measurables.camera_image`** is often **`null`** until an **observe** run (`OBSERVE_MEASURABLES` or `POST .../measurables/observe`). For **`OPTICAL_CAMERA`** catalog types, mock/real communicators may set an object such as:

```json
{
  "path": "/absolute/path/to/tag_22_last.png",
  "source": "mock_table_cam",
  "cam_id": 1,
  "format": "png"
}
```

Real mode typically writes under the lab’s **`Camera_Images`** tree; see **`backend/lab_model/README.md`** for field semantics.
