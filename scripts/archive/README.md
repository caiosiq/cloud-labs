# Archived one-shot migration scripts

These scripts were used during the **Universal Component** refactor. The
repo’s mock/real catalogs and state files are already on the new shapes;
keep these only for **legacy JSON** you pull in from old benches or backups.

| Script | Purpose |
|--------|---------|
| `migrate_to_tunables.py` | Legacy component JSON (`state` / `pose` / `intent`) → `tunables` + `measurables` |
| `migrate_component_library_to_capabilities.py` | Catalog array → v1 object + inferred `capabilities` blocks |

Run from repo root, e.g.:

```bash
python scripts/archive/migrate_to_tunables.py path/to/old_lab_state.json
python scripts/archive/migrate_component_library_to_capabilities.py --dry-run
```

Do not import these from application code.
