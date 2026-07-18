# mock_edge — filled Edge Contract skeleton

This package follows the same layout as `cloudlabs-edge init` (`adapters/`,
`dispatch.py`, `latch.py`, `kernel_host.py`, `contract.py`, `capabilities.json`).
Unlike a Phase-5 lab skeleton, every adapter function here is **implemented**
against the teaching host in `mock_edge.host`.

| Skeleton piece | Mock fill |
|----------------|-----------|
| `adapters/*` | UC dispatch + live/teleop session state |
| `latch.py` | Software epochs (`software_approx`) |
| `kernel_host.py` | Deterministic BGR mean stub |
| `dispatch.py` | Same primitive map as init (async-aware) |
| `server/app.py` | Thin HTTP over dispatch |
| `host/` | Teaching physics (lab-automation stand-in) |

Run: `python -m mock_edge --port 8100` then `cloudlabs-edge certify http://127.0.0.1:8100 --profile stub`.
