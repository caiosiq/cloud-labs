# Remote Access Roadmap

This roadmap details the steps required to enable secure remote control of the Optical Digital Twin.

## 1. Server Work (Repository Changes)
**Goal:** Harden the application for remote access and optimize bandwidth usage.

*   [ ] **Authentication Middleware**
    *   Add `fastapi.security` utilities to `backend/main.py`.
    *   Create a dependency that checks for a valid `ACCESS_KEY` (via Cookie or Header).
    *   Implement a `/login` HTML page for browser access.
    *   Protect sensitive routes (`/api/command`, `/api/components`, `/api/recipes/*`).

*   [ ] **Adaptive Video Stream**
    *   Modify `/api/video-feed/stream` to accept `fps` and `quality` query parameters.
    *   Update `RealLabCommunicator.get_video_stream()` to respect these parameters (e.g., skip frames, downscale resolution using `cv2.resize`).
    *   Update `frontend/app.js` to allow the user to toggle between "High Quality" (Alignment) and "Low Latency" (Monitoring) modes.

*   [ ] **Connection Health Indicator**
    *   Add a simple "Ping" endpoint or use the existing polling to measure round-trip time (RTT).
    *   Display a "Connection Quality" icon in the UI (Green/Yellow/Red).

## 2. Homework To-Dos (External Configuration)
**Goal:** Establish the secure tunnel and configure the physical machines.

### Step 1: Account Setup
*   [ ] **Create Tailscale Account**: Go to [tailscale.com](https://tailscale.com) and sign up (using Google/GitHub/Microsoft).

### Step 2: Lab Computer Setup (The Server)
*   [ ] **Install Tailscale**: Download and install Tailscale for Windows/Linux on the Lab PC.
*   [ ] **Log In**: Open the app and log in with your account.
*   [ ] **Disable Key Expiry** (Recommended):
    *   Go to the [Tailscale Admin Console](https://login.tailscale.com/admin/machines).
    *   Find the Lab machine -> Click "..." -> "Disable Key Expiry". (Prevents disconnection after 180 days).
*   [ ] **Enable Unattended Access**: Ensure Tailscale starts on boot so you can access it even if the PC restarts.
*   [ ] **Set Environment Variables**:
    *   Set `ACCESS_KEY` to a strong password.
    *   Set `LAB_MODE="REAL"`.
    *   Set `LAB_AUTOMATION_PATH` to your library location.

### Step 3: Home Computer Setup (The Client)
*   [ ] **Install Tailscale**: Install the client on your personal laptop/desktop.
*   [ ] **Log In**: Use the *same* account as the Lab PC.
*   [ ] **Verify Connection**:
    *   Open a terminal/PowerShell.
    *   Find the Lab IP in the Tailscale app (starts with `100.x.y.z`).
    *   Run `tailscale ping <LAB_IP>`.
    *   *Success:* You see "pong from ... via <direct/DERP>".
    *   *Note:* If it says "via DERP", latency might be higher. If "via <IP:PORT>", it's a direct P2P connection (Ideal).

### Step 4: End-to-End Test
*   [ ] **Start Server**: Run `uvicorn main:app --host 0.0.0.0` on the Lab PC.
*   [ ] **Connect**: Open `http://<LAB_TAILSCALE_IP>:8000` on your Home PC browser.
*   [ ] **Login**: Enter the `ACCESS_KEY`.
*   [ ] **Video Check**: Verify the stream loads. Toggle "Low Quality" if it lags.
