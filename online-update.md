# Remote Access & Security Strategy

To operate the **Optical Digital Twin** from a remote location (Home) while the server runs in the laboratory (Lab), we need a solution that bridges the network gap securely without exposing the robot to the public internet.

## 1. The Network Challenge

University laboratories are typically behind strict firewalls and NAT (Network Address Translation). This means the Lab PC does not have a public IP address you can connect to directly.

**Requirements:**

* **Traversal**: Must work through the university firewall.
* **Security**: The connection must be encrypted.
* **Access Control**: Only *you* should be able to access it.
* **Performance**: Low latency is critical for robot control and video streaming.

## 2. Proposed Solution: Mesh VPN (Tailscale)

We recommend using **Tailscale** (built on WireGuard). It creates a private, encrypted mesh network between your devices.

### Why Tailscale?

* **Zero Config**: It handles NAT traversal automatically (no router port forwarding needed).
* **P2P Connection**: It attempts to establish a direct peer-to-peer connection between Home and Lab, minimizing latency (crucial for video).
* **Security**: Only devices logged into your Tailscale account can see the server. It is effectively "invisible" to the internet.

### Setup Guide
1.  **Lab Side**:
    *   Install Tailscale on the Lab PC.
    *   Log in with your account.
    *   (Optional) Enable "Unattended Access" so it stays connected even if the PC reboots/locks.
2.  **Home Side**:
    *   Install Tailscale on your Home PC.
    *   Log in with the same account.
3.  **Access**:
    *   Tailscale assigns a stable IP (e.g., `100.x.y.z`) or a hostname (e.g., `optics-lab-pc`) to the Lab machine.
    *   Open `http://optics-lab-pc:8000` in your home browser.
4.  **Verification (P2P vs Relay)**:
    *   Run `tailscale ping <lab-ip>` from home.
    *   Ideally, you want a direct P2P connection for low latency.
    *   If the university firewall is strict (Symmetric NAT), it might fallback to "DERP" relays. This works but increases latency.

## 3. Application-Level Security (Authentication)
Even inside the VPN, it is best practice to prevent unauthorized commands (e.g., if you leave your laptop open). We should implement a simple **Access Key** mechanism.

### Proposed Changes to `backend/main.py`
We will use FastAPI's built-in security utilities.

1.  **Environment Variable**: Set `ACCESS_KEY="my-secret-password"` on the Lab PC.
2.  **Implementation**:
    *   Use `fastapi.security.APIKeyCookie` to handle token extraction automatically.
    *   Create a dependency `async def get_current_user(api_key: str = Security(cookie_sec))` that verifies the key.
    *   Protect sensitive routes (e.g., `/api/command`) with this dependency.
    *   Unauthenticated requests will receive `401 Unauthorized` (API) or redirect to login (Browser).

### The Login Flow
1.  You open `http://optics-lab-pc:8000/`.
2.  Server sees no cookie -> Redirects to `login.html`.
3.  You enter the password.
4.  Frontend sends `POST /login { key: "..." }`.
5.  Server verifies key -> Sets `HttpOnly` cookie -> Redirects back to `/`.

## 4. Latency & Bandwidth Optimization
Remote video streaming (MJPEG) can be bandwidth-heavy (5-10 Mbps).

**Optimizations:**
1.  **Adaptive Framerate**:
    *   Add a query parameter to the video stream: `/api/video-feed/stream?fps=5&quality=low`.
    *   In the Frontend, add a "Connection Quality" toggle (High/Low).
    *   **Low Quality**: 5 FPS, 320x240 resolution (Good for monitoring).
    *   **High Quality**: 20 FPS, 640x480 resolution (Good for alignment).

2.  **State Synchronization (Future Upgrade)**:
    *   **Current**: Polling `lab_state.json` every 500ms is robust but adds HTTP overhead.
    *   **Future**: If remote lag is noticeable, migrate to **WebSockets**. This maintains a single open connection, significantly reducing latency for high-frequency updates.

## 5. Summary of Next Steps

1. **Install Tailscale** on both machines.
2. **Implement Auth**: Add the Login page and Middleware to `main.py`.
3. **Implement Video Options**: Allow downscaling the video stream for remote connections.
