# Real-Time Optimization Feed Design

## Is it Possible?
**Yes, absolutely.** Since the `NewtonPlacementStrategy` (and similar strategies) saves an image to disk at each optimization step (e.g., inside the `Camera_Images` directory), we can create a mechanism that "tails" this directory and streams the latest image to the UI without interfering with the camera hardware or the optimization loop itself.

## How to Implement It Efficiently (Minimal Impact)

To achieve this without blocking or slowing down the optimization process, we should use an **asynchronous file-watching approach** combined with **Server-Sent Events (SSE)** or an **MJPEG stream**.

### Architecture Proposal

1.  **Backend: File Watcher / Streamer**
    *   Create a new FastAPI endpoint (e.g., `/api/optimization-feed`).
    *   When the UI connects to this endpoint, the backend starts a background task or asynchronous generator.
    *   This generator monitors the target directory (e.g., `Camera_Images`) or a specific file path (e.g., `Camera_Images/test.png` or `Camera_Images/current_step.png`).
    *   **Efficiency**: Instead of aggressive polling, we can use `os.stat().st_mtime` to check if the file has been modified since the last check. If it has, read the image, optionally overlay the "Step X" text using OpenCV, and yield it to the stream.

2.  **Backend: Step Tracking**
    *   The optimization strategy currently just runs. To know "which step" we are on, the Strategy class could either:
        *   Write a small metadata file (e.g., `step_info.json`) alongside the image.
        *   Update a shared variable in the `RealLabCommunicator` state that the streaming endpoint can read.
        *   *Easiest*: Since the UI already receives system status updates via `GET /api/lab-state`, we can add `optimization_step: N` to the lab state payload.

3.  **Frontend: Displaying the Feed**
    *   In the UI, when the system status changes to `OPTIMIZING`, the "Table Cam" section (or a dedicated modal/overlay) switches its image source from the static on-demand capture to the new streaming endpoint: `<img src="/api/optimization-feed">`.
    *   The "Step X" legend can either be burned into the image by the backend (using `cv2.putText` before yielding) or overlaid via HTML/CSS by reading the step number from the `lab-state` polling. Overlaying via HTML is cleaner and uses less CPU on the backend.

### Step-by-Step Execution Plan

1.  **State Management**: Update `RealLabCommunicator` to track the current optimization step. (Requires minor modification to how `NewtonPlacementStrategy` reports progress, or passing a callback callback to it).
2.  **Stream Endpoint**: Create `/api/optimization/stream` in `main.py`.
    *   Uses a generator that checks the `st_mtime` of the `Camera_Images/test.png` file every ~100ms.
    *   If modified, it yields the new image as a multipart JPEG frame (just like the live ceiling feed).
3.  **UI Updates**:
    *   Modify `app.js` to detect when `labState.system_status === 'OPTIMIZING'`.
    *   Automatically switch the table camera `src` to the optimization stream.
    *   Show a small overlay `div` that reads `labState.optimization_step` and displays "Step: 3" etc.

### Why this has Minimal Impact
*   **No Camera Contention**: The streaming endpoint only reads from the disk. It never attempts to talk to the `CameraDriver` or the OpenCV `VideoCapture` object. The Strategy retains exclusive control over the hardware.
*   **Low CPU**: Checking file modification time (`os.stat`) is extremely fast. Reading a PNG and re-encoding it to JPEG for the stream takes a few milliseconds and runs in a separate async context, so it won't block the Robot or Camera drivers.
*   **Decoupled**: If the stream fails, the UI disconnects, or the user closes the browser, the optimization loop continues completely unaware and unaffected.
