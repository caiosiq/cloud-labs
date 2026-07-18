# simulation_edge skeleton notes

Filled `cloudlabs-edge init` tree for **`sim.default`**.

## Implemented (v1)

- Stub-profile contract surface: live feed, teleop WS, record, eval kernel
- `MOVE_COMPONENT` → `SimulationHost` (soft poses by default; MuJoCo when `SIMULATION_EDGE_MUJOCO=1`)

## Not in v1

Inventory / motors / OPTIMIZE — omitted from `capabilities.json` (HTTP refuses as
`UNKNOWN_PRIMITIVE`). Adapter modules still keep the full init **Parameters /
Returns** docstrings and raise ``NotImplementedError`` so the intended surface
stays documented for later fill-in.

## Certify

```powershell
cloudlabs-edge doctor --path simulation_edge/cloudlabs_edge
cloudlabs-edge certify http://127.0.0.1:8120 --path simulation_edge/cloudlabs_edge --profile stub
```
