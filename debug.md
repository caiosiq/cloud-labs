# Debugging Optimization Strategy Path Issues

## Issue Identification
When running the `NewtonPlacementStrategy` from the `RealLabCommunicator` in `optics-digital-twin`, the optimization fails because the image capture verification times out.

### Root Cause
1.  **Process Separation**: The system runs two sets of processes:
    *   **Client Process**: The `optics-digital-twin` backend (running in `.../optics-digital-twin`).
    *   **Recorder Processes**: The camera recorders launched by `RealLabCommunicator` (running in `.../robot-deathray`).
2.  **Relative Path Ambiguity**:
    *   The `NewtonPlacementStrategy` (in `robot-deathray/objects/strategies.py`) calls `change_camera_exposure` with a hardcoded relative path: `output_dir="Camera_Images"`.
    *   The helper function constructs a relative filepath: `Camera_Images/test.png`.
3.  **Divergent Working Directories**:
    *   The **Recorder** receives `CAP ... Camera_Images/test.png`. It resolves this relative to *its* CWD (`robot-deathray`), saving the file to `.../robot-deathray/Camera_Images/test.png`.
    *   The **Client** (Strategy) waits for the file to appear using `os.path.exists("Camera_Images/test.png")`. It resolves this relative to *its* CWD (`optics-digital-twin`), looking in `.../optics-digital-twin/Camera_Images/test.png`.
4.  **Result**: The Client never sees the file appear in its expected location, causing a timeout.

## Proposed Solution

To fix this, we must ensure that both processes refer to the exact same file location. The most robust way is to use **Absolute Paths**.

### Recommended Fix (In `robot-deathray`)

Modify `robot-deathray/objects/strategies.py` to resolve the output directory to an absolute path before passing it to the capture helper.

**File:** `robot-deathray/objects/strategies.py`
**Method:** `NewtonPlacementStrategy.execute`

**Change:**
```python
# Import os at the top if not present
import os

# ... inside execute method ...

# Calculate absolute path for output directory
# This resolves "Camera_Images" relative to the CURRENT Client CWD (optics-digital-twin)
# creating a shared absolute reference.
abs_output_dir = os.path.abspath("Camera_Images") 

img = change_camera_exposure(
        cam_id=self.camera_number,
        video_exposure=self.video_exposure,
        filename=self.file_name,
        settle_s=0.5,
        output_dir=abs_output_dir, # <--- Pass Absolute Path here
)
```

### Why this works
1.  `os.path.abspath("Camera_Images")` in the Client process generates a full string like `C:\Users\...\optics-digital-twin\Camera_Images`.
2.  This full string is sent to the Recorder.
3.  The Recorder saves to that exact absolute path (ignoring its own CWD).
4.  The Client checks that exact absolute path.
5.  Both processes sync correctly.
