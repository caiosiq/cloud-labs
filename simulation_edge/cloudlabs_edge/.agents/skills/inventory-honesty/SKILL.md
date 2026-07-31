---
name: inventory-honesty
description: >-
  Keeps store, place, inventory, and runtime-sync primitives honest on a Cloud
  Labs edge. Use when implementing STORE/PLACE, RECORD_TUNABLES, SYNC_RUNTIME,
  LOCALIZE_COMPONENTS (alias), or any claim about where components live / what
  tunables are.
---

# Inventory honesty

## Do

- Treat `data/library.json` + `data/inventory.json` as the source of truth for
  what the lab owns and what is currently tracked (`GET /library`, `GET /inventory`).
- Implement inventory verbs only when the lab stack can actually track storage.
- Until then, keep scaffold `NotImplementedError` → `refused` / `NOT_IMPLEMENTED`.
- Prefer confirming holding/presence from real session + gripper/vision state over guesses.
- Document in capabilities what you truly support; do not advertise inventory you cannot enforce.
- Declare per-tunable metadata in the library (authored JSON — doctor/load
  **fail hard**; nothing silently backfills missing capabilities):
  - `recordable: true` → `RECORD_TUNABLES` may overwrite from the world
    (pose writes **`nominal_pose`**, never a shadow `reported_pose`).
  - `recordable: false` → **`set_at_init` is required**; SYNC sets that value.
- When `features.runtime_sync` is true, `RECORD_TUNABLES` + `SYNC_RUNTIME` must
  be in `supported_primitives`, and every non-`nominal_pose` tunable must declare
  `recordable` explicitly (doctor strict mode).
- Wire `RECORD_TUNABLES` (and `SYNC_RUNTIME` = RECORD(recordable) + SET(set_at_init)
  for inventory tags). Edge is not READY until SYNC completes.
- Mock/sim may implement RECORD as copy-from-state/JSON; real edges must measure.
- `LOCALIZE_COMPONENTS` is a **deprecated alias** of RECORD for `nominal_pose` only;
  prefer RECORD_TUNABLES / SYNC_RUNTIME. Default inventory scope: `localize != false`
  and `placement` in `{table, storage}`.

## Do not

- Do not fake "stored" / "placed" success to make certify green.
- Do not invent slot maps or AprilTag locations without a source of truth.
- Do not silently no-op STORE/PLACE — refusal is better than a lie.
- Do not keep a separate edge `active_catalog.json`; active tags are inventory keys.
- Do not invent `reported_*` shadow tunables; recording overwrites the real tunable.
- Do not advertise READY / accept motion before SYNC_RUNTIME has succeeded.
- Do not silently backfill / infer missing library `capabilities` or `recordable`
  metadata at load time — fix the authored JSON so doctor fails for the real cause.

## Related

- Holding confirmation (e.g. `CONFIRM_HOLDING_TAG`) may use session state + gripper
  when that is all the hardware exposes — say so in the result (`source` field).
- Design brief: Cloud Labs `docs/RECORD_TUNABLES_AND_SYNC_RUNTIME.md`.
