# Schemas

Reference JSON fixtures for API contracts and examples.

**Lab-specific data** (table bounds, laser lines, component library + active catalog, mock `lab_state.json`, recipes, **saved UI states** (`states/*.json`), motor rotations, stored-intent manifest) now lives under the directory pointed to by **`LAB_VIEW_PATH`** — see `backend/lab_communicator/mock/lab_view/` for the bundled mock bundle and `backend/lab_communicator/real/lab_view/default/` as a starting template for a physical lab.

Remaining files here are generic shapes (e.g. `client_payload.json`, `strategies.json`, recipe/state examples).

Per-component runtime shape (`statecontrol`, `telemetry`) is documented in **`backend/lab_model/README.md`** and **`backend/lab_model/ARCHITECTURE.md`**. Authoritative examples live under **`{LAB_VIEW_PATH}/lab_state.json`**.
