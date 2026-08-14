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

For the radial MuJoCo simulation used by Cloud Labs, run:

```powershell
.\scripts\ops\run_simulation_edge_radial.ps1 -Viewer `
    -JointSpeedDegPerSec 30 -PlaybackRate 1 -ViewerFps 30
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
