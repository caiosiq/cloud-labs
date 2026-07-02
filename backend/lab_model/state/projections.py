"""Project runtime JSON into Configuration / Observations / Setup slices."""

from __future__ import annotations

import copy
import json
from typing import Any, Dict, Mapping, Optional

from lab_model.domain.component import (
    PRESENCE_BREADBOARD,
    PRESENCE_OFF_TABLE,
    PRESENCE_STORAGE,
    default_measurables,
    get_measurables,
    get_tunables,
    is_off_table,
    is_stored,
    measurables_bucket,
    normalize_components_map,
    set_presence_and_storage,
    storage_slot,
)
from lab_model.domain.holding import get_holding


# Virtual "zeroth state": the lab with no active components. Every repo's first
# commit is conceptually a branch off this shared empty base, so configurations
# from unrelated repos are always comparable (a diff/reconcile against EMPTY is
# always well-defined). It carries no alignment overlays so that diffing against
# it never produces spurious guide/laser changes (those keys act as wildcards).
EMPTY_CONFIGURATION: Dict[str, Any] = {
    "holding": {"tag_id": None, "nominal_pose": None},
    "components": {},
}


def _config_entry_on_table(entry: Mapping[str, Any]) -> bool:
    """True when a *configuration-shaped* component entry is an active table part.

    Stored and off-table components are inventory, not active lab parts, so they
    are excluded from the versioned configuration (membership model). Table
    membership is realized through STORE_COMPONENT / PLACE_FROM_STORAGE.
    """
    tun = (entry.get("statecontrol") or {}).get("tunables") or {}
    presence = tun.get("presence", PRESENCE_BREADBOARD)
    if presence in (PRESENCE_STORAGE, PRESENCE_OFF_TABLE):
        return False
    storage = tun.get("storage") or {}
    if isinstance(storage, dict) and storage.get("in_storage"):
        return False
    return True


def table_configuration(configuration: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a copy of ``configuration`` keeping only active table components.

    Used to normalize legacy node documents (which embed stored components with
    ``presence: storage``) before diff/reconcile so they read the same way as
    membership-based configurations produced by the current ``extract_*``.
    """
    cfg = dict(configuration or {})
    out = copy.deepcopy(cfg)
    comps = out.get("components")
    if isinstance(comps, dict):
        out["components"] = {
            tag_id: entry
            for tag_id, entry in comps.items()
            if isinstance(entry, dict) and _config_entry_on_table(entry)
        }
    return out


def holding_for_configuration(runtime: Mapping[str, Any]) -> Dict[str, Any]:
    """Holding intent for configuration commits (exclude runtime-only flags)."""
    holding = get_holding(dict(runtime))
    tag_id = holding.get("tag_id")
    nominal_pose = holding.get("nominal_pose")
    out: Dict[str, Any] = {
        "tag_id": tag_id,
        "nominal_pose": copy.deepcopy(nominal_pose) if isinstance(nominal_pose, dict) else None,
    }
    return out


def extract_configuration(runtime: Mapping[str, Any]) -> Dict[str, Any]:
    """Extract the configuration slice from a runtime document."""
    runtime_copy = dict(runtime)
    components_in = runtime_copy.get("components") or {}
    if not isinstance(components_in, dict):
        components_in = {}
    components_out: Dict[str, Any] = {}
    for tag_id, entry in components_in.items():
        if not isinstance(tag_id, str) or not isinstance(entry, dict):
            continue
        # Membership model: stored / off-table parts are inventory, not active
        # lab components, so they are omitted from the versioned configuration.
        if is_stored(entry) or is_off_table(entry):
            continue
        components_out[tag_id] = {
            "id": entry.get("id", tag_id),
            "type": entry.get("type"),
            "statecontrol": {
                "tunables": copy.deepcopy(get_tunables(entry)),
            },
        }
    out: Dict[str, Any] = {
        "holding": holding_for_configuration(runtime_copy),
        "components": components_out,
    }
    # Versioned alignment overlays. Only include when the runtime actually
    # carries them so minimal runtime dicts (unit tests, legacy states) keep
    # their previous configuration shape — the live runtime is always seeded
    # with these keys via the communicator before extraction.
    guides = runtime_copy.get("alignment_guides")
    if isinstance(guides, list):
        out["alignment_guides"] = normalize_alignment_guides(guides)
    laser = runtime_copy.get("laser_lines")
    if isinstance(laser, dict):
        out["laser_lines"] = normalize_laser_lines_doc(laser)
    return out


def normalize_alignment_guides(guides: Any) -> list:
    """Canonical guide list: ``[{id, p1:{x,y}, p2:{x,y}}]`` sorted by id."""
    out = []
    if not isinstance(guides, list):
        return out
    for g in guides:
        if not isinstance(g, dict):
            continue
        gid = g.get("id")
        p1 = g.get("p1")
        p2 = g.get("p2")
        if not gid or not isinstance(p1, dict) or not isinstance(p2, dict):
            continue
        try:
            out.append(
                {
                    "id": str(gid),
                    "p1": {"x": float(p1["x"]), "y": float(p1["y"])},
                    "p2": {"x": float(p2["x"]), "y": float(p2["y"])},
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    out.sort(key=lambda item: item["id"])
    return out


def normalize_laser_lines_doc(doc: Any) -> Dict[str, Any]:
    """Canonical laser overlay slice: ``{snap_line_id, lines:[...]}``."""
    src = doc if isinstance(doc, dict) else {}
    lines_out = []
    src_lines = src.get("lines")
    if isinstance(src_lines, list):
        for ln in src_lines:
            if not isinstance(ln, dict) or not ln.get("id"):
                continue
            entry: Dict[str, Any] = {"id": str(ln["id"])}
            if "name" in ln:
                entry["name"] = ln.get("name")
            if "color" in ln:
                entry["color"] = ln.get("color")
            entry["enabled"] = bool(ln.get("enabled", True))
            for key in ("p1", "p2"):
                pt = ln.get(key)
                if isinstance(pt, dict):
                    try:
                        entry[key] = {"x": float(pt["x"]), "y": float(pt["y"])}
                    except (KeyError, TypeError, ValueError):
                        entry[key] = pt
            lines_out.append(entry)
    lines_out.sort(key=lambda item: item["id"])
    return {"snap_line_id": src.get("snap_line_id"), "lines": lines_out}


def extract_observations(runtime: Mapping[str, Any]) -> Dict[str, Any]:
    """Extract the observations slice from a runtime document."""
    runtime_copy = dict(runtime)
    components_in = runtime_copy.get("components") or {}
    if not isinstance(components_in, dict):
        components_in = {}
    components_out: Dict[str, Any] = {}
    for tag_id, entry in components_in.items():
        if not isinstance(tag_id, str) or not isinstance(entry, dict):
            continue
        components_out[tag_id] = {
            "id": entry.get("id", tag_id),
            "type": entry.get("type"),
            "statecontrol": {
                "measurables": copy.deepcopy(get_measurables(entry)),
            },
        }
    return {"components": components_out}


def build_setup(
    configuration: Mapping[str, Any],
    observations: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Bundle configuration and optional observations into a setup payload."""
    setup: Dict[str, Any] = {
        "configuration": copy.deepcopy(dict(configuration)),
    }
    if observations is not None:
        setup["observations"] = copy.deepcopy(dict(observations))
    return setup


def apply_configuration_to_components(
    runtime: Dict[str, Any],
    configuration: Mapping[str, Any],
) -> None:
    """Soft-apply configuration tunables (+ holding intent) onto runtime in place."""
    cfg = dict(configuration)
    holding = cfg.get("holding")
    if isinstance(holding, dict):
        target = get_holding(runtime)
        target["tag_id"] = holding.get("tag_id")
        np = holding.get("nominal_pose")
        target["nominal_pose"] = copy.deepcopy(np) if isinstance(np, dict) else None

    # Restore versioned alignment overlays (guides + laser lines). These are
    # UI/alignment geometry, not hardware — applying them is a pure state
    # projection (no robot motion), but they ARE part of the configuration.
    if "alignment_guides" in cfg:
        runtime["alignment_guides"] = normalize_alignment_guides(
            cfg.get("alignment_guides")
        )
    if "laser_lines" in cfg:
        runtime["laser_lines"] = normalize_laser_lines_doc(cfg.get("laser_lines"))

    cfg_components = cfg.get("components") or {}
    if not isinstance(cfg_components, dict):
        return
    runtime_components = runtime.setdefault("components", {})
    if not isinstance(runtime_components, dict):
        runtime["components"] = {}
        runtime_components = runtime["components"]

    for tag_id, cfg_entry in cfg_components.items():
        if not isinstance(tag_id, str) or not isinstance(cfg_entry, dict):
            continue
        entry = runtime_components.get(tag_id)
        if not isinstance(entry, dict):
            continue
        sc = cfg_entry.get("statecontrol") or {}
        tun = sc.get("tunables") if isinstance(sc, dict) else None
        if isinstance(tun, dict):
            from lab_model.domain.component import tunables_bucket

            bucket = tunables_bucket(entry)
            bucket.clear()
            bucket.update(copy.deepcopy(tun))

    # Membership reconciliation: a part currently on the breadboard but absent
    # from this configuration is, by definition, NOT on the table here — move it
    # to storage. Stored parts are omitted from the versioned config, so without
    # this, soft-checkout previewing a node where a stored part is placed (and
    # then returning) would leave that part stranded on the breadboard. Already
    # stored / off-table inventory is left untouched.
    cfg_tags = set(cfg_components.keys())
    for tag_id, entry in list(runtime_components.items()):
        if not isinstance(entry, dict) or tag_id in cfg_tags:
            continue
        if is_stored(entry) or is_off_table(entry):
            continue
        set_presence_and_storage(
            entry, PRESENCE_STORAGE, in_storage=True, slot=storage_slot(entry)
        )

    normalize_components_map(runtime_components)


def clear_observations_in_runtime(runtime: Dict[str, Any]) -> None:
    """Reset measurables to defaults (hard checkout clears stale observations)."""
    components_in = runtime.get("components") or {}
    if not isinstance(components_in, dict):
        return
    defaults = default_measurables()
    for entry in components_in.values():
        if not isinstance(entry, dict):
            continue
        bucket = measurables_bucket(entry)
        bucket.clear()
        bucket.update(copy.deepcopy(defaults))


def configuration_equal(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    """Stable JSON equality for tests and idempotency checks."""
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(
        b, sort_keys=True, default=str
    )
