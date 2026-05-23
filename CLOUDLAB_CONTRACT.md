# Cloud-Labs ↔ `lab_automation` Capability Contract

**Audience:** maintainers of the `lab_automation` repo.
**Scope:** describes the JSON catalog cloud-labs ships and the runtime
hooks `lab_automation` is expected to honor when invoked from cloud-labs.
**Authoritative spec:** `universal_component_architecture.md` (cloud-labs repo).

---

## 1. Why this document exists

Cloud-Labs and `lab_automation` are intentionally **decoupled repos**:

- `cloud-labs` (this repo) owns the **UI**, the **JSON state machine**,
  and the **catalog of components** (the *Capability Contract*).
- `lab_automation` owns **hardware drivers**: motors, cameras, the robot
  arm, optimization strategies, recorder/cam helpers.

Neither repo imports the other at runtime *except* in one direction:
cloud-labs constructs `OpticalExperiment` from `lab_automation` and
optionally passes the catalog. There is **no JSON schema in
`lab_automation`** — it reads the same catalog file cloud-labs ships.

Decision reference: §16.4 of `universal_component_architecture.md`
("Reading B — Catalog JSON is generated/edited in cloud-labs; both repos
read the same file at boot").

---

## 2. What `lab_automation` MUST implement

### 2.1. `OpticalExperiment` constructor signature

```python
class OpticalExperiment:
    def __init__(
        self,
        mock: bool = False,
        catalog: Optional[Dict[str, Any]] = None,  # NEW (Phase 5)
        ...
    ):
        ...
```

- `catalog=None` → preserves current behavior (hardcoded steppers,
  cameras, IPs). Existing callers outside cloud-labs are unaffected.
- `catalog=<dict>` → opt-in **capability-driven** mode. The dict is the
  fully-validated v1 document
  (`{"schema_version": 1, "components": {tag_id: {...}}}`)
  cloud-labs loaded from `component_library.json`.

Until this kwarg is accepted, cloud-labs falls back to the legacy
zero-arg constructor and logs a warning. See
`backend/lab_communicator/real/communicator.py::RealLabCommunicator.__init__`.

### 2.2. Fields `lab_automation` MUST read

For each entry in `catalog["components"]`:

| Field                              | Why hardware-side cares                                   |
|------------------------------------|-----------------------------------------------------------|
| `type`                             | Selects which `OpticalComponent` subclass to instantiate. |
| `tag_id`                           | Map key for all per-component routing.                    |
| `motor_ids`                        | Wiring to a `wifi_stepper` controller.                    |
| `motor_controller`                 | String key resolving to an IP-mapped controller.          |
| `capabilities.tunables[*].min/max/options/default/unit` | Input validation at the hardware boundary. |
| `capabilities.primitives`          | Gate which methods on this component are callable.        |

### 2.3. Fields `lab_automation` MUST ignore

| Field                                 | Why hardware-side ignores it             |
|---------------------------------------|------------------------------------------|
| `capabilities.tunables[*].widget`     | UI concern (cloud-labs picks the React widget). |
| `capabilities.measurables[*].widget`  | Same.                                    |
| `capabilities.telemetry[*].url`       | Cloud-labs serves the HTTP routes.       |
| `properties.*` (radius, coating, ...) | Display-only physical specs.             |

If `lab_automation` ever needs hardware-side configuration of a tunable
(e.g. an LED color), the catalog gains a hardware-relevant field — but
the `widget` key stays cloud-labs-only.

---

## 3. Validation expectations (§15)

Cloud-labs runs `validate_catalog_v1()` at boot and refuses to start on
hard-fail (`schema_version` unsupported, missing required field, unknown
primitive, widget descriptor missing required keys). Hardware side
should mirror **the input-validation half**:

| Where                          | What is validated                                              |
|--------------------------------|----------------------------------------------------------------|
| `lab_automation` boot          | Each catalog entry maps to a known `OpticalComponent` subclass. |
| `lab_automation` per request   | Tunable updates outside catalog's declared `min`/`max`/`options` rejected at the hardware boundary. |

Both repos must reject the same inputs for the same reasons.

---

## 4. Adding a new component (the full lifecycle)

1. **Cloud-labs** — edit `component_library.json` (or run the migration
   script with a new entry already added). Declare:
   - `type`, `tag_id`, `motor_ids`/`motor_controller` if applicable,
     `properties.*`,
   - and a `capabilities` block (tunables + measurables + telemetry +
     primitives). Use existing entries as templates; refer to
     `universal_component_architecture.md` §13 for the schema.
2. **`lab_automation`** — if the `type` is brand-new, add the
   `OpticalComponent` subclass and register it in the type-to-class
   dispatch. Existing types need no hardware-side work.
3. **Boot cloud-labs** — `validate_catalog_v1()` either passes (new
   component is fully usable) or hard-fails with a precise pointer at
   what is missing. Mock mode boots without `lab_automation`, so
   catalog edits can be UI-tested first.

---

## 5. Schema versioning

- Cloud-labs maintains `SUPPORTED_SCHEMA_VERSIONS` in
  `backend/lab_communicator/shared/catalog_schema.py`. Today: `{1}`.
- When the catalog grows a breaking field, cloud-labs cuts `v2` and adds
  it to `SUPPORTED_SCHEMA_VERSIONS`. A migration script must accompany
  the bump.
- `lab_automation` does NOT need to track schema versions if it sticks
  to fields listed in §2.2 (those will be stable across minor versions).

---

## 6. Telemetry URLs

Catalog entries declare telemetry endpoints with `{tag_id}` substitution
(§16.6), e.g.:

```json
"stream": { "widget": "MJPEGViewer", "url": "/api/components/{tag_id}/telemetry/stream" }
```

These routes are served by cloud-labs (Phase 6). `lab_automation`
provides only the underlying **generator function** that cloud-labs
wraps (e.g. `get_table_cam_stream`). No `lab_automation` code needs to
know the HTTP path.

---

## 7. References

- `universal_component_architecture.md` — full design rationale and
  validation policy.
- `capability_contract.md` — the contract's reference JSON examples.
- `backend/lab_communicator/shared/catalog_schema.py` — cloud-labs's
  authoritative validator. Hardware-side validation should match it
  exactly for the fields in §2.2.
- `scripts/migrate_component_library_to_capabilities.py` — cloud-labs's
  migration / refresh tool. `lab_automation` never runs this script.
