# mock_edge/cloudlabs_edge — filled Edge Contract face

This folder is the **only** surface cloud-labs / Twin / SDK should talk to for
the teaching mock. It matches `cloudlabs-edge init` layout; every adapter is
implemented against `mock_edge.host` (sibling package under `src/mock_edge/`).

```text
mock_edge/
  cloudlabs_edge/     ← you are here (Edge Contract HTTP)
  src/mock_edge/host/ ← teaching physics (not the public face)
  lab_view/           ← catalog / state bundle
```

| Skeleton piece | Mock fill |
|----------------|-----------|
| `adapters/*` | UC dispatch + live/teleop session state |
| `latch.py` | Software epochs (`software_approx`) |
| `kernel_host.py` | Deterministic BGR mean stub |
| `dispatch.py` | Same primitive map as init (async-aware) |
| `server/app.py` | Thin HTTP over dispatch |

```powershell
python -m mock_edge --port 8100
cloudlabs-edge certify http://127.0.0.1:8100 --path mock_edge/cloudlabs_edge --profile stub
```
