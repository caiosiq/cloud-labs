---
name: cloud-labs
description: >-
  Inspect and operate a Cloud Labs simulation or experiment through the
  remote Lemma gateway, coordinator, Python SDK, or Twin Command Console. Use
  for reading lab state, moving or measuring components, controlling motors,
  and saving or loading MuJoCo simulation presets.
---

# Cloud Labs control

Treat the Cloud Labs coordinator as the authority. Use shared primitives; do
not manipulate MuJoCo or hardware behind the coordinator.

## Connect

A cloud-sandbox agent (no route to laptop `127.0.0.1`) has no use for the
addresses below; skip straight to "Remote Lemma gateway".

- Coordinator: `http://127.0.0.1:8000`
- Twin: `http://127.0.0.1:8000/twin`
- Local simulation backend: `sim.default`
- Positions use lab-frame millimetres; rotations use degrees.

Call `GET /api/backends` and confirm the intended backend is ready. Never act on
whichever backend happens to be visible in a browser tab.

### Remote Lemma gateway

Lemma cannot reach laptop `127.0.0.1` directly. The operator starts the
simulation edge, coordinator, authenticated gateway, then tunnel:

```powershell
$env:CLOUDLABS_LEMMA_API_TOKEN = "<secret>"
.\scripts\ops\run_lemma_gateway.ps1
& "C:\Program Files (x86)\cloudflared\cloudflared.exe" tunnel --url http://127.0.0.1:8787
```

Treat the generated `https://*.trycloudflare.com` URL and token as runtime
inputs; never store them in this skill. The URL changes after a tunnel restart.
Send `Authorization: Bearer <token>` on every request:

```text
GET  <gateway>/v1/health
GET  <gateway>/v1/capabilities
POST <gateway>/v1/console   {"command":"state"}
POST <gateway>/v1/console   {"command":"move tag_13 -220 100 45"}
GET  <gateway>/v1/jobs/<job_id>
```

Mutations return `202` plus a job ID. Poll the job, then read `state` and verify
the requested values. Do not trust job `succeeded` alone: the current gateway
can miss a later Command Matrix failure. The gateway is locked to `sim.default`
and manages its internal Cloud Labs lease; do not send backend or lease headers.
`401` means the bearer token is missing/wrong. A connection failure usually
means the gateway or tunnel is down.

Observed in practice: a single `move` job's wall-clock time has ranged from
~3 s to ~30 s call to call, so do not assume a fixed settle time. Poll `state`
in parallel with the job, not only after it reports `succeeded` — the pose and
`system_status` can reach their final values a moment before the job object
itself flips to `succeeded`, and polling only the job afterward can miss the
`BUSY` window entirely.

For automation running on the laptop, prefer the Python SDK because it manages
the exclusive lease:

```python
from cloudlabs import connect

with connect("sim.default", base_url="http://127.0.0.1:8000",
             holder="lemma") as lab:
    state = lab.get_lab_state()
    if state.get("system_status") != "IDLE":
        raise RuntimeError(f"Lab is {state.get('system_status')}")

    tag = "tag_13"
    if tag not in (state.get("components") or {}):
        raise KeyError(f"Inactive component: {tag}")

    lab.move_pose(tag, x=-300, y=0, rotation=-90)
    final = lab.wait_for_primitive_settled(timeout_s=180)
```

Direct `POST /api/command` may require `X-CloudLabs-Backend` and an active
`X-CloudLabs-Lease`. Do not bypass or repeatedly retry a lease conflict.

## Always observe, act, and verify

1. Read current state and confirm `backend_id` and `system_status`.
2. Resolve a real `tag_id`; never guess from a similar display name.
3. Inspect presence, storage, holding state, and current pose.
4. Send one primitive. Do not overlap motion commands.
5. Wait until settled, read state again, and verify the exact result.
6. Report requested and final values, whether that final read was commanded
   intent (`tunables`) or a fresh measurement (`measurables`), plus any
   refusal or uncertainty.

For a request containing multiple actions, repeat the complete observe -> act ->
wait -> verify cycle for every action. "Send one primitive" means one at a time,
not only one total. Continue through the requested sequence after each verified
success, and do not report completion until every requested action has
succeeded or a specific failure has been reported.

Normally mutate only from `IDLE`. `HOLDING` permits only holding-compatible
actions. Wait during `BUSY`, `OPTIMIZING`, or `TELEOP`.

`tunables` are commanded intent. `measurables` are the last recorded
observations and may be stale. Use `RECORD_MEASURABLES` when fresh observation
is required. `GET /api/lab-state?backend_id=sim.default` is a planning overview,
not high-rate scientific telemetry.

## Component orientation

Reason about each component's semantic **input direction**; knowledge of the
physical tracking marker is not required.

- At `rotation = 0 deg`, the component input direction points along lab `+x`.
- Positive rotation is counterclockwise when viewed from above.
- Orient the input direction along the incoming beam's propagation direction.
- Components in storage normally use `0 deg`.

For a beam traveling from source `(x1, y1)` to destination `(x2, y2)`, compute:

```text
dx = x2 - x1
dy = y2 - y1
rotation_deg = atan2(dy, dx) * 180 / pi
```

Thus `+x -> 0 deg`, `+y -> 90 deg`, `-x -> 180 deg`, and `-y -> -90 deg`.
For example, a beam traveling from `+y` toward `-y` requires `-90 deg`.
Apply this convention when translating an Optiland or paper-derived layout into
Cloud Labs component poses.

## Physical housing and layout clearance

Design and move the mounted assemblies, not idealized optical elements. A lens,
polarizer, camera, or filter occupies its housing, base, and required clearance;
its clear aperture or optic diameter is not a valid placement footprint.

Before proposing a layout or sending moves, read the selected backend's
`/api/catalog` and `/api/lab-layout` when available. For two catalog footprints
`(w1, h1)` and `(w2, h2)`, Cloud Labs' conservative collision rule is:

```text
r = sqrt(width^2 + height^2) / 2
center_distance >= r1 + r2 + 5 mm
```

The current optical-housing catalog entries are 62 x 62 mm, so two such parts
need at least 92.7 mm center-to-center. The MuJoCo profile itself models an
approximately 72 x 64.3 mm housing and a 75 mm-diameter base. Treat the
catalog-derived rule and backend validation as authoritative, never shrink a
footprint to the optic diameter, and fail closed for an unknown component size.
Also respect frame clearance, the robot-base danger zone, storage, and motion
reachability; a non-overlapping optical diagram is not automatically executable.

When handing a design to Optiland or another optical-design agent, explicitly
include these mechanical constraints and require the returned coordinates to
pass the same center-spacing rule. If an optically valid design needs closer
spacing, report it as mechanically infeasible and redesign it; do not defer the
problem until Cloud Labs execution.

## Twin Command Console

Use the console when a person wants to watch commands in the open Twin tab:

```text
labstate                  complete concise state report
labstate --json           complete raw state JSON
tunables <tag-or-name>
measurables <tag-or-name>
record <tag-or-name>      take a fresh measurement
move <tag-or-name> <x> <y> <rotation>
motor <tag-or-name> <motor_id> <distance>
store <tag-or-name> [slot_i slot_j]
place <tag-or-name> <x> <y> <rotation>
```

Run `help` for the full grammar, including laser stitching and in-air
pick/hover/place commands. The console is a client of the same coordinator, not
a separate source of truth.

## Simulation component library

Use simulation component commands when a paper-derived experiment needs parts
that are not in the active startup arrangement. Definitions, active table
membership, and saved arrangements are separate:

- Built-in definitions stay immutable. `configure` stores a simulation-only
  override; `reset` removes that override.
- Tags are canonical positive integers: `tag_1`, `tag_2`, and so on. Always use
  `simcomponent nexttag` before defining a new component; never invent a
  descriptive tag.
- `define` creates a persistent custom definition under that stable numeric tag.
  MuJoCo renders it with the standard generic housing/black-box model.
- Optical details such as focal length, coating, filter band, material, and
  nominal wavelength belong in `parameters`. They are available to downstream
  planning and Optiland agents, but MuJoCo does not interpret them as optical
  physics.
- `insert`/`remove` change the live simulation. Save a preset when the resulting
  arrangement should be reusable.
- For a multi-component layout, avoid repeated `insert` restarts: define or
  configure all required tags, use `simwrite` to author their poses together,
  then `simreset` that preset once. A known library tag may be introduced by
  `simwrite` even when it is not present in the selected base state.

```text
simcomponent list
simcomponent nexttag
simcomponent show <tag>
simcomponent define <tag> <json>
simcomponent configure <tag> <json>
simcomponent reset <tag>
simcomponent insert <tag> <x_mm> <y_mm> <rotation_deg>
simcomponent insert <tag> storage <slot_i> <slot_j>
simcomponent remove <tag>
simcomponent delete <tag>
simclear [table|all]
```

Minimal parametric lens example (submit as one line):

```text
simcomponent nexttag
simcomponent define tag_100 {"name":"Paper lens","type":"OPTICAL_LENS","parameters":{"focal_length_mm":175,"filter":"longpass"}}
simcomponent insert tag_100 -200 120 0
```

`list` and `show` expose `tag_id`, `name`, `type`, `parameters`, `housing`,
`active`, and `placement`. The read-only `housing` contains footprint width and
depth, height, and the conservative clearance radius; the response also gives
`pair_clearance_margin_mm`. Plan two centers at least
`radius_1 + radius_2 + pair_clearance_margin_mm` apart. `list` returns all
records, `show` returns one, and `nexttag` returns only the next unused tag.
Allowed editable fields remain `name`, `type`, and `parameters`; Cloud Labs owns
housing geometry. Use `GENERIC_COMPONENT` for an unknown black box. Do not
encode poses in a definition. Tags identify instances and cannot be silently renamed;
remove/delete and define a new tag instead. `simclear table` keeps stored parts;
`simclear all` removes every active part but never deletes definitions.

These commands are simulation-only, require `IDLE`, validate tags and geometry,
restart only MuJoCo when the live scene changes, and publish a Twin reset
revision. Never use them to claim that physical hardware exists.

## MuJoCo simulation presets

These commands are simulation-only and require `IDLE`:

```text
simreset list             list named presets
simreset current          restart MuJoCo from live working state
simreset default          load simulation_edge/lab_view/lab_state.json
simreset <name>           load lab_view/states/<name>.json
simsave <name>            save the normalized live setup
simsave <name> --overwrite
simshow <name>            return editable preset JSON without loading it
simwrite <name> [--overwrite] <json>
```

A reset restarts only the MuJoCo viewer/process, replaces the coordinator's
working state, and publishes a revision that makes open Twin tabs discard stale
ghost poses. Prefer `simsave` over editing JSON. Edit a preset file directly
only while the relevant processes are stopped.

Use `simshow` when target poses are needed without changing the lab. Its output
is the exact compact JSON accepted by `simwrite`; edit it and write a new preset
without moving components. `simwrite` validates the schema, tags, poses,
storage, bounds, danger zone, and overlap, and never loads the result. Through
the remote gateway the equivalent routes are `GET /v1/presets/<name>` and
`PUT /v1/presets/<name>`.

Simulation presets are quick starting arrangements. Control/version-control
configurations are durable experimental history. Loading a preset produces an
uncommitted live setup; save it into Control separately when it should become
durable history.

Never use `simreset`, `simsave`, `simshow`, or `simwrite` on physical hardware. A physical bench must
reach a configuration through supported primitives, sensing, and any required
operator confirmation.

The ordinary primitive vocabulary is shared between simulation and a physical
backend, but support and safety gates are backend-specific. Before sending a
primitive to a backend not yet exercised, check its `/v1/capabilities` (or
`GET /api/backends`) and accept a refusal there as authoritative, not as
something to retry or route around.

## Refusals and completion

- `400/422`: malformed or invalid request; read the detail before correcting.
- `409`: busy, unsafe, unsupported, or lease conflict; do not blindly retry.
- `410`: expired lease; reacquire through the normal client lifecycle.
- `502/503`: coordinator or edge unavailable; report which service is down.

HTTP success alone is not completion. Completion requires a post-action state
read that matches the intended result.

For the full command inventory, endpoint examples, startup commands, and safety
notes, read `LEMMA_SKILL.txt` in this skill's folder (mirrors
`simulation_edge/LEMMA_SKILL.txt` in the cloud-labs repo — that repo path does
not exist wherever this skill is deployed, so the companion file travels with
it instead).
