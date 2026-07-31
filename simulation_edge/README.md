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
