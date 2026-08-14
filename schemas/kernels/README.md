# `schemas/kernels/` — coordinator fixtures only

This directory is **not** the live kernel catalog for remote / edge backends.

| Role | Location |
|------|----------|
| Live catalog (Wiki, Twin `/api/kernels`, OPTIMIZE) | Active edge `cloudlabs_edge/kernels/` |
| CI / local TorchScript unit tests | This tree (`manifest.json` + `.pt`) |
| New-edge starter copy | `packages/cloudlabs_edge_dev/.../scaffold_fixtures/kernels/` |

Rebuild fixtures and re-seed mock / sim / real / scaffold trees:

```bash
python scripts/ops/build_torchscript_kernels.py --seed-edges
```

See `docs/EDGE_OPTIMIZATION_PIPELINE.md` Phase 5.
