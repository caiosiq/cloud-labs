# lab_automation updates (lab computer)

The **optics-digital-twin** repo now creates a **per-optimization subfolder** under `Camera_Images/` and passes its path to alignment strategies **when their constructors support it**. The folder name looks like:

`opt_<YYYYMMDD_HHMMSS>_<STRATEGY>`  
Example: `opt_20260330_171200_NEWTON`

It is created under:

- **`$LAB_AUTOMATION_PATH/Camera_Images/`** when `LAB_AUTOMATION_PATH` is set, otherwise  
- **`Camera_Images/`** next to the process working directory (usually the digital-twin repo root).

The communicator watches **only that subfolder** during `OPTIMIZING`, feeds `/api/optimization-feed/stream`, and advances `optimization_step` from PNGs written there—so runs no longer overwrite each other **once strategies save into that folder**.

## What you must change in `lab_automation` (if images still land in the flat `Camera_Images/` folder)

1. **`NewtonPlacementStrategy_cloudlab`**
   - Add an optional constructor argument, e.g. **`output_dir: str = "Camera_Images"`** (or `camera_images_dir`).
   - Thread **`output_dir`** into every call that currently hardcodes `"Camera_Images"` (e.g. `change_camera_exposure(..., output_dir=...)`, file saves, debug plots).
   - Keep filenames that include **`stepNN`** so the cloud-labs watcher can parse the step index (e.g. `test_step02.png`).

2. **`CobylaAlignmentStrategy`** (if it writes optimization / alignment frames to disk)
   - Same pattern: accept **`output_dir`** (or one of the aliases checked in optics-digital-twin: `output_dir`, `camera_images_dir`, `save_dir`, `image_output_dir`) and use it for all saves.

3. **Process / CWD**
   - If your code uses paths relative to a different working directory, prefer **absolute** paths built from the passed `output_dir` so files land in the folder the communicator created.

4. **Verify**
   - Start a NEWTON run from the UI; confirm new PNGs appear **only** under  
     `Camera_Images/opt_<timestamp>_NEWTON/`  
     and that the table-cam optimization stream updates.

If the strategy does **not** accept any of the parameter names above, the communicator logs a warning and may still watch the new (empty) subfolder while images are written to the legacy flat folder—**step count and MJPEG will be wrong until `lab_automation` is updated.**

See also: `README.md` (optimization / `Camera_Images` section) and `backend/lab_communicator/real.py` (`_apply_optimization_output_dir_kw`, `_get_optimization_watch_dirs`).
