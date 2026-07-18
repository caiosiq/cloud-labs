# Ensemble optimization example payloads

Copy-paste bodies for `POST /api/command`. See **[`docs/ENSEMBLE_OPTIMIZATION.md`](../../docs/ENSEMBLE_OPTIMIZATION.md)** §18 (mock walkthrough).

| File | Use case |
|------|----------|
| [`two_mirror_mock.json`](./two_mirror_mock.json) | Default mock demo: `tag_20` + `tag_18`, motors 1 & 3, centroid + power |
| [`real_bench_centroid_laser.json`](./real_bench_centroid_laser.json) | Real-bench style centroid + laser objective |
| [`torchscript_image_mean.json`](./torchscript_image_mean.json) | TorchScript image-mean score term |

**Requirements (mock):** mirrors on the breadboard with `nominal_motor_positions` for the listed motor IDs. Pre-flight returns **400** if a path or tag is invalid.
