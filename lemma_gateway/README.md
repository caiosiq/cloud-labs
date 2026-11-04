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

For queued primitives, the gateway polls that specific coordinator command ID.
`succeeded` means the command reported `done`; an idle lab alone is insufficient.
Failures and cancellations retain the coordinator command ID and final status
in `job.result`, with the reason in `job.error`. Timeouts or lost connections
mean the outcome could not be confirmed; an accepted action may still finish.
Do not automatically resubmit a move after either condition. With command
queuing disabled, the coordinator's synchronous completed response is used.

After completion, read `state` to verify the requested effect. Both compact
`state` and full `state --json` include `last_runtime_error` (or null); this is
the latest simulator diagnostic, not a command history or a success signal.
Gateway jobs and request-id deduplication are held in memory and are lost when
the gateway restarts. An unknown job after a restart does not establish whether
its action executed; inspect the lab before submitting it again.

## Inspect a failed job

Failed jobs automatically include a `diagnostics` object in the normal job
response. It is captured before the terminal status is published and retained
in memory with that job, so subsequent reads remain stable if the simulator
restarts or its log is deleted. Immediate console rejections also include
`diagnostics` alongside `detail`, without creating a job. If collecting logs
fails, the original command error is preserved and the diagnostic response
explains the limitation. Successful jobs have `diagnostics: null`.

Lemma should read the attached `error` and `diagnostics` before deciding on
another action; do not automatically retry safety refusals or uncertain moves.
Missing or shortened logs do not imply success. No follow-up request is needed.

Each job also includes a `diagnostics_url` for optional retrieval with the same
authentication used to submit commands:

```text
GET /v1/jobs/<job_id>/diagnostics
```

The gateway console equivalent is `diagnostics <job_id>`. It returns the
command's status and error, plus matching simulator log entries when available:
the submitted pose, failure, completed stages, and Python traceback. It filters
by the simulator request ID so unrelated moves are excluded. No new move is
submitted by either diagnostic read. For a failed job, both return the captured
snapshot already attached to its status response. Snapshots expire with the
gateway's in-memory job history when the gateway restarts.

The gateway reads local MuJoCo session logs on the laptop. If the simulator
uses a custom `CLOUDLAB_MUJOCO_LOG_DIR`, set the same directory for the gateway.
Only referenced `mujoco_*.jsonl` files in that directory are readable; callers
cannot provide arbitrary file paths. Reads scan at most the last 4 MiB and
return at most 40 events / approximately 96 KiB of event data, with truncation
reported. Full physics snapshots are omitted. If the file is missing, the
failure predates the excerpt, or the error occurred before the simulator was
called, the response explains why no simulator entries are available and keeps
the original error. This does not expose the entire server terminal history.

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
