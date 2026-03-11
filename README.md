# Optical Digital Twin: Robotic Experimentation Interface

## 🔭 Project Overview

This project is a **Digital Twin** user interface for an autonomous robotic laboratory. It allows researchers to design optical experiments (lasers, lenses, mirrors) via a drag-and-drop web interface and execute those designs on a physical **xArm6 Robot**.

The system bridges the gap between **High-Level Scientific Intent** (e.g., "Build a cavity") and **Low-Level Robotic Execution** (e.g., "Move TCP to x=300, y=100, open gripper").

---

## 🏗 Architecture

The system is built on a modular **4-Tier Data Architecture** to handle the complexity of robotic alignment:

### The 4 Tiers of Truth
1.  **Tier 1: Lab State (Physical Reality)**
    *   **Source:** Real-time sensor data / Robot telemetry.
    *   **Purpose:** The ground truth. "Where is the mirror *actually*?"
    *   **Format:** `lab_state.json` (Polling).
2.  **Tier 2: Ghost State (User Intent)**
    *   **Source:** User UI interactions.
    *   **Purpose:** The plan. "Where *should* the mirror be?"
    *   **Key Feature:** "Smart Ghost" stores both the nominal target and the *last optimized position*, allowing the system to distinguish between "Drift" and "Optimization Offset".
3.  **Tier 3: Recipe (Procedure)**
    *   **Source:** Recorded sequence of actions.
    *   **Purpose:** Replayability. An experiment is not a static snapshot; it is a sequence of steps (Place -> Optimize -> Remove).
    *   **Format:** `recipe_id.json`.
4.  **Tier 4: Golden State (Reference)**
    *   **Source:** Snapshot taken after a successful Recipe run.
    *   **Purpose:** Drift Detection. "Is the lab in the same state as when it last worked?"
    *   **Format:** `recipe_id_golden.json`.

### Tech Stack
*   **Frontend:** Vanilla JS + HTML5 Canvas (No heavy frameworks).
*   **Backend:** FastAPI (Python).
*   **Communication:** REST API + Polling (for simplicity and robustness).
*   **Hardware Interface:** Adapter Pattern (`LabCommunicator` -> `Mock` | `Real`).

### Synchronization Protocol (CQS)
The system follows a **Command-Query Separation** pattern:
1.  **Queries (Reading State)**: The Client *polls* `GET /lab-state` every 500ms to visualize reality.
2.  **Commands (Writing Intent)**: The Client *pushes* `POST /command` (e.g., `MOVE`, `OPTIMIZE`) to the Server.
    *   Response: `202 Accepted` (Queued), `409 Conflict` (Busy).
    *   The UI marks components as "Pending" and waits for the next Poll to confirm the physical move.

---

## 📂 Project Structure

```
optics-digital-twin/
├── backend/                # FastAPI Application
│   ├── main.py             # Entry point & API Routes
│   ├── lab_communicator/   # Hardware Abstraction Layer
│   │   ├── base.py         # Abstract Base Class
│   │   ├── mock.py         # Simulated Lab (Delays, Noise)
│   │   └── real.py         # Adapter for 'lab_automation' Library
│   └── ...
├── frontend/               # Static Web Assets
│   ├── index.html          # Main Interface
│   ├── debug.html          # Debugger & Data Visualizer
│   ├── app.js              # Core UI Logic
│   └── ...
├── schemas/                # JSON Data Stores
│   ├── component_catalog.json # Physical Inventory Definition
│   ├── lab_state.json      # Current Physical State
│   └── recipes/            # Saved Recipes & Golden States
├── README.md               # This file
├── ROADMAP.md              # Development Plan
└── online-update.md        # Remote Access Strategy
```

---

## 🚀 Usage Guide

### 1. Simulation Mode (Default)
The system runs in **Mock Mode** by default, simulating a physical lab with delays and sensor noise.

```bash
cd backend
# Install dependencies
pip install fastapi uvicorn
# Run the server
uvicorn main:app --reload
```
*   **Main UI:** [http://localhost:8000/](http://localhost:8000/)
*   **Debugger:** [http://localhost:8000/debug](http://localhost:8000/debug)

### 2. Real Lab Mode
To connect to the physical robotic setup, the system uses a **RealLabCommunicator** adapter that wraps the `lab_automation` Python package.

**Prerequisites:**
*   **Hardware**: xArm6 Robot, RealSense Camera (Ceiling), ArUco-tagged mounts, and 2 Table Cameras.
*   **Software**: The `lab_automation` package (drivers & managers) must be available.
*   **Scripts**: The system expects `recorder_cam_laser_align_simplified.py` in the `LAB_AUTOMATION_PATH` to handle camera drivers.

**Configuration:**
Set environment variables to point to your automation library:

**PowerShell Example:**
```powershell
$env:LAB_MODE="REAL"
$env:LAB_AUTOMATION_PATH="C:\path\to\your\lab_automation"
uvicorn main:app --reload
```

**What Happens in Real Mode?**
1.  **Process Management**: On startup, the backend launches two subprocesses (ports 9999 & 10000) to manage the table cameras via the `lab_automation` library.
2.  **Scan-First Initialization**: The system calls `experiment.scan_components()` to discover what is actually on the table using the ceiling camera.
3.  **Live Video**: 
    *   **Ceiling**: Streams MJPEG video via `/api/video-feed/stream`.
    *   **Table**: Provides on-demand high-res captures via `/api/table-cam/capture`.
4.  **Real Execution**: `MOVE` commands map to `experiment.place_component(...)`. `OPTIMIZE` commands trigger feedback loops (Newton).

### 3. Experiment Workflow
1.  **Drag & Drop**: Move components from the inventory to the table.
2.  **Interact**: Click a component to open the context popup. Move it precisely or run an **Optimization Strategy** (e.g., Newton).
3.  **Visualize**: The UI overlays the **Laser Path** (loaded from `laser_line_fit.npy` in Real Mode) to show the predicted beam trajectory.
4.  **Record Recipe**: Open the "Recipe Editor" (Sidebar), click "Record", perform actions, and "Save".
5.  **Run Recipe**: Click the "Play" button on a saved recipe to re-execute the sequence.
6.  **Check Drift**: Go to the **Debugger**, view "Golden States", and compare with the current Lab State.

---

## 🛠 Adding New Components
To add new physical components to the system:
1.  **Tag It**: Attach an ArUco tag to the physical object.
2.  **Catalog It**: Add an entry to `schemas/component_catalog.json` with the corresponding `tag_id` (e.g., `tag_22`) and physical properties.
3.  **Restart**: Restart the backend. The system will now recognize and scan for this component.
