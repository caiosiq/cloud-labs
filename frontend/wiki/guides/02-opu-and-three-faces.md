# The Optical Processing Unit and three faces

A conventional processor operates on numerical state. An **Optical Processing
Unit (OPU)** operates on light paths: stages translate, motors tip mirrors,
cameras observe the beam, and control loops choose the next actuation. Cloud
Labs is the **control plane** for that machine. It does not replace optics; it
defines how humans and scripts address the lab safely and reproducibly.

## Three faces

Cloud Labs separates three roles that are often conflated in single-process lab
stacks:

![Client, Coordinator, and Edge](/static/wiki/guides/figures/three-faces.svg)

*Three equal faces: Client and Twin speak HTTP; the coordinator validates and queues; the edge owns instruments.*

| Face | Responsibility |
|------|----------------|
| **Client** (author laptop / Twin) | Express intent; hold a **session lease** |
| **Coordinator** (this server) | Validate primitives and jobs; matchmake; queue work |
| **Edge** (bench PC or mock) | Own cameras, motors, and robot; run tight loops |

## What you call vs what runs

The **Client** (Twin or `cloudlabs` script) sends HTTP requests. The
**Coordinator** validates them and tracks state. The **Edge** runs
instruments—or an in-process mock when no separate edge is attached. You do
not configure these roles from your notebook; you **choose a backend** and
**connect** to it (see *Connecting and backends*).

## Meaning of “remote”

Remote control does not mean that the network performs physics. It means:

- **Intent** (primitives / jobs) travels over HTTP.
- **Physical actuation and sensing** remain on the edge, next to the instruments.
- When optimization must be frame-rate sensitive, the **inner loop** also stays
  on the edge (see *Imperative vs closed-loop*).

## Practice

- Open **Operations** while a job runs to see lease and job status for your
  backend.
- Read *Connecting and backends* for how Twin and scripts target the same
  backend id.

Next: [Components and the lab model](#)—tunables, measurables, parameters.
