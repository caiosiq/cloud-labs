# Connecting and backends

Cloud Labs can host **multiple labs** on one coordinator. Each registered lab
is a **backend**—for example `mock.default` for a teaching simulator or a
named bench for a real deployment. Everything you do in the Twin, in scripts,
or in this Wiki’s **Catalog** tab targets **one backend at a time**.

Backend choice is an author decision. It is not configured through environment
variables in your notebook or browser.

## Choosing a backend in the UI

When you open Twin, Catalog, or Operations, the **boot gate** lists backends
from `GET /api/backends`. Pick an available (`ready`) backend. That choice is
stored for the browser session and sent on subsequent API calls.

You can switch backends from the session badge (reloads the boot gate). In this
Wiki’s **Catalog** tab, use the **Backend** dropdown to inspect another lab’s
components and kernels without changing your Twin session.

## Choosing a backend in Python

Install the SDK (`pip install -e ./packages/cloudlabs`) and connect with an
explicit or resolved backend id:

```python
from cloudlabs import connect, resolve_backend_id

# Explicit — preferred when you know which lab you need
with connect("mock.default", base_url="http://127.0.0.1:8000") as lab:
    lab.components.tag_20.move(x=10.0).wait_until_idle()

# Resolved — first ready backend from /api/backends
backend_id = resolve_backend_id("http://127.0.0.1:8000")
with connect(backend_id, base_url="http://127.0.0.1:8000") as lab:
    ...
```

Language scripts accept `--backend` for the same purpose, e.g.
`python scripts/language/01_hello_lab.py --backend mock.default`.

`resolve_backend_id()` is convenience, not magic: it queries the server and
picks the first ready entry. For reproducible work, pass the backend id
explicitly once you know which lab you are using.

## Session lease

`connect()` acquires a **session lease** on that backend: exclusive right to
issue mutating primitives for the life of the context manager. If another
notebook or UI session already holds the lease, you will see a lock error.

Release the lease by exiting the `with` block (or closing the client). Long
optimization jobs may use their own lease while your script waits.

## What the coordinator and edge mean for you

You interact with the **coordinator** (the HTTP server). It validates commands,
tracks leases, and queues jobs. **Instrument execution** happens on the
**edge**—the process that owns cameras and motors for that backend.

For imperative moves and captures on a mock backend, the coordinator often
runs the communicator in-process. For frame-rate **OPTIMIZE** on hardware, an
edge process may need to be attached; if closed-loop jobs fail with “no edge,”
that is an infrastructure issue for whoever runs the server—not something you
fix from a script by changing paths or env vars.

## Practice

1. Open **http://127.0.0.1:8000/** and note which backends the boot gate offers.
2. Select one, open **Twin**, and confirm components load.
3. Run `python scripts/language/01_hello_lab.py --backend <that-id>` with the
   same backend id.
4. Wiki → **Catalog** → match the **Backend** dropdown to inspect capabilities.

Next: [Your first experiment](#)—end-to-end walkthrough.
