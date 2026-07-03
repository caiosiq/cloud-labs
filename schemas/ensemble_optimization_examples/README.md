# Ensemble optimization example payloads

Copy-paste bodies for `POST /api/command`. See **[`docs/ENSEMBLE_OPTIMIZATION.md`](../../docs/ENSEMBLE_OPTIMIZATION.md)** §18 (mock walkthrough).

| File | Use case |
|------|----------|
| [`two_mirror_mock.json`](./two_mirror_mock.json) | Default mock demo: `tag_20` + `tag_18`, motors 1 & 3, centroid + power |
| [`single_mirror_cobyla_legacy.json`](./single_mirror_cobyla_legacy.json) | One-tag migration from legacy `COBYLA` + `motor_ids: [1, 3]` |

**Requirements (mock):** mirrors on the breadboard with `nominal_motor_positions` for the listed motor IDs. Pre-flight returns **400** if a path or tag is invalid.
