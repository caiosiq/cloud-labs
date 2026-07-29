# Edge library and inventory (v1 sketch)

Companion to the Edge Contract. **Instance data lives on the lab edge**; Cloud Labs
owns only the language (types, measurable schemas, primitive names).

Normative JSON Schema:

- [`schemas/edge_contract/v1/library.schema.json`](../schemas/edge_contract/v1/library.schema.json)
- [`schemas/edge_contract/v1/inventory.schema.json`](../schemas/edge_contract/v1/inventory.schema.json)

Related primitive: **`LOCALIZE_COMPONENTS`** — measure poses for declared tags
(deathray: `scan_components_cloudlab`).

---

## Files on the edge

```text
cloudlabs_edge/data/
  library.json      # full library (every part this lab owns)
  inventory.json    # which tags are currently tracked + declared placement
  recipes/          # bench-scoped recipes (same cut, later wiring)
  control/          # bench-scoped control repos (same cut, later wiring)
```

On-disk documents omit `backend_id`. HTTP `GET /library` and `GET /inventory`
may stamp `backend_id` like `/bench`.

---

## Decision: no separate `active_catalog.json` on the edge

Today’s coordinator `active_catalog.json` is only a list of tag ids. On the edge
that list is **exactly the key set of `inventory.json`**.

| API (coordinator, kept for Twin/SDK) | Derived from |
|--------------------------------------|--------------|
| `GET /api/catalog/library-rows` | `library.json` (all components) |
| `GET /api/catalog/active-tags` → `tag_ids` | `Object.keys(inventory.entries)` |
| `GET /api/catalog/active-tags` → `library_tag_ids` | `Object.keys(library.components)` |
| `GET /api/catalog` | library rows whose `tag_id` ∈ inventory keys |

So:

- **In library, not in inventory** — owned by the lab, not currently tracked (sidebar library only).
- **In inventory** — tracked; Twin draws / scripts can move / localize by default.
- **In inventory with `placement: "off"`** — still active/tracked, but declared off-table (not a default localize target unless `localize: true` is forced via `tag_ids`).

Validation rule: every inventory key **must** exist in `library.json`. Extra library
keys are allowed.

---

## `library.json` (sketch)

Same mental model as today’s `component_library.json` v1:

```json
{
  "schema_version": 1,
  "components": {
    "tag_22": {
      "id": "cam_gripper_1",
      "type": "OPTICAL_CAMERA",
      "tag_id": "tag_22",
      "name": "Gripper Camera 1",
      "parameters": { "hardware_binding": { "..." : "..." } },
      "capabilities": {
        "primitives": ["RECORD_MEASURABLES", "SET_EXPOSURE", "START_LIVE_FEED", "..."],
        "statecontrol": { "tunables": {}, "measurables": {} },
        "telemetry": {}
      }
    }
  }
}
```

Cloud Labs continues to validate widgets/primitives against the language registry;
the edge only authors instances.

---

## `inventory.json` (sketch)

```json
{
  "schema_version": 1,
  "entries": {
    "tag_22": {
      "placement": "table",
      "storage_slot": null,
      "localize": true
    },
    "tag_9": {
      "placement": "storage",
      "storage_slot": { "i": 0, "j": 1 },
      "localize": true
    },
    "tag_50": {
      "placement": "off",
      "localize": false,
      "notes": "spare; not on bench this week"
    }
  }
}
```

### `LOCALIZE_COMPONENTS` default scope

When `args.tag_ids` is omitted, the edge should localize tags where:

- the tag is in `inventory.entries`, and
- `localize` is not `false` (default **true**), and
- `placement` is `"table"` or `"storage"`.

Explicit `tag_ids` may include `"off"` entries if the caller insists.

---

## Edge HTTP (target)

| Method | Path | Body schema |
|--------|------|-------------|
| `GET` | `/library` | `library.schema.json` |
| `GET` | `/inventory` | `inventory.schema.json` |
| `POST` | `/execute` `{ "primitive": "LOCALIZE_COMPONENTS", "args": { ... } }` | execute request/response |

Coordinator keeps existing Twin URLs and **proxies** library/inventory from the edge
when `edge.base_url` is set.

---

## Migration from today’s mock `lab_view/`

| Today (coordinator disk) | Tomorrow (edge) |
|--------------------------|-----------------|
| `component_library.json` | `data/library.json` |
| `active_catalog.json` | **deleted as source of truth**; derived from `data/inventory.json` keys |
| (implicit “everything active is on table”) | explicit `placement` per tag |
| `recipes/`, `control/` | `data/recipes/`, `data/control/` (same cut, wire later) |

Mock can keep serving in-process from files that physically sit under
`mock_edge/cloudlabs_edge/data/` so ownership matches deathray.
