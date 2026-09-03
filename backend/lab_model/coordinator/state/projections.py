"""Project runtime JSON into Configuration / Observations / Setup slices."""

from __future__ import annotations

import copy
import json
import math
from typing import Any, Dict, Mapping, Optional

from lab_model.language.domain.component import (
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
from lab_model.language.domain.holding import get_holding


# Virtual "zeroth state": the lab with no active components. Every repo's first
# commit is conceptually a branch off this shared empty base, so configurations
# from unrelated repos are always comparable (a diff/reconcile against EMPTY is
# always well-defined). It carries no alignment overlays so that diffing against
# it never produces spurious guide/laser changes (those keys act as wildcards).
EMPTY_CONFIGURATION: Dict[str, Any] = {
    "holding": {"tag_id": None, "nominal_pose": None},
    "components": {},
}

# Tunable keys that annotate how a pose was reached but do not affect reconcile.
NON_RECONCILE_TUNABLE_KEYS = frozenset({"placement"})

# Pose fields are breadboard intent only; storage cells are versioned by slot.
_STORED_NON_VERSIONED_TUNABLE_KEYS = frozenset({"nominal_pose", "reported_pose"})

# Placement modes that are not optimization-sourced (mirrors frontend component-model).
NON_OPTIMIZATION_PLACEMENT_MODES = frozenset({"MANUAL", "STORAGE", "HOVER", "PICK"})


def _normalize_storage_blob(storage: Any) -> Dict[str, Any]:
    """Canonical ``{in_storage, slot:{i,j}|None}`` for versioned inventory."""
    src = storage if isinstance(storage, dict) else {}
    slot_out = None
    slot = src.get("slot")
    if isinstance(slot, dict) and slot.get("i") is not None and slot.get("j") is not None:
        try:
            slot_out = {"i": int(slot["i"]), "j": int(slot["j"])}
        except (TypeError, ValueError):
            slot_out = None
    return {"in_storage": True, "slot": slot_out}


def _tunables_for_configuration_slice(
    tunables: Mapping[str, Any],
    *,
    stored: bool = False,
) -> Dict[str, Any]:
    """Copy tunables for versioned configuration, omitting non-reconcile keys.

    Stored inventory versions **slot only** — cell-center XY/rot is derived at
    apply / preview / hardware time from the lab storage grid.
    """
    out = copy.deepcopy(dict(tunables))
    for key in NON_RECONCILE_TUNABLE_KEYS:
        out.pop(key, None)
    if stored:
        for key in _STORED_NON_VERSIONED_TUNABLE_KEYS:
            out.pop(key, None)
        out["presence"] = PRESENCE_STORAGE
        out["storage"] = _normalize_storage_blob(out.get("storage"))
    return out


def _optimization_entry_from_component(entry: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """Build one tag's optimization metadata, or None if not optimization-sourced."""
    tun = get_tunables(entry)
    meas = get_measurables(entry)
    score = meas.get("last_optimization_score")
    if score is None:
        return None
    try:
        score_f = float(score)
        if not math.isfinite(score_f):
            return None
    except (TypeError, ValueError):
        return None
    pl = tun.get("placement") or {}
    mode = str(pl.get("mode") or "MANUAL").upper()
    if mode in NON_OPTIMIZATION_PLACEMENT_MODES:
        return None
    out: Dict[str, Any] = {
        "placement_mode": mode,
        "last_optimization_score": score_f,
    }
    lop = meas.get("last_optimized_pose")
    if isinstance(lop, dict):
        try:
            out["last_optimized_pose"] = {
                "x": float(lop.get("x", 0.0)),
                "y": float(lop.get("y", 0.0)),
                "rotation": float(lop.get("rotation", 0.0)),
            }
        except (TypeError, ValueError):
            pass
    return out


def extract_configuration_metadata(runtime: Mapping[str, Any]) -> Dict[str, Any]:
    """Non-reconcile annotations stored on configuration commits (optimization outcomes)."""
    components_in = runtime.get("components") or {}
    if not isinstance(components_in, dict):
        return {}
    optimization: Dict[str, Any] = {}
    for tag_id, entry in components_in.items():
        if not isinstance(tag_id, str) or not isinstance(entry, dict):
            continue
        if is_stored(entry) or is_off_table(entry):
            continue
        meta_entry = _optimization_entry_from_component(entry)
        if meta_entry is not None:
            optimization[tag_id] = meta_entry
    if not optimization:
        return {}
    return {"optimization": optimization}


def _optimization_entry_from_legacy_sources(
    cfg_entry: Mapping[str, Any],
    obs_entry: Mapping[str, Any] | None = None,
) -> Optional[Dict[str, Any]]:
    """Infer optimization metadata from a legacy configuration component + optional obs pin."""
    tun = ((cfg_entry.get("statecontrol") or {}).get("tunables") or {})
    if not isinstance(tun, dict):
        tun = {}
    pl = tun.get("placement") or {}
    mode = str(pl.get("mode") or "MANUAL").upper()
    if mode in NON_OPTIMIZATION_PLACEMENT_MODES:
        return None

    score = None
    lop = None
    if isinstance(obs_entry, dict):
        meas = ((obs_entry.get("statecontrol") or {}).get("measurables") or {})
        if isinstance(meas, dict):
            score = meas.get("last_optimization_score")
            lop = meas.get("last_optimized_pose")

    if score is None:
        return None
    try:
        score_f = float(score)
        if not math.isfinite(score_f):
            return None
    except (TypeError, ValueError):
        return None

    out: Dict[str, Any] = {
        "placement_mode": mode,
        "last_optimization_score": score_f,
    }
    if isinstance(lop, dict):
        try:
            out["last_optimized_pose"] = {
                "x": float(lop.get("x", 0.0)),
                "y": float(lop.get("y", 0.0)),
                "rotation": float(lop.get("rotation", 0.0)),
            }
        except (TypeError, ValueError):
            pass
    else:
        np = tun.get("nominal_pose")
        if isinstance(np, dict):
            try:
                out["last_optimized_pose"] = {
                    "x": float(np.get("x", 0.0)),
                    "y": float(np.get("y", 0.0)),
                    "rotation": float(np.get("rotation", 0.0)),
                }
            except (TypeError, ValueError):
                pass
    return out


def infer_configuration_metadata_from_document(
    document: Mapping[str, Any],
    *,
    observations: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build optimization metadata for a stored commit (legacy backfill helper)."""
    configuration = document.get("configuration") or {}
    if not isinstance(configuration, dict):
        return {}
    existing = document.get("metadata") or {}
    existing_opt = (
        existing.get("optimization") if isinstance(existing, dict) else None
    ) or {}
    obs_components = (observations or {}).get("components") or {}
    if not isinstance(obs_components, dict):
        obs_components = {}

    cfg_components = table_configuration(configuration).get("components") or {}
    if not isinstance(cfg_components, dict):
        cfg_components = {}

    optimization: Dict[str, Any] = dict(existing_opt) if isinstance(existing_opt, dict) else {}
    for tag_id, cfg_entry in cfg_components.items():
        if not isinstance(tag_id, str) or not isinstance(cfg_entry, dict):
            continue
        if tag_id in optimization:
            continue
        obs_entry = obs_components.get(tag_id)
        obs_map = obs_entry if isinstance(obs_entry, dict) else None
        entry = _optimization_entry_from_legacy_sources(cfg_entry, obs_map)
        if entry is not None:
            optimization[tag_id] = entry

    if not optimization:
        return {}
    return {"optimization": optimization}


def strip_non_reconcile_tunables_from_configuration(configuration: Dict[str, Any]) -> bool:
    """Remove ``placement`` from stored configuration tunables (legacy normalize). Returns True if changed."""
    changed = False
    comps = configuration.get("components")
    if not isinstance(comps, dict):
        return False
    for entry in comps.values():
        if not isinstance(entry, dict):
            continue
        sc = entry.get("statecontrol")
        if not isinstance(sc, dict):
            continue
        tun = sc.get("tunables")
        if not isinstance(tun, dict):
            continue
        if "placement" in tun:
            del tun["placement"]
            changed = True
    return changed


def normalize_stored_slot_only_in_configuration(configuration: Dict[str, Any]) -> bool:
    """Strip XY/rot from stored inventory entries (slot-only VC). Returns True if changed."""
    comps = configuration.get("components")
    if not isinstance(comps, dict):
        return False
    changed = False
    for entry in comps.values():
        if not isinstance(entry, dict) or not _config_entry_is_stored(entry):
            continue
        sc = entry.get("statecontrol")
        if not isinstance(sc, dict):
            continue
        tun = sc.get("tunables")
        if not isinstance(tun, dict):
            continue
        normalized = _tunables_for_configuration_slice(tun, stored=True)
        if normalized != tun:
            sc["tunables"] = normalized
            changed = True
    return changed


def apply_configuration_metadata(
    runtime: Dict[str, Any],
    metadata: Mapping[str, Any] | None,
) -> None:
    """Restore optimization annotations onto runtime after checkout (display only)."""
    if not metadata:
        return
    optimization = metadata.get("optimization")
    if not isinstance(optimization, dict):
        return
    components = runtime.get("components") or {}
    if not isinstance(components, dict):
        return
    for tag_id, meta_entry in optimization.items():
        if not isinstance(tag_id, str) or not isinstance(meta_entry, dict):
            continue
        entry = components.get(tag_id)
        if not isinstance(entry, dict):
            continue
        mode = meta_entry.get("placement_mode")
        if isinstance(mode, str) and mode.strip():
            from lab_model.language.domain.component import tunables_bucket

            tunables_bucket(entry)["placement"] = {"mode": mode.strip().upper()}
        score = meta_entry.get("last_optimization_score")
        if score is not None:
            try:
                measurables_bucket(entry)["last_optimization_score"] = float(score)
            except (TypeError, ValueError):
                pass
        lop = meta_entry.get("last_optimized_pose")
        if isinstance(lop, dict):
            try:
                measurables_bucket(entry)["last_optimized_pose"] = {
                    "x": float(lop.get("x", 0.0)),
                    "y": float(lop.get("y", 0.0)),
                    "rotation": float(lop.get("rotation", 0.0)),
                }
            except (TypeError, ValueError):
                pass


def _config_entry_is_stored(entry: Mapping[str, Any]) -> bool:
    """True when a configuration-shaped entry is inventory (storage), not breadboard."""
    tun = (entry.get("statecontrol") or {}).get("tunables") or {}
    if not isinstance(tun, dict):
        return False
    presence = tun.get("presence", PRESENCE_BREADBOARD)
    if presence == PRESENCE_STORAGE:
        return True
    storage = tun.get("storage") or {}
    return isinstance(storage, dict) and bool(storage.get("in_storage"))


def _config_entry_is_off_table(entry: Mapping[str, Any]) -> bool:
    tun = (entry.get("statecontrol") or {}).get("tunables") or {}
    if not isinstance(tun, dict):
        return False
    return tun.get("presence") == PRESENCE_OFF_TABLE


def _config_entry_on_breadboard(entry: Mapping[str, Any]) -> bool:
    """True for breadboard-only entries (excludes storage and off-table)."""
    if not isinstance(entry, dict):
        return False
    if _config_entry_is_off_table(entry) or _config_entry_is_stored(entry):
        return False
    return True


def _config_entry_in_lab(entry: Mapping[str, Any]) -> bool:
    """True for versioned lab layout entries: breadboard or storage (not off-table)."""
    if not isinstance(entry, dict):
        return False
    return not _config_entry_is_off_table(entry)


def configuration_versions_storage(configuration: Mapping[str, Any]) -> bool:
    """True when ``configuration`` records at least one stored inventory entry.

    Legacy commits (and the empty baseline) omit storage; dirtiness against those
    bases ignores ambient inventory. New commits that capture slots use full
    lab-layout diffs.
    """
    comps = (configuration or {}).get("components") or {}
    if not isinstance(comps, dict):
        return False
    return any(
        isinstance(entry, dict) and _config_entry_is_stored(entry)
        for entry in comps.values()
    )


def table_configuration(configuration: Mapping[str, Any]) -> Dict[str, Any]:
    """Copy keeping only breadboard components (strip storage + off-table).

    Used for empty-baseline / legacy dirty checks where inventory layout is not
    part of the versioned contract. Prefer :func:`lab_configuration` for
    reconcile and for commits that version storage slots.
    """
    cfg = dict(configuration or {})
    out = copy.deepcopy(cfg)
    comps = out.get("components")
    if isinstance(comps, dict):
        out["components"] = {
            tag_id: entry
            for tag_id, entry in comps.items()
            if isinstance(entry, dict) and _config_entry_on_breadboard(entry)
        }
    return out


def lab_configuration(configuration: Mapping[str, Any]) -> Dict[str, Any]:
    """Copy keeping breadboard + storage inventory (strip off-table only).

    Stored entries are normalized to slot-only (no ``nominal_pose``) so legacy
    commits that embedded cell XY compare cleanly against current extracts.
    """
    cfg = dict(configuration or {})
    out = copy.deepcopy(cfg)
    comps = out.get("components")
    if isinstance(comps, dict):
        cleaned: Dict[str, Any] = {}
        for tag_id, entry in comps.items():
            if not isinstance(entry, dict) or not _config_entry_in_lab(entry):
                continue
            if _config_entry_is_stored(entry):
                sc = entry.setdefault("statecontrol", {})
                if not isinstance(sc, dict):
                    sc = {}
                    entry["statecontrol"] = sc
                tun = sc.get("tunables") if isinstance(sc.get("tunables"), dict) else {}
                sc["tunables"] = _tunables_for_configuration_slice(tun, stored=True)
            cleaned[tag_id] = entry
        out["components"] = cleaned
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
    """Extract the configuration slice from a runtime document.

    Versioned layout includes breadboard parts (with pose) and storage
    inventory (**slot only**). Off-table tags remain unversioned. Cell-center
    pose for stored parts is derived from the slot at apply / reconcile time.
    """
    runtime_copy = dict(runtime)
    components_in = runtime_copy.get("components") or {}
    if not isinstance(components_in, dict):
        components_in = {}
    components_out: Dict[str, Any] = {}
    for tag_id, entry in components_in.items():
        if not isinstance(tag_id, str) or not isinstance(entry, dict):
            continue
        # Off-table stays outside VC; storage is part of commanded lab layout.
        if is_off_table(entry):
            continue
        stored = is_stored(entry)
        components_out[tag_id] = {
            "id": entry.get("id", tag_id),
            "type": entry.get("type"),
            "statecontrol": {
                "tunables": _tunables_for_configuration_slice(
                    get_tunables(entry), stored=stored
                ),
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
    """Canonical laser overlay slice: ``{snap_line_id, lines:[...]}``.

    Migrates legacy id ``ne_he`` → ``he_ne`` (and display name Ne–He → He–Ne)
    so Twin / command console / dock stay consistent without breaking geometry.
    """
    src = doc if isinstance(doc, dict) else {}
    # Historical typo / swap: Neon–Helium id → Helium–Neon.
    id_aliases = {"ne_he": "he_ne"}
    lines_out = []
    src_lines = src.get("lines")
    if isinstance(src_lines, list):
        for ln in src_lines:
            if not isinstance(ln, dict) or not ln.get("id"):
                continue
            raw_id = str(ln["id"])
            canon_id = id_aliases.get(raw_id, raw_id)
            entry: Dict[str, Any] = {"id": canon_id}
            if "name" in ln:
                entry["name"] = ln.get("name")
            if canon_id == "he_ne":
                raw_name = str(entry.get("name") or "")
                normalized = (
                    raw_name.replace("\u2013", "-").replace("\u2014", "-").strip().lower()
                )
                if not raw_name or normalized in ("ne-he", "ne_he", "nehe"):
                    entry["name"] = "He\u2013Ne"
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
    # Dedupe if both ne_he and he_ne were present (keep he_ne).
    by_id: Dict[str, Dict[str, Any]] = {}
    for entry in lines_out:
        by_id[str(entry["id"])] = entry
    lines_out = list(by_id.values())
    lines_out.sort(key=lambda item: item["id"])
    snap = src.get("snap_line_id")
    if isinstance(snap, str) and snap in id_aliases:
        snap = id_aliases[snap]
    return {"snap_line_id": snap, "lines": lines_out}


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
            from lab_model.language.domain.component import tunables_bucket
            from lab_model.language.domain.storage_region import (
                STORAGE_NOMINAL_ROTATION_DEG,
                cell_center,
            )

            bucket = tunables_bucket(entry)
            bucket.clear()
            bucket.update(copy.deepcopy(tun))
            # Slot-only configs: derive commanded cell-center pose for Twin UI.
            if _config_entry_is_stored(cfg_entry) or is_stored(entry):
                slot = storage_slot(entry)
                if slot is not None:
                    cx, cy = cell_center(int(slot["i"]), int(slot["j"]))
                    bucket["nominal_pose"] = {
                        "x": float(cx),
                        "y": float(cy),
                        "rotation": float(STORAGE_NOMINAL_ROTATION_DEG),
                    }
                    bucket["presence"] = PRESENCE_STORAGE
                    bucket["storage"] = {
                        "in_storage": True,
                        "slot": {"i": int(slot["i"]), "j": int(slot["j"])},
                    }

    # Membership reconciliation: a breadboard part absent from this configuration
    # is not on the table here — move it to storage. Inventory already stored (or
    # off-table) and absent from a legacy breadboard-only config is left alone;
    # when the config versions storage slots, those entries are applied above via
    # tunables (presence + slot; pose derived from the grid).
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
