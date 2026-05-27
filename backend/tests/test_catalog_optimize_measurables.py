"""Catalog enrich: optimize-placeable rows declare last_optimization_score."""

from lab_model.catalog.schema import (
    catalog_optimize_strategies,
    enrich_catalog_row_optimize,
    ensure_last_optimization_score_measurable,
    normalize_capabilities,
)


def test_enrich_catalog_row_optimize_adds_last_optimization_score():
    row = {
        "tag_id": "tag_2",
        "type": "OPTICAL_BEAM_BLOCK",
        "capabilities": {
            "statecontrol": {"tunables": {"nominal_pose": {"widget": "TablePose"}}, "measurables": {}},
        },
    }
    assert catalog_optimize_strategies(row)
    enriched = enrich_catalog_row_optimize(row)
    caps = normalize_capabilities(enriched.get("capabilities"))
    meas = (caps.get("statecontrol") or {}).get("measurables") or {}
    assert "last_optimization_score" in meas
    assert meas["last_optimization_score"]["widget"] == "NumberBadge"


def test_ensure_last_optimization_score_measurable_is_idempotent():
    caps = {"statecontrol": {"measurables": {"camera_image": {"widget": "ImageViewer"}}}}
    ensure_last_optimization_score_measurable(caps)
    ensure_last_optimization_score_measurable(caps)
    meas = caps["statecontrol"]["measurables"]
    assert "last_optimization_score" in meas
    assert "camera_image" in meas
