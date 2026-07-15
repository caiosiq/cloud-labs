# Ops scripts (server / platform)

Platform tooling for the Cloud Labs coordinator, edge agent, backends, and fixtures.
Authoring demos live in [`../language/`](../language/).

| Script | Purpose |
|--------|---------|
| `mock_edge_agent.py` | Second-process mock edge (jobs + command proxy) |
| `create_lab_communicator.py` | Scaffold a new `lab_communicator` backend |
| `build_torchscript_kernels.py` | Rebuild approved TorchScript under `schemas/kernels/` |
| `audit_primitives.py` | Print primitive handler coverage |
| `generate_laser_line_fit.py` | Write repo-root `laser_line_fit.npy` for the UI |

```powershell
$env:PYTHONPATH="backend"
python scripts/ops/mock_edge_agent.py
python scripts/ops/create_lab_communicator.py my_backend --with-lab-view
python scripts/ops/build_torchscript_kernels.py
python scripts/ops/audit_primitives.py
```
