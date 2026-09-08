# simulation_edge

MuJoCo-backed simulation lab for Cloud Labs, structured like `mock_backend`:

```text
simulation_edge/
  cloudlabs_edge/          # Edge Contract face (certify / EdgeClient URL)
  src/simulation_edge/     # host physics (soft pose + optional MuJoCo process)
  lab_view/                # layout / catalog / state bundle
  simulation_profiles/     # component geometry profiles (from develop/josh)
  third_party/mujoco_menagerie/ufactory_xarm7/
```

## Modes

| Mode | How | Notes |
|------|-----|--------|
| **Soft** (default) | `python -m simulation_edge` | In-memory poses; no `mujoco` install required. Stub certify passes. |
| **MuJoCo** | `SIMULATION_EDGE_MUJOCO=1` + `pip install -e ".[mujoco]"` | Spawns Josh’s viewer process; needs menagerie XML under `third_party/`. |

Env knobs (MuJoCo mode): `CLOUDLAB_MUJOCO_VIEWER`, `CLOUDLAB_MUJOCO_REALTIME`, `CLOUDLAB_SIM_PROFILE`, `MUJOCO_XARM7_XML`.

Radial motion uses the assisted component weld by default after the physical
two-finger grasp has been validated. Set
`CLOUDLAB_MUJOCO_ASSISTED_WELD=0` before starting the simulation edge to run
the same radial planner with physical gripper contact only. Accepted boolean
values are `1/0`, `true/false`, `yes/no`, and `on/off`.

The radial runtime rests at the experimental xArm home joint target
`[180, 75, -180, 20, 0, 90, -60]` degrees. Each component move transitions
from physical home to radial observation home before planning the pickup, and
returns through radial observation home to physical home after placement.

For the radial MuJoCo simulation used by Cloud Labs, run:

```powershell
.\scripts\ops\run_simulation_edge_radial.ps1 -Viewer `
    -JointSpeedDegPerSec 30 -PlaybackRate 4 -ViewerFps 15
```

This launcher selects `radial_motion_libraries/optical_housings_noninverted.json`
by default. Use `-Library original` to run the previous radial library without
renaming or overwriting either JSON file. `-JointSpeedDegPerSec` sets the radial
joint-motion target directly; use a smaller value for slower movement or a larger
value for faster movement. `-PlaybackRate` independently controls how quickly
MuJoCo simulation time passes relative to wall-clock time. For example,
`-PlaybackRate 2` plays the unchanged 30-deg/s simulated trajectory in half the
real-world time. `-ViewerFps` controls rendering frequency independently; a
lower value leaves more wall-clock time for physics at high playback rates.
The command above uses the workstation-tested fast settings: 4x playback and a
15 FPS viewer, while leaving the planned 30-deg/s joint trajectory unchanged.

### High-resolution PNG capture

Focus the MuJoCo viewer and press `F8` to save a lossless render of the current
simulation state using the current free-camera view. The default image is
3840x2160 and is written to `simulation_edge/captures/`. The shortcut writes
only the PNG image; it does not create a JSON sidecar.

Press `F9` in the MuJoCo viewer to enter photo pause. Physics and the active
robot trajectory freeze at the exact current simulation state, while camera
pan, orbit, zoom, and `F8` high-resolution capture remain available. Press
`F9` again to resume the same movement from the frozen state.

Press `F10` to toggle labels for lab components only. Label text comes from the
component catalog (for example, `ND Filter` and `Beam Block`); robot joints and
links are not included.

To measure the live 3D center-to-center distance between two components,
double-click the first component and press `F11`, then double-click the second
component and press `F11`. The viewer draws a ruler line labeled in millimetres;
it follows both components as they move. Repeat those steps to keep adding
rulers. Press `F12` to clear all measurements at once.
MuJoCo's built-in `P` shortcut only writes a window-resolution `screenshot.png`;
use `F8` for the paper-quality render.

The viewer and capture start with the paper settings: active headlight with
ambient `0.2 0.2 0.2`, diffuse `0.5 0.5 0.5`, and specular `0.1 0.1 0.1`;
perspective FOV 45; center `0 0 0`; azimuth 135; elevation -10. Reflection,
Haze, and Cull Face are enabled, while the other OpenGL effects are disabled.
The optical component assemblies use cool light-gray RGBA `0.55 0.58 0.64 1`
so their housing edges remain readable against the table and background.
Model textures are disabled in the viewer and `F8` output. The table uses a
solid blue-gray RGBA `0.28 0.34 0.42 1`, and the default capture background is
the lighter blue-gray `#8FA2B5` so the black gripper remains visible.

Optional overrides, set before starting Terminal 1:

```powershell
$env:CLOUDLAB_MUJOCO_CAPTURE_WIDTH = "3840"
$env:CLOUDLAB_MUJOCO_CAPTURE_HEIGHT = "2160"
$env:CLOUDLAB_MUJOCO_CAPTURE_DIR = (Resolve-Path ".").Path + "\simulation_edge\captures"
$env:CLOUDLAB_MUJOCO_CAPTURE_BACKGROUND = "#8FA2B5"
```

`CLOUDLAB_MUJOCO_CAPTURE_BACKGROUND` is the solid color substituted for empty
background pixels in the `F8` PNG; it does not use or require a skybox. Any
six-digit RGB hex color is accepted.

With the Twin UI browser tab focused, `F8` (or `Fn+F8`) saves labeled and
text-free 4000x2800 table-layout PNGs into the same
`simulation_edge/captures/` directory. These browser captures are saved by the
local backend and do not create browser downloads or JSON files.

The noninverted planner uses the CAD-derived clear frame opening (46.5 by
48.0 inches). Radial posture lookup remains one-dimensional, but runtime
safety is analytical in `(radius, theta)`: the TCP axis must remain one
3.5-inch tool/camera radius plus `frame_safety.clearance_mm` (3 inches by
default) inside the frame. A transfer rotates before extending and retracts
before rotating; when a theta sweep crosses a tighter frame direction, it
automatically retracts to the tightest legal sweep radius first. Diagnostic
outer-library poses remain non-executable unless the realized pose and every
interpolated path sample pass these runtime checks.

## Run

```powershell
$env:PYTHONPATH="backend;simulation_edge/src;packages/cloudlabs_edge_dev/src"
pip install -e ./packages/cloudlabs_edge_dev
pip install -e ./simulation_edge

cloudlabs-edge doctor --path simulation_edge/cloudlabs_edge
python -m simulation_edge --port 8120
cloudlabs-edge certify http://127.0.0.1:8120 --path simulation_edge/cloudlabs_edge --profile stub
```

`backend_id`: **`sim.default`**. Set `edge.base_url` in `schemas/backends.json` when serving HTTP.

v1 physically implements **`MOVE_COMPONENT`** (soft or MuJoCo). Live / teleop / observe are soft contract stubs so certify and coordinator wiring work without a full optical stack.
