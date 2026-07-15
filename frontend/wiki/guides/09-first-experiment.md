# Your first experiment

A minimal path from opening Cloud Labs to a confirmed lab action on a chosen
backend.

## 1. Open Cloud Labs and choose a backend

Go to **http://127.0.0.1:8000/**. The boot gate lists registered backends.
Select one that is **ready** (for local development this is often
`mock.default`). Twin and the other surfaces will use that selection for your
browser session.

## 2. Twin

Open **Twin**. The breadboard map shows:

- **Solid** shapes — measured or believed pose
- **Ghost** shapes — commanded intent

Select a component. The floating panel issues **primitives**—the same action
family scripts use.

## 3. Wiki Catalog

In this Wiki, open **Catalog**. Set the **Backend** dropdown to the same lab.
Select a tag you touched in the Twin. Read **Physical interpretation**, then
skim tunables (command vs report) versus measurables (e.g. camera frames).

## 4. Python

```powershell
pip install -e ./packages/cloudlabs
python scripts/language/01_hello_lab.py --backend mock.default
```

Replace `mock.default` with the backend id you chose. The script:
`connect` (lease) → `prepare` → fluent `move` / `motor` → capture.

## 5. Closed-loop (when available)

If `03_closed_loop_catalog.py` fails with no edge attached, the coordinator is
not running a distributed edge for that backend. Imperative scripts still work;
ask whoever operates the server to attach an edge for hardware closed-loop, or
use the in-process mock path your deployment provides.

## Further reading

| Goal | Location |
|------|----------|
| Backends and `connect()` | Learn → *Connecting and backends* |
| Kernels | Learn → *Kernels*; Catalog → Kernels |
| Closed-loop | `03_closed_loop_catalog.py` |
| Author a measurement | `04_session_kernels.py` |
| Script ladder | `scripts/language/README.md` |
