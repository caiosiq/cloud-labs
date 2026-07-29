# Cloud Labs Edge Contract v1

Machine-readable southbound boundary between the **coordinator** (cloud-labs)
and an **edge agent** (mock process or `cloudlabs_edge/` in a lab repo).

**Normative design:** [`docs/EDGE_CONTRACT_AND_UC_LIVE_PLANE.md`](../../../docs/EDGE_CONTRACT_AND_UC_LIVE_PLANE.md)  
**Migration order:** [`docs/EDGE_UC_MIGRATION_ROADMAP.md`](../../../docs/EDGE_UC_MIGRATION_ROADMAP.md)

`contract_version` for this folder: **`1.0.0`**.

## Endpoints (edge process)

| Method | Path | Schema |
|--------|------|--------|
| `GET` | `/capabilities` | [`capabilities.schema.json`](./capabilities.schema.json) |
| `GET` | `/bench` | [`bench.schema.json`](./bench.schema.json) |
| `GET` | `/library` | [`library.schema.json`](./library.schema.json) — full lab component library |
| `GET` | `/inventory` | [`inventory.schema.json`](./inventory.schema.json) — declared tracked tags + placement |
| `POST` | `/execute` | request [`execute_request.schema.json`](./execute_request.schema.json), response [`execute_response.schema.json`](./execute_response.schema.json) |
| WS | path from capabilities (`teleop` channel) | client→edge [`teleop_ws_client.schema.json`](./teleop_ws_client.schema.json), edge→client [`teleop_ws_server.schema.json`](./teleop_ws_server.schema.json) |
| stream | path from capabilities (`live_feed` / measurable wire) | JPEG/MJPEG bytes; not JSON-schema’d |

Latch / observe payloads (inside execute results or dedicated observe bodies):
[`epoch_packet.schema.json`](./epoch_packet.schema.json).

Measurable catalog fields for wire + live channel binding:
[`measurable_live_decl.schema.json`](./measurable_live_decl.schema.json).

Library / inventory design (active-tags as a derived view, `LOCALIZE_COMPONENTS`):
[`docs/EDGE_LIBRARY_AND_INVENTORY.md`](../../../docs/EDGE_LIBRARY_AND_INVENTORY.md).


## UC rules (non-negotiable)

1. Mutations are **primitives** only (`POST /execute`).
2. Live video is the **wire** form of a declared measurable, armed by `START_LIVE_FEED`.
3. TeleOp pose feedback is **flat live tunable samples**, armed by `START_TELEOP`.
4. Kernel eval is **`EVAL_KERNEL` via `/execute`** — no `/kernel/probe` in v1.
5. Tier A WebSocket messages are **flat** — no nested tunable trees on the hot path.

## Locked product decisions (v1)

See design doc §12: coordinator stream proxy by default, bench monotonic `epoch_ms`,
hardware-aware latch, Option A tunable live samples.
