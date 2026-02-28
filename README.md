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
*   **Simulation:** `MockLabCommunicator` simulates robot delays, noise, and optimization processes.

---

## 📂 Project Structure

```
optics-digital-twin/
├── backend/                # FastAPI Application
│   ├── main.py             # Entry point & API Routes
│   ├── lab_communicator.py # Hardware Abstraction Layer (Mock/Real)
│   └── ...
├── frontend/               # Static Web Assets
│   ├── index.html          # Main Interface
│   ├── debug.html          # Debugger & Data Visualizer
│   ├── app.js              # Core UI Logic
│   └── ...
├── schemas/                # JSON Data Stores
│   ├── lab_state.json      # Current Physical State
│   └── recipes/            # Saved Recipes & Golden States
├── README.md               # This file
└── ROADMAP.md              # Development Plan
```

---

## 🚀 Quick Start (Simulation Mode)

The system currently runs in **Mock Mode**, simulating a physical lab with delays and sensor noise.

### 1. Start the Backend
```bash
cd backend
# Install dependencies (fastapi, uvicorn)
pip install fastapi uvicorn
# Run the server
uvicorn main:app --reload
```
*Server will start at `http://localhost:8000`*

### 2. Access the Interface
Open your browser to:
*   **Main UI:** [http://localhost:8000/](http://localhost:8000/)
*   **Debugger:** [http://localhost:8000/debug](http://localhost:8000/debug)

### 3. Usage Flow
1.  **Drag & Drop:** Move components from the inventory to the table.
2.  **Interact:** Click a component to open the context popup. Move it precisely or run an **Optimization Strategy** (e.g., Newton).
3.  **Record Recipe:** Open the "Recipe Editor" (Sidebar), click "Record", perform actions, and "Save".
4.  **Run Recipe:** Click the "Play" button on a saved recipe to re-execute the sequence.
5.  **Check Drift:** Go to the **Debugger**, view "Golden States", and compare with the current Lab State.

---

## 🛠 Development
To switch to a real robot, implement a `RealLabCommunicator` class in `backend/lab_communicator.py` inheriting from `LabCommunicator`, and update `main.py` to use it.
