# Schemas

JSON fixtures under this directory back **mock lab state**, **component catalog**, and **motor rotation** tracking.

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
