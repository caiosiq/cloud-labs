# cloudlabs-edge-dev

Developer kit for **Cloud Labs Edge Contract v1**.

- Scaffold a `cloudlabs_edge/` folder for a lab repo
- **Doctor** — local kit + tree readiness (no live edge)
- **Check / certify** — live probe packets against a running edge before you add it to the coordinator

Schemas: `schemas/edge_contract/v1/` in the cloud-labs monorepo.

## Install

From the cloud-labs repo root:

```powershell
pip install -e ./packages/cloudlabs_edge_dev
```

## Scaffold (Phase 5 — lab repo language skeleton)

From the **lab** repo root (e.g. `robot-deathray`), using this kit:

```powershell
pip install -e <path-to-cloud-labs>/packages/cloudlabs_edge_dev
cloudlabs-edge init ./cloudlabs_edge --backend-id real.default
```

That creates a **full function skeleton** (not a 2-function stub):

```text
cloudlabs_edge/
  main.py, capabilities.json, contract.py, latch.py, kernel_host.py, dispatch.py
  SKELETON.md          # checklist: function → deathray target
  bench/layout.json
  adapters/
    motion.py          # MOVE_*, PICK, HOVER, PLACE, storage, REMOVE
    motors.py          # MOVE_MOTOR, setpoints, zero, home
    vision.py          # BGR + JPEG
    live_feed.py       # START/END_LIVE_FEED
    teleop.py          # START/END + JOG/GOTO + pose_sample
    tunables.py        # SET_EXPOSURE, SET_LASER_OUTPUT
    optimize.py        # OPTIMIZE
    observe.py         # RECORD_MEASURABLES, EVAL_KERNEL
```

`capabilities.json` includes `execution_threads` with **`arm.0` + `sense.0` only**.
Do not advertise bare `motor.<n>` columns — motor ids are per-tag and the
coordinator creates `motor.<tag_id>.<motor_id>` on demand
(`docs/COMMAND_MATRIX.md`).

Phase 5 HTTP still uses `stub_server.create_app` (no OpticalExperiment imports). Every adapter function raises `NotImplementedError` until Phase 6.

```powershell
cd cloudlabs_edge
uvicorn main:app --host 0.0.0.0 --port 8100
cloudlabs-edge doctor --path .
cloudlabs-edge certify http://127.0.0.1:8100 --path . --profile skeleton
```

## Doctor (local, no HTTP)

Inspect the installed kit, contract schemas, and an optional `cloudlabs_edge/` tree:

```powershell
cloudlabs-edge doctor
cloudlabs-edge doctor --path ./cloudlabs_edge
cloudlabs-edge doctor --path ./cloudlabs_edge --json
```

Checks include:
- kit version vs monorepo `pyproject.toml` (when run inside cloud-labs)
- Edge Contract schemas discoverable
- scaffold files, `contract_version` pin, capabilities/bench schema
- warn if adapters are still Phase-5 `NotImplementedError` placeholders

## Conformance (live packets)

```powershell
# Terminal A — reference stub (passes stub profile)
cloudlabs-edge serve-stub --port 8100

# Terminal B
cloudlabs-edge check http://127.0.0.1:8100 --profile stub
# or:
python scripts/ops/edge_conformance.py http://127.0.0.1:8100 --profile stub
```

Profiles:

| Profile | Meaning |
|---------|---------|
| `stub` | Schema + refuse unknown; safe START/END live+teleop; RECORD/EVAL return `epoch_ms`; flat WS only |
| `skeleton` | Capabilities + bench + refuse unknown (motion may refuse) |
| `hardware` | Stub checks plus expects real motion primitives not to no-op (Phase 6+) |

## Certify (pre-register gate)

Run **doctor + check** and emit a machine-readable report **before** wiring `edge.base_url` into cloud-labs:

```powershell
cloudlabs-edge serve-stub --port 8100

cloudlabs-edge certify http://127.0.0.1:8100 `
  --path ./cloudlabs_edge `
  --profile stub `
  --out ./edge-certify.json
```

Exit code `0` only when both doctor and conformance pass. Adapter placeholders produce **warnings** (do not fail doctor) so Phase-5 skeletons can certify on the stub profile; hardware labs should clear those warnings before `--profile hardware`.

Use `--skip-doctor` when you only have a remote URL and no local tree.

## Design docs

- `docs/EDGE_CONTRACT_AND_UC_LIVE_PLANE.md`
- `docs/EDGE_UC_MIGRATION_ROADMAP.md`
