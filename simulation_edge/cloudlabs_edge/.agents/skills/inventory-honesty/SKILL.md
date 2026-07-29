---
name: inventory-honesty
description: >-
  Keeps store, place, and inventory primitives honest on a Cloud Labs edge.
  Use when implementing STORE_COMPONENT, PLACE_FROM_STORAGE, AFFIRM, REPACK,
  RECENTER, REMOVE, or any inventory claim about where components live.
---

# Inventory honesty

## Do

- Treat `data/library.json` + `data/inventory.json` as the source of truth for
  what the lab owns and what is currently tracked (`GET /library`, `GET /inventory`).
- Implement inventory verbs only when the lab stack can actually track storage.
- Until then, keep scaffold `NotImplementedError` → `refused` / `NOT_IMPLEMENTED`.
- Prefer confirming holding/presence from real session + gripper/vision state over guesses.
- Document in capabilities what you truly support; do not advertise inventory you cannot enforce.
- Wire `LOCALIZE_COMPONENTS` to a real scan (`scan_components_cloudlab` or equivalent);
  default scope is inventory tags with `localize != false` and `placement` in
  `{table, storage}`.

## Do not

- Do not fake "stored" / "placed" success to make certify green.
- Do not invent slot maps or AprilTag locations without a source of truth.
- Do not silently no-op STORE/PLACE — refusal is better than a lie.
- Do not keep a separate edge `active_catalog.json`; active tags are inventory keys.

## Related

- Holding confirmation (e.g. `CONFIRM_HOLDING_TAG`) may use session state + gripper
  when that is all the hardware exposes — say so in the result (`source` field).
