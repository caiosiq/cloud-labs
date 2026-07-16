# Version control and lab history

Optical benches change. Parts move, motors tick, and a layout that worked this
morning may be gone by afternoon. Cloud Labs keeps a **git-like history of
commanded layouts** so you can save a story worth returning to, fork an idea,
and travel between known points without treating every camera frame as sacred
truth.

The Twin’s control graph above the breadboard is the main place this history
lives. Scripts can take the same snapshots through the `cloudlabs` SDK. This
chapter explains what is being saved, what “dirty” means, and how apply-on-bench
differs from a casual UI drag.

![Runtime, empty baseline, commits, and catalog pins](/static/wiki/guides/figures/vc-layers.svg)

*Live runtime is the working tree; commits store configuration; the empty
baseline is the shared floor for dirtiness; catalog pins are approved
snapshots.*

## Why history exists

A programmable lab needs two kinds of memory. One is the **live bench**—what
Twin polls every fraction of a second. The other is a **saved layout**: which
parts should be on the table, where they are commanded to sit, and how motors
should be set. History is for the second kind. You commit when a configuration
is worth naming, not every time a measurable flickers.

Without that split, “undo” would mean replaying noisy images and telemetry as
if they were setpoints. Cloud Labs refuses that confusion: commits store
**configuration** (intent), while measurements stay **observations** you can
pin and compare, not check out as physics.

## Runtime versus configuration

**Runtime** is the live lab document Twin and Operations read—the working tree.
It includes tunables, measurables, telemetry, holding, and process fields such
as system status. When you move a mirror or jog a motor, runtime updates. That
live document is **not** itself a version-control node.

**Configuration** is the slice of commanded intent taken from runtime: per-tag
tunables plus holding intent. A **commit** in a control repository is a
configuration document on a branch graph. Checking out a commit projects that
intent back into runtime (and, for a hard checkout, through primitives onto
hardware). Telemetry is ephemeral; it rides along in runtime but is not what
commits remember.

**Observations** are the ensemble of measurables—camera frames, scores, and
other receipts. You may pin them next to a configuration as a **setup** (a
named pairing of layout plus what the lab saw). You do not “check out” an old
PNG to make the photons true again; you re-record if you need a fresh receipt.

## The empty baseline

Every control repository conceptually starts from a shared **empty baseline**:
a virtual bench with no active components and no holding. Internally this is
sometimes called the **zeroth state** (`EMPTY_CONFIGURATION`). It is not a
commit you open in the graph; it is the common floor that makes “is the bench
dirty?” well-defined even when no commit yet owns the physical table.

If a repository has an **applied** commit, dirtiness means the live
configuration differs from that applied node. If nothing applied owns the
bench—or you are looking from a repo that does not own it—the live layout is
compared against the empty baseline, so any part on the table reads as
uncommitted work. Stashing from a non-owning repo clears back toward that
empty floor rather than inventing a foreign history.

This empty baseline is unrelated to the motor primitive `MOTOR_SET_ZERO`, which
only references a motor angle.

## Commits, branches, and the Twin graph

Local version control lives in **control repositories** on the backend’s lab
view (for example under `control/{repo_id}/`). Twin’s graph shows branches and
commit nodes for the repo you select. Creating a **commit** freezes the current
configuration with a message; **fork** starts a new branch from a chosen parent
so parallel ideas do not overwrite each other.

While you work on top of an applied node, the HEAD pill describes whether the
runtime is clean, carrying **uncommitted** changes, sitting in a **detached**
view of an older commit, or **adopted** (the graph pointer moved without moving
hardware). Those labels are the same ideas as dirty trees and detached HEAD in
git, expressed for a physical bench.

| Familiar git idea | In Cloud Labs |
|-------------------|---------------|
| Working tree | Runtime (live lab state) |
| Commit | Configuration commit on a control-repo branch |
| Branch / fork | Named line of commits; fork creates a new branch tip |
| Dirty tree | Runtime configuration ≠ applied (or ≠ empty baseline) |
| Stash | Single-slot stash of uncommitted layout, then reconcile back |
| Checkout | Soft preview, hard apply-on-bench, or adopt-without-motion |
| Remote | Catalog **pins** (owner-approved snapshots), not auto-push |

Twin is where you edit this local history. Operations is for watching jobs and
a live mirror—not for committing or stashing. Wiki **Backends → Snapshots**
browses frozen pins and local graphs in a read-oriented way.

## Diff and apply-on-bench

![Soft preview, hard checkout, and adopt](/static/wiki/guides/figures/vc-checkout.svg)

*Soft checkout previews; hard checkout reconciles with primitives; adopt only
moves the graph pointer.*

A **diff** between two configurations is a field-level map of what changed:
poses, motor setpoints, holding, and related intent. When you ask the lab to
**apply** a saved layout on the bench, that diff becomes a **reconcile plan**:
an ordered list of primitives (move, store, set motor, and so on) that walk the
hardware from the current configuration toward the target.

**Soft checkout** (preview) lets Twin show the target layout without insisting
the robots move yet. **Hard checkout** runs the reconcile plan for real, then
marks that commit as applied. **Adopt** updates which node the repo treats as
current without commanding motion—useful when the bench already matches and
you only need the graph to agree.

**Stash** sets uncommitted work aside, reconciles the bench back to the applied
baseline (or the empty baseline when appropriate), and keeps a single slot you
can pop later. Drop discards that slot. This is how you clear the table for
someone else’s baseline without losing your draft forever.

## Local repositories versus catalog pins

Local control repos are private to the deployment and operator workflow: commit
freely, fork, stash, travel history. **Catalog pins** are a second tier—curated,
often owner-approved freezes of configuration that appear under Wiki Backends
**Snapshots**. Publishing from Twin submits a local commit for that path; it
does not silently make every commit a shared pin.

In Python you can load a baseline and drive hardware to match it:

```python
from cloudlabs import connect

with connect("mock.default") as lab:
    lab.load_snapshot(repo="laser-cavity", branch="main")
    lab.reconcile_hardware()
    # … or pin by catalog id when your deployment exposes one …
```

The language ladder’s `01_hello_lab.py --reconcile <repo> <branch>` exercises
the same idea. Prefer an explicit backend id once you know which lab you mean;
see [Connecting and backends](#) for leases and multi-lab choice.

## What this is not

Several nearby features sound like version control and are not the same system.

A **session checkpoint** (`session_last_lab_state.json`) is crash or restart
recovery for the last live runtime. It may offer to restore poses after a
reboot when measurements still match within noise. It is not a branch graph and
not something you fork.

**Recipe golden** files are a legacy recipe-replay baseline. Prefer control-repo
commits and setups for new work.

Confirming a Twin **ghost** into a solid pose runs a primitive; that confirms
intent on the live bench. It does **not** create a ControlManager commit until
you explicitly commit (or stash) in the control graph. For the ghost/solid
story itself, see [Components and the lab model](#).

## Practice

Open Twin on a ready backend, make a small layout change, and watch the HEAD
pill go dirty. Commit with a short message, then soft-preview an older node and
hard-checkout back if you want to feel reconcile. In this Wiki, open
**Backends** for the same lab and skim **Snapshots** so local history and
catalog pins stay distinct in your mind.

Next: [TeleOp, telemetry, and optimize](#)—live control loops on top of a layout
you can also save.
