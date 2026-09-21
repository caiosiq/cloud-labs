# Simulation state presets

Named MuJoCo workspace presets are stored here as `<name>.json`.

Create one from the Twin UI Command Console with `simsave <name>`, or add a
hand-authored JSON file and load it with `simreset <name>`. The default startup
state remains `../lab_state.json` and is loaded with `simreset default`.

These files are simulation initialization data, not Control/version-control
commits. Loading one replaces the uncommitted simulation working state.
