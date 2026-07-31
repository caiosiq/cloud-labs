# coordinator_data/

Per-`backend_id` **coordinator** store, created on first probe/connect:

- `lab_state.json` — Twin working FSM (HOLDING, presence, commanded tunables)
- `control/` — configuration version control
- `laser_lines.json`, `recipes/` — Twin overlays / operator artifacts

**Not here:** library, inventory, layout, motor rotations — those live on the
edge (`cloudlabs_edge/data/`, bench layout). See `docs/BACKEND_ISOLATION.md`.

Runtime contents are gitignored; this README is the only tracked file.
