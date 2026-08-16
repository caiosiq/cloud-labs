# Schemas

Reference JSON fixtures for API contracts and examples.

**Lab-specific data** (table bounds, laser lines, component library + active catalog, mock `lab_state.json`, recipes, **saved UI states** (`states/*.json`), motor rotations, stored-intent manifest) now lives under the directory pointed to by **`LAB_VIEW_PATH`** — see [`mock_backend/lab_view/`](../mock_backend/lab_view/) for the teaching bundle. Physical labs ship their own bundle beside `cloudlabs_edge/`.

Also here: `backends.json`, approved `kernels/`, Edge Contract under `edge_contract/v1/`, and OPTIMIZE / objective-graph examples.

Per-component runtime shape (`statecontrol`, `telemetry`) is documented in **`backend/lab_model/README.md`** and **`backend/lab_model/ARCHITECTURE.md`**. Authoritative examples live under **`{LAB_VIEW_PATH}/lab_state.json`**.
