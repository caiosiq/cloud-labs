# Ops scripts (server / platform)

Platform tooling for the Cloud Labs coordinator, edge agent, backends, and fixtures.
Authoring demos live in [`../language/`](../language/).

| Script | Purpose |
|--------|---------|
| `run_mock_edge.py` | Start teaching Edge Contract edge (`python -m mock_edge`) |
| `mock_edge_agent.py` | Poll-attach mock edge (jobs + command proxy); prefer `run_mock_edge` |
| `create_lab_communicator.py` | **Retired** — use `cloudlabs-edge init` or extend `mock_edge/` |
| `build_torchscript_kernels.py` | Rebuild approved TorchScript under `schemas/kernels/` |
| `audit_primitives.py` | Print primitive handler coverage (mock_edge host) |
| `edge_conformance.py` | Run Edge Contract conformance against a URL |
| `generate_laser_line_fit.py` | Write repo-root `laser_line_fit.npy` for the UI |

```powershell
$env:PYTHONPATH="backend;mock_edge/src"
python -m mock_edge --port 8100
python scripts/ops/mock_edge_agent.py
python scripts/ops/audit_primitives.py
python scripts/ops/edge_conformance.py http://127.0.0.1:8100 --profile stub
```
