# Synchronization Protocol: Client-Server Communication

This document defines how the **Optics Digital Twin UI** (Client) and the **Lab Manager Backend** (Server) synchronize state.

## 1. Overview
The system follows a **Command-Query Separation (CQS)** pattern:
- **Queries (Reading State):** The Client *polls* the Server for the latest `lab_state.json`.
- **Commands (Writing Intent):** The Client *pushes* `client_payload.json` to the Server.

## 2. Reading State (Polling)
Since the robot moves slowly (seconds to minutes) and the lab network is local, simple HTTP polling is sufficient for Phase 1-3.

- **Endpoint:** `GET /lab-state`
- **Frequency:** Every 500ms - 1000ms.
- **Response:** Returns the full `lab_state.json`.
- **Client Logic:**
  - If `system_status` changes from `BUSY` to `IDLE`, the UI triggers a "Task Complete" notification.
  - The UI diffs the incoming JSON against the previous one to animate changes.

## 3. Writing Intent (Commands)
User actions are transactional.

- **Endpoint:** `POST /command`
- **Payload:** `client_payload.json`
- **Response:**
  - `202 Accepted`: Command received and queued.
  - `400 Bad Request`: Invalid parameters (e.g., target out of bounds).
  - `409 Conflict`: System is `BUSY`.
- **Client Logic:**
  - On success, the UI marks the Ghost Component as "Pending" (e.g., spinner).
  - The UI *does not* update the "Solid" component immediately. It waits for the next Poll cycle to see the physical change.

## 4. Future Upgrades (Phase 4+)
- **WebSockets:** For live video streaming and sub-100ms sensor feedback.
- **Optimistic Updates:** If network latency becomes an issue, the UI may predict the final state, but for a physical robot, showing "Reality" is safer than showing "Prediction".
