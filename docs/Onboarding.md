# Onboarding: Cloud Labs and the real lab edge

**Audience:** anyone new to Cloud Labs — scientists, lab owners, and engineers — who wants to understand what the system is, how the three faces fit together, and how the real lab edge (`robot-deathray/cloudlabs_edge/`) was implemented.

**How to read this:** the first sections explain Cloud Labs in the language of a scientist; the later sections get concrete about endpoints, channels, and files. It ends with an honest status of what works and what is still a seam.

---

## What Cloud Labs is, in the language of a scientist

Think of Cloud Labs as a way to drive a physical optics bench — an xArm7 robot, a MindVision industrial camera, a laser source, WiFi stepper motors, a breadboard with components on AprilTags — the same way you would drive a simulation: by writing a short script (or clicking in a browser) that says *move this component here, take a picture, run this scoring function on the picture, optimize until the score is maximized*. The central promise is that there is exactly **one vocabulary** for everything you can do or observe, and that vocabulary is identical whether you are talking to a mock bench on your laptop, a MuJoCo simulation, or the real laser bench in the building.

That single vocabulary has a small number of noun/verb categories:

- **Primitives** are the only verbs (the things that change or observe the world, e.g. `MOVE_COMPONENT`, `RECORD_MEASURABLES`, `START_TELEOP`).
- **Tunables** are the commanded degrees of freedom (a component's nominal pose, a camera's exposure, a laser's power, a motor's angle).
- **Measurables** are observations that have no commanded counterpart (a camera frame, an optimization score).
- **Telemetry channels** are the catalog-declared live feeds (a video stream, a teleop pose stream) that you are only allowed to open by first running the matching primitive.
- **Kernels** are small compiled scoring functions (TorchScript) that you feed into `EVAL_KERNEL` or `OPTIMIZE`.

The governing rule, which shows up everywhere in the code, is that if you want something to happen it must be nameable in this vocabulary — there is deliberately no back-door `get_video_feed()` helper, because the moment a lab invents its own side API, the "same language everywhere" guarantee dies.

What Cloud Labs *requires* of a physical lab, then, is that the lab expose its real hardware **through that vocabulary and nothing else**. The scientist keeps writing `lab.move_component(...)` and `lab.measurable("tag_22","camera_image").resolve()`; the lab's job is to make those verbs actually turn the robot and actually read the camera, while owning all the messy hardware-specific details (coordinate frames, camera TCP protocols, motion planning) so that none of that leaks upward. That is precisely what `cloudlabs_edge/` is: the translation layer that receives the universal vocabulary and executes it on this particular bench.

---

## The three faces and where this edge sits

The architecture has three faces, and the edge is the third one.

- The **coordinator** (the cloud-labs server) owns the language itself — it validates primitives, holds session leases, keeps the version-control history of the bench configuration, holds the registry of kernels and the registry of *which* edge to talk to, and it deliberately does **not** import any robot code.
- The **client** is either the Twin (the browser UI) or the SDK (your Python script), and the deep principle is that these two are the *same power*: the Twin is just an SDK client with a browser shell and some default live subscriptions, so it may never gain a verb the SDK lacks.
- The **edge** — the lab's code, living inside the lab's own repository — implements the "Edge Contract v1" and holds all the adapters that actually touch `OpticalExperiment`, the recorder camera, `LiveControlSession`, and the steppers.

The user never talks to the edge directly; they talk to the coordinator in the universal language, the coordinator forwards the primitive southbound to the edge over the Edge Contract, and the edge does the physical work and returns results stamped with a common time. This work completes the triangle for the real bench: before it, cloud-labs itself imported lab_automation and owned the robot math (the "communicator" fracture); now that responsibility has moved into the lab repo where the people who know the table live, and the coordinator can stay hardware-agnostic.

---

## What was skeleton, what we created, and what we touched

The tree began life as a scaffold produced by `cloudlabs-edge init` (the generator is `packages/cloudlabs_edge_dev/src/cloudlabs_edge_dev/scaffold.py`), which writes a *complete function surface* where every adapter is fully documented with docstrings but raises `NotImplementedError`, and where `main.py` serves through a reference stub so that `doctor` and `certify` pass before any hardware is wired. Our work was Phase 6: filling that surface against deathray, plus adding the infrastructure the scaffold intentionally left for the lab to design.

| Provenance | Files |
|---|---|
| **From the skeleton, left essentially unchanged** | `contract.py`, `contract_version.txt`, `bench/layout.json`, `capabilities.json`, `adapters/__init__.py` |
| **From the skeleton, filled in / rewritten** | `main.py` (now points at our real server app), `dispatch.py` (kept the primitive→adapter table, made dispatch `async`), `latch.py`, `kernel_host.py`, `SKELETON.md`, `README.md`, and every module under `adapters/`: `motion.py`, `motors.py`, `vision.py`, `live_feed.py`, `teleop.py`, `tunables.py`, `optimize.py`, `observe.py` |
| **Created from scratch (scaffold left this to the lab)** | the `server/` package (`server/app.py`, the real HTTP/WebSocket surface that replaced the reference stub), the `runtime/` package (`context.py`, `frames.py`, `jobs.py`), the `cameras/` package (`base.py`, `mindvision_tcp.py`, `synthetic.py`, `registry.py`), and `measurables_schema.py` (the canonical measurable envelope) |

In short, the scaffold gave us the *contract-facing shell and the list of verbs*; we supplied the *hardware adapters, the edge-owned runtime, the camera abstraction, and the real transport server*.

---

## How the lab and its components are defined — on both sides

The definition of "what this bench is" is deliberately split by lifetime, and the edge honors that split.

- The **static geometry** of the table — its bounds in millimetres, the danger zone around the robot base, the storage grid, the breadboard spacing — lives in `bench/layout.json` and is served, unchanged, at `GET /bench`. This is what the Twin uses to draw the table and what a script can ask for once.
- The **runtime abilities** — which primitives this process supports right now, which measurables it produces, which live channels exist and at what paths, and what wire profiles (JPEG quality, scale, fps) it offers — live in `capabilities.json` and are served at `GET /capabilities`.

The contract is emphatic that these two must not be merged: capabilities answers "what can this edge *do*?" and bench answers "what does this table *look like*?", and mixing them would make every capability poll drag geometry around and confuse conformance. `server/app.py` loads both files once at startup, stamps the `backend_id` and `contract_version` onto them, and serves them as plain declarations.

The richer question — how a *component* and its *properties* are defined — is answered in two complementary places.

On the **coordinator/server side**, the canonical shape of a component lives in `backend/lab_model/language/domain/component.py`. Every component entry has three buckets: `parameters` (static identity from the catalog — manufacturer, constants — never set by motion), `statecontrol` (which splits into `tunables` and `measurables`), and `telemetry` (which splits into `teleop` and `live_feed` sessions). The crucial conceptual distinction, encoded right there in the defaults, is that a *pose* is a **tunable**, not a measurable: `nominal_pose` is the commanded intent and `reported_pose` is the bench's read-back of that *same* degree of freedom, so when the arm actually moves and reality drifts from the command you refresh `reported_pose` — but it is still the tunable family, because it has a commanded counterpart. A `camera_image`, by contrast, is a genuine **measurable**: it is an observation with no setpoint, it must be captured, and it can be stochastic. The measurable *fields themselves* are defined as plugins on the server: `backend/lab_model/language/measurables/registry.py` holds a decorator-based `MEASURABLE_REGISTRY`, and `backend/lab_model/language/measurables/camera_image.py` registers `camera_image` with its tensor schema — `dtype="uint8"`, `domain="spatial"`, `axes={"y":"pixel","x":"pixel","c":"bgr"}`, `units={"y":"px","x":"px","c":"channel"}`, `layout="lazy_image"`. That registry is the authoritative statement of what a camera measurement *is* as data.

On the **edge/backend side**, the same component and its properties are declared in `capabilities.json`, but only in terms of what this bench can execute and stream: the `measurables` block declares `tag_22.camera_image` with its analysis layout (`bgr_hwc_uint8`), its wire encoding (JPEG), the live channel it binds to, and a `capture_latency_ms`; the `telemetry_channels` block declares the JPEG-poll channel, the MJPEG channel, and the teleop WebSocket, each annotated with the primitive that must arm it; and `supported_primitives` lists the verbs this edge accepts. The edge does not re-define the *meaning* of a component — it does not own a second Wiki — it declares which parts of the coordinator's language it can honor and how they physically reach the wire.

The one place where the edge does own real physics is the **coordinate frame**: `runtime/frames.py` is the single, edge-local place that converts the universal lab/table frame (millimetres and degrees, which is all the coordinator, Twin, and SDK ever speak) into the xArm7 robot-table frame that `OpticalExperiment`'s `_cloudlab` helpers expect, and back again for pose samples. This is the responsibility that used to live in `optics-digital-twin/real.py`; moving it here is exactly what lets the coordinator stay hardware-ignorant. Until the exact rotation/origin/sign constants are copied over from the old `real.py`, the transform reads from environment variables and defaults to identity so it never silently mis-homes.

---

## The endpoints, and how every primitive rides a single door

The most important thing to understand about the edge's HTTP surface, all of which lives in `server/app.py`, is that **mutation has exactly one door**: `POST /execute`, whose body is always `{"primitive": <name>, "args": {...}}`. There is no per-verb REST route; `execute` looks the primitive up, refuses cleanly with `UNKNOWN_PRIMITIVE` if it is not in `supported_primitives`, and otherwise hands off to `dispatch.dispatch_primitive`, which consults the `PRIMITIVE_HANDLERS` table in `dispatch.py` and calls the matching adapter. So `MOVE_COMPONENT`, `RECORD_MEASURABLES`, `SET_EXPOSURE`, `TELEOP_JOG`, `OPTIMIZE` — every verb — enters through the identical endpoint and differs only by the string in `primitive`. The response is shaped by `contract.py`: a success returns `{"status":"completed","epoch_ms":...,"result":{...}}`, a policy refusal returns `{"status":"refused","error":{...}}`, and a hardware failure returns `{"status":"failed",...}`; when `lab_automation` cannot be imported at all, the runtime raises a `LabUnavailable` that the server maps to a clean `LAB_UNAVAILABLE` failure rather than a stack trace.

Around that single mutation door sit a small number of **read/transport endpoints that are never verbs**:

- `GET /capabilities` and `GET /bench` — the static declarations.
- `GET /stream/{tag}/camera_image.jpg` and `.../camera_image.mjpg` — live video, both returning HTTP 409 unless a `START_LIVE_FEED` has armed the channel.
- `GET /measurables/{tag}/camera_image.jpg` — serves the *exact* frame captured by the last `RECORD_MEASURABLES`, so a lazy reference resolves to the real latched instant rather than a fresh live frame.
- `WS /ws/teleop` — the teleoperation WebSocket.
- `GET /jobs`, `GET /jobs/{id}`, `GET /jobs/{id}/stream`, `POST /jobs/{id}/cancel` — the async job scheme for long-running work.

Every one of these is either a static declaration or the *transport of a measurable/tunable that a primitive already armed* — which is the whole philosophy made concrete.

---

## The three channels, and why there are exactly three

The design maps latency onto three tiers, and the edge implements all three, each armed by a primitive and each stamped with time.

- **Tier A — teleoperation.** After `START_TELEOP` takes the single teleop lease (managed in `runtime/context.py`), the client opens the `/ws/teleop` WebSocket and exchanges *flat, tiny* JSON frames at interactive rates — `{"cmd":"JOG","tag_id":...,"axis":"x","val":0.05}` inbound, `{"kind":"pose_sample","x":...,"epoch_ms":...}` outbound. The handler in `server/app.py` deliberately *rejects* nested envelopes with a `NESTED_ENVELOPE` error, because at 50 Hz on a single-threaded asyncio loop, deep JSON is a tax; the flatness is a contract rule, not an accident. The teleop adapter (`adapters/teleop.py`) converts the incoming lab-frame jog into the robot frame via `runtime/frames.py`, drives `LiveControlSession` when hardware is present, and converts the robot pose back to the lab frame for the outgoing sample — and when there is no hardware it keeps a soft in-memory pose so the channel still validates on a laptop.
- **Tier B — live video.** `START_LIVE_FEED` flips an arm bit (in `runtime/context.py`), after which the JPEG-poll and MJPEG endpoints stream frames from the camera; `END_LIVE_FEED` clears the bit and the stream endpoints go back to 409.
- **Tier C — deliberate, lower-frequency work.** `POST /execute` itself, `RECORD_MEASURABLES` and `EVAL_KERNEL` with their latched epochs, and the `OPTIMIZE` job stream.

The point of separating them is that each has a fundamentally different latency profile and therefore a different transport, but *all three* are still expressed in the one vocabulary — the video is "the wire form of `camera_image`," the teleop stream is "live samples of the pose tunable," and neither is a new type of thing.

---

## How each fundamental concept shows up on this backend now

- **Primitives** show up as the `PRIMITIVE_HANDLERS` table in `dispatch.py`, one row per verb, each pointing at an adapter function. The ones backed by real deathray code (`MOVE_COMPONENT`, `PICK`/`HOVER`/`PLACE_FROM_HOVER`, `SCAN_ROTATE_IN_PLACE`, `MOVE_MOTOR`, `SET_EXPOSURE`, `SET_LASER_OUTPUT`, `OPTIMIZE`, and the live-plane set) are implemented, while the inventory/storage verbs and absolute-motor verbs that have no lab_automation implementation yet keep their scaffold docstrings and raise `NotImplementedError`, which the server surfaces as an honest `NOT_IMPLEMENTED` refusal rather than a fake success.
- **Tunables** show up as edge writes: `adapters/tunables.py` sends exposure to the camera and power to the laser, and pose/motor tunables are driven through motion and teleop.
- **Measurables** show up in `adapters/observe.py`, the heart of the observation path: `record_measurables` opens a latch, captures a BGR frame through the camera abstraction, caches the JPEG so the `/measurables/...` URL can later serve that exact instant, and returns a canonical `MeasurableTensor` envelope. The envelope is built by `measurables_schema.py`, which pins the same `axes`/`units`/`dtype`/`domain` as the coordinator's `camera_image` registry so that every backend speaks byte-identical measurable metadata. The image data itself is returned as a **LazyRef** — `{"kind":"url","href":"/measurables/tag_22/camera_image.jpg","format":"jpeg"}` — meaning the heavy pixels stay resident on the edge and only travel when someone actually resolves them, which keeps lab-state small while still giving scripts full pixel access on demand.
- **Telemetry channels** show up as the `capabilities.json` declarations plus the stream/WS endpoints they name.
- **Kernels** show up in `kernel_host.py`, which loads and caches TorchScript modules and evaluates them on the in-RAM BGR frame, invoked only through `EVAL_KERNEL` (there is no separate `/kernel/probe` route, by contract).
- **Jobs** show up in `runtime/jobs.py`, an in-process async registry where a long primitive like `OPTIMIZE` is submitted, returns `{"status":"accepted","job_id":...}` immediately, runs the blocking scipy/arm work in a worker thread via `asyncio.to_thread`, and reports progress that clients can poll or subscribe to as a server-sent event stream.
- **Latch / epoch** shows up in `latch.py`, which stamps one `epoch_ms` and a `latch_quality` onto a capture so that an image, motor angles, and a kernel score can be known to belong to the same instant.

---

## Why there are API/backend requests at all, and how we handle latency

It is worth being explicit about *why* the system is built from requests, streams, and jobs rather than one uniform call, because the answer is entirely about latency and truth. A synchronous `POST /execute` request/response is right for a discrete action whose result you need before proceeding — move a component, set an exposure, take one measurement — and the edge keeps these fast and returns a completed envelope. But three kinds of work break the simple request/response model, and each gets a purpose-built mechanism:

1. **Interactive control** (human "laser hunting") cannot tolerate a fresh HTTP handshake per nudge, so it lives on the Tier A WebSocket with flat frames; the latency budget is met by keeping the payload tiny and refusing nested JSON.
2. **Continuous observation** (watching the video) should not be a thousand discrete captures, so it is a Tier B stream armed by a primitive; the latency is bounded by the wire profile's fps and JPEG quality rather than by the analysis-grade BGR, and the full-resolution pixels never leave the edge on a mere preview.
3. **Long deliberate work** (an optimization sweep that runs for many seconds) must not hold a socket open, so it becomes a Tier C job that returns a handle instantly and streams progress; the closed-loop inner iterations run *entirely on the edge* next to the camera and the arm, which is the only way to make an optimizer's feedback loop fast — a WAN `for`-loop shipping whole frames back to a laptop would be hopeless.

The subtler latency problem is not speed but **synchronization**, and the edge's honest position is encoded in `latch.py`. We cannot guarantee zero delay from the bench to a remote browser, and we must not pretend that "read the camera buffer and read the encoders at software `now`" is physically simultaneous — an industrial camera's shutter and readout can lag the encoders by tens of milliseconds. So the contract's guarantee is narrower and truthful: any payload *produced on the edge* carries a single `epoch_ms` for all fields latched together, plus a `latch_quality` that says how that time was obtained. This bench has no hardware trigger line (`capabilities.features.hardware_triggered_latch` is false), so `end_latch` reports `latch_quality="software_approx"`, computes the epoch as the midpoint of the capture window, and shifts it back by the per-device `capture_latency_ms` read from `capabilities.json`. The consequence for a scientist is a discipline: if you need image-and-motors to genuinely correspond, you call *one* latched `RECORD_MEASURABLES` or `EVAL_KERNEL` — you do not compose science out of two independent live polls. The live streams are for the human eye; the latched observe is for the truth.

---

## Honest status

- The edge passes `cloudlabs-edge doctor` and `cloudlabs-edge certify` (with expected warnings for the still-`NotImplementedError` inventory/storage verbs and a clean degradation to the synthetic camera when `lab_automation` is absent).
- The measurable envelope now conforms exactly to the coordinator's canonical schema, with a `certify` check guarding against drift.
- The frame calibration constants from the old `real.py` still need to be filled into `runtime/frames.py` before the arm is driven for real, since the default is identity.
- The one architectural seam that is designed but not yet finished is the coordinator's `GET /lab-state` reconciliation plus image proxy: right now the edge emits the correct LazyRef and serves the exact latched JPEG at its own `/measurables/...` URL, but for a script to resolve a tensor *through the coordinator* against a remote HTTP edge, the coordinator still needs to poll the edge's state and proxy that image URL. That is the natural next piece, and it is the thing that would make `resolve()` / `resolve_torch()` work end-to-end from a laptop against the real bench.
