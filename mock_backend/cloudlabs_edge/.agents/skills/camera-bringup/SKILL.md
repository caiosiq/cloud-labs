---
name: camera-bringup
description: >-
  Real-lab camera initialization, exclusive device ownership, scan vs science
  cameras, frame quality, and startup failure modes on a Cloud Labs edge. Use
  when wiring boot camera init, debugging CONNECT / no-frame / wrong-resolution
  captures, leftover recorder processes, OpenCV vs vendor-SDK collisions, or
  deciding what belongs in inventory vs bench infrastructure. Mostly relevant
  to hardware edges (mock/sim may stub or skip).
---

# Camera bring-up (real labs)

Cameras fail more often at **startup and exclusive access** than inside a single
`POST /execute`. Fix ownership and boot order before chasing Twin FSM bugs.

Mock and simulation edges may have no USB cameras — keep adapters honest
(`refused` / synthetic) and skip hardware bring-up. This skill is for benches
that open real devices.

## Two jobs, two stacks

Most optics labs have:

| Job | Typical stack | Driven by |
|-----|---------------|-----------|
| **Science / Twin stills** (`RECORD_MEASURABLES`, live feed) | Vendor SDK or a long-lived recorder process that owns USB | Inventory tags whose library binding says so (e.g. `recorder_tcp`) |
| **Scene localization** (AprilTag / stereo / overview for LOCALIZE) | OS camera API (often OpenCV) + calibration | Bench infrastructure — **not** “put every camera in inventory so it initializes” |

Do not merge those stacks. Opening the same physical camera twice (SDK + OpenCV,
or two processes) yields “no camera”, empty frames, or a listening TCP port that
never CONNECT successfully.

## Ownership (normative)

| Piece | Owner |
|-------|--------|
| Which science cameras must be live | **This edge** — `data/inventory.json` ∩ library camera bindings |
| Boot spawn + CONNECT / open | **This edge** (e.g. `cameras/initialize.py` after robot home) |
| Scan / overview OpenCV (or equivalent) | Host experiment runtime — open as infrastructure when needed |
| Twin / coordinator | Never starts lab cameras; only consumes measurables / streams |

`lab_automation` (or your host SDK) must **not** import edge inventory to decide
camera lists. Edge boot loads inventory + library, then opens what inventory
requires.

Launch scripts should start the edge HTTP process (and calibration env) — not
silently spawn a second camera stack unless that is the documented bring-up path.

## Index namespaces (do not conflate)

Labs often have three different integers that look alike:

| Namespace | Meaning |
|-----------|---------|
| Logical / Twin cam id + TCP port | Client identity for RECORD / live (`cam_id`, `recorder_port`) |
| Vendor SDK device index | Enumeration from **that** SDK (“Found N cameras: 0: …”) |
| OS / OpenCV device index | Enumeration from VideoCapture / Media Foundation / V4L2 |

A science camera’s SDK index is **not** an OpenCV device index. Feeding an SDK
index into OpenCV (or the reverse) remaps the wrong sensor — often a ceiling cam
at the wrong resolution — and breaks stereo / PnP against calibration that
assumes a specific pixel size.

## Frame quality

- Requested resolution must match **calibration intrinsics**. Opening a 4K-calibrated
  overview at VGA produces systematically wrong world poses (often multi-meter scale).
- After open, do a **warm-up grab** before the first science or scan frame —
  exclusive backends (e.g. Windows MSMF) often return empty until the stream settles.
- Prefer one open per device. Never construct a default camera handle as a
  `dict.get(key, CameraDriver(...))` fallback — many languages evaluate the
  default even on a hit and **double-open** the device.
- When the active catalog has no OpenCV/overview bindings (only science cameras
  in inventory), still open the **scan pair / overview cams your LOCALIZE path
  needs** as infrastructure. Do not fall through to “open every legacy USB index”
  including gripper indices the vendor SDK already owns.

## Inventory vs infrastructure

- Inventory = tracked components / science cameras Twin should know about.
- Overview / stereo / fixed scan cameras are usually **fixtures**. Legacy
  active catalogs often listed only manipulables — same idea: do not add overview
  tags to inventory merely so they “initialize.”
- Library may still describe a table-top camera for UI/stream metadata; that does
  not require inventory membership for LOCALIZE.

## Leftover processes and ports

Long-lived recorder / grabber processes listen on localhost ports. After a crash:

- A port may still **accept TCP** while the USB session is dead → CONNECT /
  CameraInit fails on “reuse.”
- Prefer: detect listen → try CONNECT → on failure **kill listener and respawn**
  once, rather than stacking a second process on the same port.
- On a shared Windows/Linux box, Twin HTTP, edge HTTP, and recorder ports are
  distinct. Bind failures (`address already in use`) mean another process still
  owns that port — identify and stop it; do not treat it as a catalog bug.

Typical local layout (adjust to your lab): coordinator ~`8000`, edge HTTP
~`8200`, science recorders on dedicated localhost ports.

## Boot order

```text
get_experiment() / edge host construct
  → open host runtime (catalog = library ∩ inventory for tracked tags)
  → initialize_robot() / safe home   # before exclusive camera storms if needed
  → initialize science cameras()     # inventory bindings → spawn + CONNECT
  → ensure scan/overview cams open   # infrastructure; resolution = calibration
  → boot SYNC_RUNTIME / RECORD_TUNABLES
```

Skip hardware init with an explicit env flag for laptop / certify stub profiles
(e.g. `CLOUDLABS_SKIP_CAMERA_INIT` / mock) — never silently pretend CONNECT ok.

## Logging

Use stable prefixes so Twin and the edge terminal agree:

- `[cameras]` — spawn, port reuse/respawn, CONNECT ok/fail
- `[measurables]` — capture path after the device is live
- `[scan-debug]` or equivalent — which OS indices opened, resolution got vs want

## Failure cheatsheet

| Symptom | Likely cause |
|---------|----------------|
| Connection refused on recorder port | Process never spawned / wrong port |
| Port already open, then CameraInit / “no camera” | Stale listener; need kill + respawn |
| Vendor SDK “no camera” right after OpenCV open | Competing stack stole the USB device |
| Scan poses nonsense / huge | Overview opened at wrong resolution or wrong index |
| Intermittent empty grab / MSMF errors | Double-open, USB bandwidth, or missing warm-up |
| Twin RECORD fails, edge looks “ready” | Bring-up skipped or CONNECT failed; not a coordinator FSM bug |
| Twin bind / edge bind address in use | Leftover process on that port |

## Do

- Bring science cameras up once at edge boot from inventory ∩ library.
- Keep scan/overview opens in the host runtime as infrastructure.
- Treat each index namespace as distinct; document them in library parameters.
- Respawn dead listeners; log CONNECT results explicitly.
- Match capture resolution to calibration; warm-up before first use.

## Do not

- Do not start cameras from the Twin / coordinator.
- Do not open the same physical device from two stacks.
- Do not put every fixture camera in inventory “so init runs.”
- Do not pass the **full** library as the experiment catalog when that opens every
  `vision_device_index` / overview row and remaps scan cameras.
- Do not invent Twin success when the device never CONNECT’d / never grabbed.

## Check

1. Edge log: science cameras CONNECT ok (or honest skip under mock/skip flags).
2. Scan/overview cams report the resolution calibration expects.
3. `RECORD_MEASURABLES` returns a LazyRef JPEG; LOCALIZE poses are bench-scale.

## Related

- [`measurables-tensors`](../measurables-tensors/SKILL.md) — envelope after a good frame
- [`inventory-honesty`](../inventory-honesty/SKILL.md) — what inventory means
- [`frames-calibration`](../frames-calibration/SKILL.md) — table frame after PnP
- [`latency-channels`](../latency-channels/SKILL.md) — live preview vs latched record
