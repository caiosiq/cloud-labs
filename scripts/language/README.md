# Language scripts (cloudlabs SDK)

Curated ladder for the imperative Python SDK. Platform/ops tools live in [`../ops/`](../ops/).

**Model:** you speak **primitives** (move, probe/`EVAL_KERNEL`, optimize). Kernels are **artifacts + inputs** to those actions — not peer verbs. `register_kernel` uploads a package; it is not a lab action.

Prerequisites: FastAPI on `:8000` and `pip install -e ./packages/cloudlabs`.

| Script | Teaches |
|--------|---------|
| `01_hello_lab.py` | `connect` / lease, `prepare`, fluent move/motor, capture + tensor |
| `02_kernels_and_match.py` | `probe_kernel` (EVAL_KERNEL), builtins, `kernel_match` → OPTIMIZE |
| `03_closed_loop_catalog.py` | Catalog pin + catalog TorchScript as OPTIMIZE inputs (edge loop) |
| `04_session_kernels.py` | Author artifact → register → probe → feature OPTIMIZE |
| `05_jobs_and_modes.py` | Objective compile, `submit_compiled_dag`, closed-loop job |
| `06_client_side_measurable_loop.py` | Author-in-the-loop: resolve `camera_image` locally, `for` loop on laptop (no kernel / no OPTIMIZE) |
| `07_live_plane.py` | Twin ≡ SDK: `start_live_feed` → latch `capture_measurable` → `end_live_feed` (Tier C lab-state ≠ live science) |

```powershell
python scripts/language/01_hello_lab.py
python scripts/language/02_kernels_and_match.py
python scripts/language/03_closed_loop_catalog.py
python scripts/language/04_session_kernels.py
python scripts/language/05_jobs_and_modes.py
python scripts/language/06_client_side_measurable_loop.py
python scripts/language/07_live_plane.py
```