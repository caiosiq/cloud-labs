# coordinator_data/

Per-`backend_id` **coordinator** store, created on first probe/connect:

- `lab_state.json` — Twin working FSM (HOLDING, presence, commanded tunables)
- `control/` — configuration version control
- `laser_lines.json`, `recipes/` — Twin overlays / operator artifacts

For HTTP-edge backends without a local `lab_view_path` (e.g. `real.default`),
initial laser lines are copied from `schemas/coordinator_seeds/<backend_id>/`
when the store is first created or still has an empty placeholder.

**Not here:** library, inventory, layout, motor rotations — those live on the
edge (`cloudlabs_edge/data/`, bench layout). See `docs/BACKEND_ISOLATION.md`.

Runtime contents are gitignored; this README is the only tracked file.
