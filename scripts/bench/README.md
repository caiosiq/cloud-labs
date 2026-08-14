# Bench scripts (operator / real hardware)

Practical `cloudlabs` scripts aimed at a live bench — not the Wiki language ladder.

| Script | Purpose |
|--------|---------|
| `cheat_sheet.py` | Interactive menu: list / move / capture (+ save & plot) / kernels / live feed / teleop / cobyla |
| `hold_teleop.py` | Hold + TeleOp smoke (default `tag_9`): move → pick → hover → teleop → place |
| `camera_kernels.py` | Camera measurables + kernels (default `tag_22`): capture/plot, probe, live feed |

Captures land in `scripts/bench/captures/` (`.npy` / `.png` / `.json`).

Defaults to **`real.default`**. Coordinator must be running; real edge reachable.

```powershell
pip install -e ./packages/cloudlabs
python scripts/bench/cheat_sheet.py
python scripts/bench/hold_teleop.py
python scripts/bench/camera_kernels.py
python scripts/bench/camera_kernels.py --once smoke
python scripts/bench/camera_kernels.py --camera tag_22 --once capture
```

Teaching demos: [`../language/`](../language/). Ops tooling: [`../ops/`](../ops/).
