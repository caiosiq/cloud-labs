# Real Lab Integration Roadmap

This document outlines the step-by-step plan to transition the **Optical Digital Twin** from a mock environment to a fully integrated interface for the physical robotic laboratory.

## 1. Environment & Configuration

**Goal**: Ensure the backend can locate and import the external `lab_automation` repository without relying on hardcoded paths or local copies (like `backend-simple`).

*   [x] **Configurable PYTHONPATH**:
    *   Modify `backend/main.py` and `backend/real_lab_communicator.py` to check for a `LAB_AUTOMATION_PATH` environment variable.
    *   If present, append this path to `sys.path` dynamically at runtime.
    *   This allows the `lab_automation` repo to reside anywhere on the host machine (e.g., `C:\Users\CaioV\...\lab_automation`).

*   [x] **Dependency Check**:
    *   Create a startup check in `main.py` that verifies `lab_automation` can be imported.
    *   Fail gracefully with a clear error message if the library is missing when `LAB_MODE=REAL`.

## 2. Backend Implementation (`RealLabCommunicator`)

**Goal**: Implement the adapter class that translates Digital Twin commands into `OpticalExperiment` method calls.

*   [x] **Create `backend/real_lab_communicator.py`**:
    *   Implement the `RealLabCommunicator` class inheriting from `LabCommunicator`.
    *   **Initialization**:
        *   Instantiate `OpticalExperiment(mock=False)`.
        *   Run `experiment.initialize_robot()`.
    
*   [x] **Scan-First Initialization**:
    *   Instead of loading a static JSON, the communicator must:
        1.  Load the `component_catalog.json` to know *what* to look for (Tag IDs).
        2.  Call `experiment.scan_components(catalog_objects)` to find their physical locations.
        3.  Construct the initial `lab_state` dictionary from these real-world poses.
        4.  Handle cases where a catalog item is *not* found (mark as `INVENTORY` instead of `PLACED`).

*   [x] **Command Mapping**:
    *   **`move_component`**:
        *   Look up the `OpticalComponent` object by ID.
        *   Call `experiment.place_component_wo_home_specific_xy(...)`.
        *   Update the local `lab_state` with the new coordinates after the move completes.
    *   **`optimize_component`**:
        *   Map strategy names (`NEWTON`, `COBYLA`) to their class equivalents.
        *   Extract parameters (e.g., `camera_port`, `tolerance`) and instantiate the strategy object.
        *   Call `experiment.optimize_component(...)`.
        *   Update the `lab_state` with the final optimized pose and metadata (score).

## 3. Frontend & API Adjustments

**Goal**: Ensure the UI can handle the realities of the physical lab (slower responses, real video feeds).

*   [x] **Live Video Integration**:
    *   Update `GET /api/video-feed/stream` in `main.py` to use `StreamingResponse` for Real mode.
    *   In `RealLabCommunicator`, implemented a generator `get_video_stream()` that yields MJPEG frames from the `lab_automation` camera driver (or a fallback if unavailable).

*   [x] **Timeout Handling**:
    *   Verified that Frontend uses asynchronous `fetch` for commands.
    *   Backend uses `BackgroundTasks` for long-running operations (`move`, `optimize`), ensuring the HTTP request returns immediately with `202 Accepted` while the robot works.
    *   The `system_status` ("BUSY") is correctly managed by the `RealLabCommunicator` during these operations.

## 4. Verification & Testing

**Goal**: Validate the integration safely.

*   [ ] **Dry Run (Mocking the Real Lib)**:
    *   Test `RealLabCommunicator` by pointing `LAB_AUTOMATION_PATH` to a *mock version* of the lab automation library (or the existing `backend-simple` temporarily) to ensure imports and method calls work before connecting to the real robot.

*   [ ] **Physical Test**:
    *   Connect to the real network.
    *   Run a simple "Scan and Display" test (Read-Only).
    *   Run a simple "Move Mirror" test (Write).

## 5. Execution Plan

1.  **Phase 1**: Implement `RealLabCommunicator` skeleton and Path Configuration.
2.  **Phase 2**: Implement `scan_components` logic to populate the initial state.
3.  **Phase 3**: Implement `move` and `optimize` command translation.
4.  **Phase 4**: Integrated testing with `LAB_MODE=REAL`.
