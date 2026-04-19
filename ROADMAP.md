# Project Roadmap: Optical Digital Twin

This roadmap outlines the development stages for the Optical Digital Twin, a system designed to separate user intent (Ghost State) from physical reality (Physical State), enabling virtual experiment planning synchronized with a physical robotic lab.

## Current Status
- **Current Phase:** Phase 4.6 - Closed-Loop Correction & Advanced Simulation
- **Last Updated:** 2026-02-15

---

## Phase 0: Architecture & Data Strategy
**Goal:** Define the system's data structures, communication protocols, and project structure before implementation.

- [x] **Define the `lab_state.json` Schema (Physical State)**
    - Create a strictly typed JSON schema representing the physical reality of the lab.
    - Include system status fields (e.g., IDLE, BUSY, ERROR) and timestamps.
    - Define the structure for optical components, including unique identifiers, types, current states (INVENTORY vs. PLACED), spatial poses (x, y, rotation), and metadata (e.g., optimization scores).

- [x] **Define the `client_payload.json` Schema (Ghost State/Intent)**
    - Create a JSON schema representing user intent and commands.
    - Define supported actions: `MOVE`, `SCAN`, `OPTIMIZE`, `SEQUENCE`.
    - Specify parameters for algorithmic actions (e.g., strategy type for alignment).

- [x] **Establish the Synchronization Protocol**
    - Determine the method for data exchange between client and server (e.g., Polling vs. WebSocket).
    - *Decision:* Initial implementation will use polling of `lab_state.json` for simplicity.

- [x] **Project Reorganization**
    - Organize the repository into a modular structure suitable for a Python-centric stack (FastAPI serving static files).
    - Create a `schemas/` directory for the JSON definitions.
    - Create a `backend/` directory for the FastAPI application.
    - Create a `frontend/` directory for the HTML/JS/CSS (served by FastAPI).

---

## Phase 1: The "Read-Only" Twin (Visualization)
**Goal:** Develop a user interface that accurately renders the laboratory state from a static data source.

- [x] **Backend Initialization (FastAPI)**
    - Set up a basic FastAPI server.
    - Configure it to serve static files from the `frontend/` directory.
    - Create an endpoint `GET /api/lab-state` that serves the mock `lab_state.json`.

- [x] **Frontend Initialization (Vanilla JS/HTML)**
    - Create a simple `index.html` and `style.css`.
    - Set up a basic JavaScript module structure.

- [x] **Virtual Breadboard Implementation (Canvas)**
    - Implement a 2D visualization stage using HTML5 Canvas or a lightweight library (e.g., Konva.js via CDN).
    - Configure the coordinate system to map physical metric units to screen pixels.
    - Render static environmental elements, such as the calibrated laser path.

- [x] **Component Rendering System**
    - Develop visual representations for specific optical components (Mirror, Lens, Crystal, etc.).
    - Implement the "Solid" state rendering logic to display components exactly as defined in the fetched `lab_state.json`.

- [x] **Live Monitor Panel**
    - Create a dashboard to display raw system data or formatted logs derived from the lab state.

---

## Phase 2: The "Ghost" Layer (Intent & Interaction)
**Goal:** Enable users to plan and visualize changes in the virtual environment without affecting the physical system.

- [x] **Ghost State Management**
    - Implement a client-side state manager to track "Desired Positions" independent of the physical state.
    - Develop rendering logic for "Ghost" components (semi-transparent) to distinguish them from physical "Solid" components.

- [x] **User Interaction Logic**
    - Implement drag-and-drop functionality for moving components from the inventory to the table.
    - Apply constraints to ensure valid placement (e.g., snapping ghosts to the calibrated laser path).
    - Implement basic collision detection to warn users when a Ghost component overlaps with a physical component.

- [x] **Command Generation System**
    - Develop logic to translate UI interactions into valid `client_payload.json` structures.
    - Implement a "Commit" mechanism to send generated commands to a `POST /api/command` endpoint (which just logs them for now).

---

## Phase 3: The "Live" Twin (Connection & Feedback)
**Goal:** Connect the UI to a simulated backend to establish the command-response loop.

- [x] **Mock Backend Logic**
    - Implement the `POST /api/command` endpoint to accept `client_payload.json`.
    - Update the internal state of the mock server based on received commands.

- [x] **Simulation Loop Implementation**
    - Develop backend logic to simulate robot movement delays and update the served `lab_state` accordingly.
    - Simulate sensor feedback by calculating and updating "Result" coordinates after moves.

- [x] **UI Synchronization & Feedback**
    - Implement visual indicators for "Pending" states (e.g., loading animations).
    - Animate "Solid" components to transition toward "Ghost" positions upon backend confirmation.
    - Visualise "Drift" to show discrepancies between the planned position (Ghost) and the actual placed position (Physical).

---

## Phase 4: Advanced Features (Intelligence & Complex Workflows)
**Goal:** Expand functionality to support complex experimental procedures and algorithmic control.

- [x] **Alignment Strategy Integration**
    - specific UI controls for algorithmic actions (e.g., Context Menu -> "Auto-Align").
    - specific visualization for optimization processes (e.g., real-time graphing of beam intensity).

- [x] **Experiment Sequencer**
    - specific a "Timeline" or "Recipe" interface for chaining multiple actions.
    - specific functionality to Save and Load experiment configurations.

- [ ] **Error Handling & Safety Systems**
    - specific visual alerts for system anomalies, such as lost components or robot errors.
    - specific an "Emergency Stop" interface in the UI.

---

## Phase 4.5: Golden Datastructures (The 4-Tier Model)
**Goal:** Evolve the data architecture to solve the "Optimization Discrepancy" and "History/Recipe" problems. We are moving from a simple "Twin" to a "Diff Engine" that can compare Intent vs. Reality vs. History.

**Rationale:**
- **Tier 2 (Smart Ghost):** Solves the "Optimization Discrepancy" (Ghost says X, Lab says X+0.2). It stores both the "Nominal Intent" (what the user dragged) and the "Learned Reality" (where optimization actually landed).
- **Tier 3 (Recipe):** Solves the "History Problem". Snapshots are insufficient because they miss transient steps (e.g., "Place Camera -> Align -> Remove Camera"). We need a replayable sequence of actions.
- **Tier 4 (Golden State):** Solves the "Drift Problem". If a setup works today, we need to save that exact physical state as the "Golden Reference" to compare against tomorrow.

### Tasks:

- [x] **Tier 2: The "Smart" Ghost State**
    - Update schema to include `nominal_target` (Intent), `placement_strategy` (How), `last_optimized_pose` (Learned Reality), and `is_optimized` flag.
    - Implement logic to distinguish between "Mismatch" (Lab vs Nominal) and "Synced" (Lab vs Optimized).
    - Update backend to write back optimization results to the Ghost State ("Learning" loop).

- [x] **Tier 3: The Recipe (ExperimentSequence)**
    - Define schema for a sequence of actions (`PLACE`, `OPTIMIZE`, `REMOVE`, etc.) instead of static snapshots.
    - Implement "Recorder" mode in UI to build recipes from user actions.
    - Implement "Player" logic in Backend to execute recipes step-by-step.

- [x] **Tier 4: The Golden State (Reference Snapshot)**
    - Define schema for storing a "Success Snapshot" (Physical Poses + Metrics) linked to a Recipe.
    - Implement logic to compare current Lab State against Golden State to detect "Drift".
    - UI visualization for "Drift" (Golden State Overlay vs Real State).

---

## Phase 4.6: Closed-Loop Correction & Advanced Simulation
**Goal:** Close the feedback loop by allowing the system to automatically correct drift based on Golden State comparisons, and improve the simulation fidelity.

### Proposed Features:
- [ ] **Auto-Correct Drift**
    - One-click action to move drifted components back to their Golden State positions.
    - "Smart Re-Alignment": If physical move isn't enough, trigger re-optimization using the original strategy.

- [ ] **Visual Simulation (Ray Tracing)**
    - Implement a simple 2D ray tracer in the browser to visualize *why* alignment matters.
    - Show the beam path based on current component positions (Ghost vs. Physical).

- [ ] **Python Client Library**
    - Create a simple `optics_twin` Python package.
    - Allow users to script recipes or control the lab programmatically without using the web UI (e.g., `twin.move_component("mirror_1", x=100)`).

- [ ] **Persisting Lab State**
    - Add "Save Lab State" and "Load Lab State" buttons to the Debugger.
    - Allow resetting the Mock Lab to a known "Clean" state.

---

## Phase 5: Real World Integration (The "Real" Twin)
**Goal:** Transition from the simulated backend to the physical laboratory hardware, ensuring the Digital Twin reflects the *actual* physical reality.

### Key Strategy: The "Scan-First" Initialization
To ensure the Digital Twin matches reality, we will not hardcode the initial state. Instead, we will use the `scan_components` capability of the `OpticalExperiment` manager to discover what is actually on the table.

- [x] **Define the Physical Inventory (Catalog)**
    - Update `component_catalog.real.json` to match the *actual* ArUco tags used in the lab (e.g., ND Filter=Tag 9, CAM1=Tag 22). Mock-mode UI demos load from a separate `component_catalog.mock.json`.
    - This ensures that when the user requests "ND Filter", the system knows exactly which physical object to look for.

- [x] **Implement `RealLabCommunicator`**
    - Create the adapter class that wraps `OpticalExperiment`.
    - **Initialization:** On startup, iterate through the known inventory list and run `experiment.scan_components()`.
    - **State Population:** Populate the `lab_state.json` with the *actual* poses found during the scan. This becomes the "Ground Truth".

- [x] **Live Command Execution**
    - Map `MOVE` commands to `experiment.place_component_wo_home_specific_xy`.
    - Map `OPTIMIZE` commands to `experiment.optimize_component` with the correct strategy class (Newton/Cobyla).

- [ ] **Live Video Integration**
    - Integrate MJPEG streams from the physical lab cameras into the UI.

- [ ] **Real-time Telemetry**
    - Display real-time data from the robot (status, joints) in the UI.

- [ ] **Fix Cobyla Alignment**
    - The `COBYLA` strategy requires specific hardware mappings (`motor_ids`, `camera_number`) which are currently not supported by the frontend UI.
    - Implement a mechanism to dynamically fetch or configure these parameters before enabling the strategy on the Real Lab.
    - Currently, `RealLabCommunicator` raises a `NotImplementedError` to prevent unsafe operations.
