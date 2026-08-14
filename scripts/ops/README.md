# Ops scripts (server / platform)

Platform tooling for the Cloud Labs coordinator, edge agent, backends, and fixtures.
Authoring demos live in [`../language/`](../language/). Operator bench scripts: [`../bench/`](../bench/).

| Script | Purpose |
|--------|---------|
| `run_mock_backend.py` | Start teaching Edge Contract edge (`python -m mock_backend`) |
| `mock_backend_agent.py` | Poll-attach mock backend (jobs + command proxy); prefer `run_mock_backend` |
| `create_lab_communicator.py` | **Retired** — use `cloudlabs-edge init` or extend `mock_backend/` |
| `build_torchscript_kernels.py` | Rebuild approved TorchScript under `schemas/kernels/` |
| `audit_primitives.py` | Print primitive handler coverage (mock_backend host) |
| `edge_conformance.py` | Run Edge Contract conformance against a URL |
| `generate_laser_line_fit.py` | Write repo-root `laser_line_fit.npy` for the UI |
| `inspect_radial_carry_poses.ps1` | Interactively display radial carry poses at a selectable radius and theta, including safety-rejected poses, in MuJoCo |
| `extend_radial_unsafe_poses.py` | Extend a radial JSON's diagnostic-only `unsafe` radii with joint poses at every library Z level; executable samples and safety rules are unchanged |

```powershell
$env:PYTHONPATH="backend;mock_backend/src"
python -m mock_backend --port 8100
python scripts/ops/mock_backend_agent.py
python scripts/ops/audit_primitives.py
python scripts/ops/edge_conformance.py http://127.0.0.1:8100 --profile stub
```
