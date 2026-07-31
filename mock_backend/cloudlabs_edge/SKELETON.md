# mock_backend/cloudlabs_edge — filled Edge Contract face

This folder is the **only** surface cloud-labs / Twin / SDK should talk to for
the teaching mock. It matches `cloudlabs-edge init` layout; every adapter is
implemented against `mock_backend.host` (sibling package under `src/mock_backend/`).

```text
mock_backend/
  cloudlabs_edge/     ← you are here (Edge Contract HTTP)
  src/mock_backend/host/ ← teaching physics (not the public face)
  lab_view/           ← catalog / state bundle
```

| Skeleton piece | Mock fill |
|----------------|-----------|
| `adapters/*` | UC dispatch + live/teleop session state |
| `latch.py` | Software epochs (`software_approx`) |
| `kernel_host.py` | Deterministic BGR mean stub |
| `dispatch.py` | Same primitive map as init (async-aware) |
| `server/app.py` | Thin HTTP over dispatch |
| `.agents/skills/*` | Phase 6 coaching (same files as `cloudlabs-edge init`) |

Phase 6 coaching for humans or any AI assistant lives under
[`.agents/README.md`](.agents/README.md).

```powershell
python -m mock_backend --port 8100
cloudlabs-edge certify http://127.0.0.1:8100 --path mock_backend/cloudlabs_edge --profile stub
```
