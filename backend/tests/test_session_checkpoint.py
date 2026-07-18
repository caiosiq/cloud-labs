"""Session checkpoint merge must read statecontrol-shaped component entries."""

from mock_edge.shared.session_checkpoint import (
    ReconciliationThresholds,
    merge_offers_with_debug,
)
from lab_model.language.domain.component import new_component_entry


def _entry(tag_id: str, x: float, y: float, rot: float, *, motor: float | None = None):
    entry = new_component_entry(
        tag_id,
        "OPTICAL_MIRROR",
        presence="breadboard",
        nominal_pose={"x": x, "y": y, "rotation": rot},
        meas_pose={"x": x, "y": y, "rotation": rot},
    )
    if motor is not None:
        entry["statecontrol"]["tunables"]["nominal_motor_positions"] = {"m1": motor}
    return entry


def test_merge_offers_reads_statecontrol_meas_pose():
    th = ReconciliationThresholds(position_mm=5.0, yaw_deg=10.0)
    current = {
        "components": {
            "tag_1": _entry("tag_1", 100.0, 200.0, 45.0),
        }
    }
    checkpoint = {
        "components": {
            "tag_1": _entry("tag_1", 101.0, 199.0, 46.0, motor=12.5),
        }
    }

    offers, debug = merge_offers_with_debug(
        current_state=current,
        checkpoint_state=checkpoint,
        thresholds=th,
    )

    assert offers == ["tag_1"]
    assert debug["shared_tag_count"] == 1
    assert debug["pose_mismatch_tags"] == []


def test_merge_offers_skips_when_poses_differ():
    th = ReconciliationThresholds(position_mm=2.0, yaw_deg=5.0)
    current = {"components": {"tag_1": _entry("tag_1", 0.0, 0.0, 0.0)}}
    checkpoint = {"components": {"tag_1": _entry("tag_1", 50.0, 0.0, 0.0)}}

    offers, debug = merge_offers_with_debug(
        current_state=current,
        checkpoint_state=checkpoint,
        thresholds=th,
    )

    assert offers == []
    assert len(debug["pose_mismatch_tags"]) == 1
