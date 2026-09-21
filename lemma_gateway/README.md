# Lemma Cloud Labs gateway

This is a localhost-only, simulation-only HTTP surface for remote actors.  It
accepts the same concise language as the Twin Command Console and translates it
to the existing Cloud Labs coordinator API.  The backend is fixed to
`sim.default`; callers cannot select the mock or physical backend.

## Start locally

Start the simulation edge and coordinator first, then open a third PowerShell
terminal at the repository root:

```powershell
.\scripts\ops\run_lemma_gateway.ps1
```

The gateway binds only to `127.0.0.1:8787`.  Interactive API documentation is
available at <http://127.0.0.1:8787/docs>.

## Local checks

```powershell
Invoke-RestMethod http://127.0.0.1:8787/v1/health
Invoke-RestMethod http://127.0.0.1:8787/v1/capabilities
Invoke-RestMethod http://127.0.0.1:8787/v1/state
```

Read commands return immediately:

```powershell
$Body = @{ command = "state" } | ConvertTo-Json
Invoke-RestMethod `
    -Method Post `
    -Uri http://127.0.0.1:8787/v1/console `
    -ContentType "application/json" `
    -Body $Body
```

Mutations return HTTP 202 and a `job.job_id`.  Poll
`GET /v1/jobs/{job_id}` until its status is `succeeded`, `failed`, or
`cancelled`:

```powershell
$Body = @{
    command = "move tag_13 -300 0 -90"
    request_id = "lemma-example-001"
} | ConvertTo-Json
$Accepted = Invoke-RestMethod `
    -Method Post `
    -Uri http://127.0.0.1:8787/v1/console `
    -ContentType "application/json" `
    -Body $Body
$JobId = $Accepted.job.job_id
Invoke-RestMethod "http://127.0.0.1:8787/v1/jobs/$JobId"
```

`request_id` is an optional idempotency key. Repeating the same request id and
command returns the original job instead of executing the command twice.

## Read and author simulation presets

Read editable preset JSON without loading it:

```powershell
Invoke-RestMethod http://127.0.0.1:8787/v1/presets/test_random
```

Write validated preset JSON without moving the live lab or restarting MuJoCo:

```text
PUT /v1/presets/<name>
{"document": <the JSON returned by simshow>, "overwrite": false}
```

The console equivalents are `simshow <name>` and
`simwrite <name> [--overwrite] <json>`. `simwrite` is a mutation job; poll its
job ID as usual. The gateway never accepts filesystem paths—preset names are
resolved and validated by the simulation-only coordinator API.

## Concise discovery

`GET /v1/capabilities` returns short command signatures, active components,
valid presets, table bounds, and current status.  Use `?detail=true` only when
the extra notes are useful.  `GET /v1/state?component=tag_13` returns one
component instead of the whole bench.

## Authentication

Cloudflare Access is the intended public authentication layer.  For a direct
local or temporary-tunnel test, set `CLOUDLABS_LEMMA_API_TOKEN` before starting
the gateway; all `/v1/*` requests must then include:

```text
Authorization: Bearer <token>
```

Do not bind this process to `0.0.0.0`.  A Cloudflare Tunnel should target the
localhost URL `http://127.0.0.1:8787`.

## Cancellation boundary

Queued gateway jobs can be cancelled.  The current Cloud Labs/MuJoCo API does
not expose a hard interrupt for motion already accepted by the edge.  The
cancel and emergency-stop responses report this explicitly rather than
claiming the physical motion was interrupted.
