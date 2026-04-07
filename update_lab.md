# lab_automation updates (lab computer)

The **optics-digital-twin** repo now creates a **per-optimization subfolder** under `Camera_Images/` and passes its path to alignment strategies **when their constructors support it**. The folder name looks like:

`opt_<YYYYMMDD_HHMMSS>_<STRATEGY>`  
Example: `opt_20260330_171200_NEWTON`

It is created under:

- **`$LAB_AUTOMATION_PATH/Camera_Images/`** when `LAB_AUTOMATION_PATH` is set, otherwise  
- **`Camera_Images/`** next to the process working directory (usually the digital-twin repo root).

The communicator watches **only that subfolder** during `OPTIMIZING`, feeds `/api/optimization-feed/stream`, and advances `optimization_step` from PNGs written there—so runs no longer overwrite each other **once strategies save into that folder**.

## What you must change in `lab_automation` (if images still land in the flat `Camera_Images/` folder)

1. **`NewtonPlacementStrategy_cloudlab`** — **done in `lab_automation`**
   - Optional **`output_dir`** on the constructor; **`execute`** resolves it with `os.path.abspath`, `os.makedirs`, and passes it to **`change_camera_exposure(..., output_dir=...)`**.
   - Filenames already use **`_filename_with_step`** (`test_step00.png`, …) for the cloud-labs step parser.

2. **`CobylaAlignmentStrategy_cloudlab`** — **done in `lab_automation`**
   - Optional **`output_dir`** (same pattern as above).
   - Initial `activate_cam_and_capture` and every **`request_capture_safe`** in the COBYLA objective use **unique** paths: **`{base}_step{NN}.png`** under that folder so **`request_capture_safe` does not delete prior iterations** (it only removes the file about to be written).
   - **`COBYLA_res.txt`** (or `file_name_position`) is written under **`output_dir`** when the path is relative.

3. **Process / CWD**
   - If your code uses paths relative to a different working directory, prefer **absolute** paths built from the passed `output_dir` so files land in the folder the communicator created.

4. **Verify**
   - Start a NEWTON run from the UI; confirm new PNGs appear **only** under  
     `Camera_Images/opt_<timestamp>_NEWTON/`  
     and that the table-cam optimization stream updates.

If the strategy does **not** accept any of the parameter names above, the communicator logs a warning and may still watch the new (empty) subfolder while images are written to the legacy flat folder—**step count and MJPEG will be wrong until `lab_automation` is updated.**

See also: `README.md` (optimization / `Camera_Images` section) and `backend/lab_communicator/real.py` (`_apply_optimization_output_dir_kw`, `_get_optimization_watch_dirs`).
