"""SYNC_RUNTIME macro — RECORD(recordable) + SET(set_at_init) for inventory.

See ``docs/RECORD_TUNABLES_AND_SYNC_RUNTIME.md``.

Conceptual expansion (order per component: set_at_init first, then record):

  for each inventory component C:
      for each tunable T on C:
          if not T.recordable:
              SET T to T.set_at_init
          else:
              RECORD_TUNABLES(C, T)

LOCALIZE_COMPONENTS expands to RECORD_TUNABLES for ``nominal_pose`` only.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Mapping, Optional, Sequence

from ..schemas import (
    LocalizeComponentsBody,
    RecordTunablesBody,
    RecordTunablesParameters,
    SyncRuntimeBody,
)

_LOG = logging.getLogger(__name__)


def tunable_is_recordable(desc: Any, *, field_id: str) -> bool:
    """Return whether a tunable descriptor is recordable.

    Omitted ``recordable`` defaults to False, except ``nominal_pose`` which
    defaults to True (primary boot measurement).
    """
    if not isinstance(desc, dict):
        return field_id == "nominal_pose"
    if "recordable" in desc:
        return bool(desc.get("recordable"))
    return field_id == "nominal_pose"


def tunable_set_at_init(desc: Any) -> Any:
    if not isinstance(desc, dict):
        return None
    return desc.get("set_at_init")


def validate_tunable_record_metadata(
    field_id: str,
    desc: Any,
    *,
    scope: str,
) -> List[str]:
    """Return catalog errors/warnings for recordable / set_at_init rules.

    Hard-fail only when ``recordable`` is explicitly ``false`` without
    ``set_at_init``. Omitted ``recordable`` on legacy catalogs yields a soft
    warning so existing libraries still load during migration.
    """
    errors: List[str] = []
    if not isinstance(desc, dict):
        return errors
    if desc.get("recordable") is True:
        return errors
    if desc.get("recordable") is False:
        if "set_at_init" not in desc:
            errors.append(
                f"{scope}.{field_id}: recordable:false requires set_at_init"
            )
        return errors
    # recordable omitted
    if field_id == "nominal_pose":
        return errors
    if "set_at_init" not in desc:
        errors.append(
            f"__WARN__{scope}.{field_id}: declare recordable:true or "
            f"recordable:false with set_at_init (SYNC_RUNTIME)"
        )
    return errors


def _library_rows_from_lab(lab: Any) -> Dict[str, Dict[str, Any]]:
    """Best-effort tag_id → library/catalog row from the communicator."""
    rows: Dict[str, Dict[str, Any]] = {}
    for attr in ("get_component_library", "component_library", "library"):
        fn = getattr(lab, attr, None)
        raw = None
        if callable(fn):
            try:
                raw = fn()
            except Exception:  # noqa: BLE001
                raw = None
        elif isinstance(fn, Mapping):
            raw = fn
        if not isinstance(raw, Mapping):
            continue
        comps = raw.get("components") if "components" in raw else raw
        if not isinstance(comps, Mapping):
            continue
        for key, entry in comps.items():
            if isinstance(entry, dict):
                tid = str(entry.get("tag_id") or key)
                rows[tid] = entry
        if rows:
            return rows
    return rows


def _inventory_tag_ids(lab: Any) -> List[str]:
    """Tags that SYNC should cover when parameters.tag_ids is omitted."""
    for attr in ("get_inventory", "inventory"):
        fn = getattr(lab, attr, None)
        raw = None
        if callable(fn):
            try:
                raw = fn()
            except Exception:  # noqa: BLE001
                raw = None
        elif isinstance(fn, Mapping):
            raw = fn
        if not isinstance(raw, Mapping):
            continue
        # Edge inventory uses ``entries``; some hosts still expose ``components``.
        comps = raw.get("entries") if isinstance(raw.get("entries"), Mapping) else None
        if comps is None:
            comps = raw.get("components")
        if not isinstance(comps, Mapping):
            continue
        out: List[str] = []
        for tid, entry in comps.items():
            if not isinstance(entry, dict):
                out.append(str(tid))
                continue
            if entry.get("localize") is False:
                continue
            placement = str(entry.get("placement") or "table").strip().lower()
            if placement in {"table", "storage", ""}:
                out.append(str(tid))
        if out:
            return out
    # Fallback: live lab-state component keys
    state = {}
    if hasattr(lab, "get_lab_state"):
        try:
            state = lab.get_lab_state() or {}
        except Exception:  # noqa: BLE001
            state = {}
    comps = (state or {}).get("components") or {}
    if isinstance(comps, Mapping):
        return [str(t) for t in comps.keys()]
    return []


def _tunables_for_tag(row: Optional[Mapping[str, Any]], tag_id: str) -> Dict[str, Any]:
    if not isinstance(row, Mapping):
        # Minimal default: recordable pose only
        return {"nominal_pose": {"widget": "TablePose", "recordable": True}}
    caps = row.get("capabilities") or {}
    if not isinstance(caps, Mapping):
        return {"nominal_pose": {"widget": "TablePose", "recordable": True}}
    sc = caps.get("statecontrol") or {}
    if not isinstance(sc, Mapping):
        # Legacy flat tunables
        tun = caps.get("tunables") or {}
        return dict(tun) if isinstance(tun, Mapping) else {
            "nominal_pose": {"widget": "TablePose", "recordable": True}
        }
    tun = sc.get("tunables") or {}
    if isinstance(tun, Mapping) and tun:
        return dict(tun)
    return {"nominal_pose": {"widget": "TablePose", "recordable": True}}


def build_sync_plan(
    lab: Any,
    *,
    tag_ids: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """Return ordered plan steps: {kind: set|record, tag_id, path, value?}."""
    tags = [str(t).strip() for t in (tag_ids or _inventory_tag_ids(lab)) if str(t).strip()]
    library = _library_rows_from_lab(lab)
    plan: List[Dict[str, Any]] = []
    for tid in tags:
        tunables = _tunables_for_tag(library.get(tid), tid)
        # SET first so record overwrites measured truth after defaults land
        for path, desc in sorted(tunables.items()):
            if tunable_is_recordable(desc, field_id=path):
                continue
            value = tunable_set_at_init(desc)
            plan.append(
                {
                    "kind": "set",
                    "tag_id": tid,
                    "path": path,
                    "value": value,
                }
            )
        record_paths = [
            path
            for path, desc in sorted(tunables.items())
            if tunable_is_recordable(desc, field_id=path)
        ]
        if record_paths:
            plan.append(
                {
                    "kind": "record",
                    "tag_id": tid,
                    "paths": record_paths,
                }
            )
    return plan


async def _apply_set_at_init(lab: Any, tag_id: str, path: str, value: Any) -> None:
    if path == "exposure_time_ms" and value is not None:
        await lab.set_exposure_time_ms(tag_id, float(value))
        return
    if path == "output_power_mw" and value is not None:
        await lab.set_output_power_mw(tag_id, float(value))
        return
    if path == "nominal_motor_positions" and isinstance(value, Mapping):
        for motor_id, angle in value.items():
            await lab.set_motor_setpoint(tag_id, int(motor_id), float(angle))
        return
    if path == "nominal_pose" and isinstance(value, Mapping):
        # Intent-only seed via tunable apply when available
        from lab_model.language.tunables import nominal_pose as pose_tunable

        await pose_tunable.apply(lab, tag_id, value)
        return
    _LOG.warning(
        "SYNC_RUNTIME: no SET handler for %s.%s (value=%r); skipping",
        tag_id,
        path,
        value,
    )


async def _record_tunables(
    lab: Any,
    *,
    tag_ids: Sequence[str],
    tunable_paths: Sequence[str],
    force_rescan: bool = True,
) -> Any:
    """Invoke lab.record_tunables, with LOCALIZE fallback for pose-only."""
    for tid in tag_ids:
        for path in tunable_paths:
            _LOG.info(
                "[lab_init] RECORD_TUNABLES tag=%s path=%s force_rescan=%s",
                tid,
                path,
                force_rescan,
            )
    fn = getattr(lab, "record_tunables", None)
    if callable(fn):
        result = await fn(
            tag_ids=list(tag_ids),
            tunable_paths=list(tunable_paths),
            force_rescan=force_rescan,
        )
        for tid in tag_ids:
            for path in tunable_paths:
                _LOG.info("[lab_init] RECORD_TUNABLES tag=%s path=%s ok", tid, path)
        return result
    # Transition: pose-only via legacy localize_components
    paths = [str(p) for p in tunable_paths]
    if paths and all(p == "nominal_pose" for p in paths):
        loc = getattr(lab, "localize_components", None)
        if callable(loc):
            _LOG.info(
                "[lab_init] RECORD_TUNABLES falling back to localize_components "
                "for tags=%s",
                list(tag_ids),
            )
            return await loc(tag_ids=list(tag_ids), force_rescan=force_rescan)
    raise NotImplementedError(
        "lab.record_tunables is required for RECORD_TUNABLES "
        f"(tags={list(tag_ids)} paths={list(tunable_paths)})"
    )


async def run_record_tunables(lab: Any, cmd: RecordTunablesBody) -> Any:
    p = cmd.parameters
    _LOG.info(
        "[lab_init] RECORD_TUNABLES begin tags=%s paths=%s",
        p.tag_ids,
        p.tunable_paths,
    )
    return await _record_tunables(
        lab,
        tag_ids=p.tag_ids,
        tunable_paths=p.tunable_paths,
        force_rescan=p.force_rescan,
    )


async def run_localize_as_record(lab: Any, cmd: LocalizeComponentsBody) -> Any:
    """LOCALIZE_COMPONENTS → RECORD_TUNABLES(nominal_pose)."""
    p = cmd.parameters
    tag_ids = list(p.tag_ids) if p.tag_ids else _inventory_tag_ids(lab)
    if not tag_ids:
        _LOG.warning("LOCALIZE_COMPONENTS alias: no tags in scope")
        return {"tag_ids": [], "poses": {}, "via": "RECORD_TUNABLES"}
    body = RecordTunablesBody(
        action="RECORD_TUNABLES",
        parameters=RecordTunablesParameters(
            tag_ids=tag_ids,
            tunable_paths=["nominal_pose"],
            force_rescan=p.force_rescan,
        ),
    )
    return await run_record_tunables(lab, body)


async def run_sync_runtime(lab: Any, cmd: SyncRuntimeBody) -> Dict[str, Any]:
    p = cmd.parameters
    plan = build_sync_plan(lab, tag_ids=p.tag_ids)
    recordable_n = sum(1 for s in plan if s.get("kind") == "record")
    set_n = sum(1 for s in plan if s.get("kind") == "set")
    _LOG.info(
        "[lab_init] runtime_sync status=running steps=%d "
        "recordable_groups=%d set_at_init=%d force_rescan=%s",
        len(plan),
        recordable_n,
        set_n,
        p.force_rescan,
    )
    errors: List[str] = []
    for step in plan:
        kind = step.get("kind")
        tid = str(step.get("tag_id") or "")
        try:
            if kind == "set":
                path = str(step.get("path") or "")
                _LOG.info(
                    "[lab_init] SYNC set_at_init tag=%s path=%s",
                    tid,
                    path,
                )
                await _apply_set_at_init(lab, tid, path, step.get("value"))
            elif kind == "record":
                paths = list(step.get("paths") or [])
                await _record_tunables(
                    lab,
                    tag_ids=[tid],
                    tunable_paths=paths,
                    force_rescan=p.force_rescan,
                )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{tid}:{kind}:{exc}")
            _LOG.exception(
                "[lab_init] runtime_sync step failed tag=%s kind=%s", tid, kind
            )
    status = "ready" if not errors else "failed"
    _LOG.info(
        "[lab_init] runtime_sync status=%s recordable_groups=%d "
        "set_at_init=%d errors=%d",
        status,
        recordable_n,
        set_n,
        len(errors),
    )
    # Best-effort stamp on lab for coordinator READY checks
    stamp = getattr(lab, "set_runtime_sync_status", None)
    if callable(stamp):
        try:
            stamp(status, errors=errors)
        except Exception:  # noqa: BLE001
            pass
    result = {
        "status": status,
        "steps": len(plan),
        "recordable_groups": recordable_n,
        "set_at_init": set_n,
        "errors": errors,
    }
    if errors:
        raise RuntimeError(f"SYNC_RUNTIME failed: {errors}")
    return result
