"""Migrate ``component_library.json`` from legacy array → v1 object shape.

Reads each target file, infers a default ``capabilities`` block per row
(see ``catalog_schema.infer_default_capabilities`` — type-based presets),
wraps everything under ``{schema_version: 1, components: {tag_id: ...}}``
and writes back atomically.

Idempotency: if a file is already in v1 shape, the script **refreshes**
the inferred capability defaults for entries that are missing the block.
It will NOT clobber a hand-edited capabilities block unless --force is
passed (in which case every capability block is regenerated from
presets). Either way, the rest of each component entry (id, type,
properties, motor_ids, ...) is preserved byte-for-byte.

Usage::

    # Migrate the default bundles (mock + real)
    python scripts/archive/migrate_component_library_to_capabilities.py

    # Migrate a specific path
    python scripts/archive/migrate_component_library_to_capabilities.py PATH [PATH ...]

    # Force regeneration of capabilities (drop any hand-edits)
    python scripts/archive/migrate_component_library_to_capabilities.py --force

    # Dry-run (print resulting JSON, don't write)
    python scripts/archive/migrate_component_library_to_capabilities.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from typing import Any, Dict, Iterable, List, Tuple


# ---------------------------------------------------------------------------
# Path bootstrap: this script may be invoked from the repo root, so add
# ``backend/`` to sys.path before importing the schema module.
# ---------------------------------------------------------------------------

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BACKEND = os.path.join(_REPO_ROOT, "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from lab_model.coordinator.catalog.schema import (  # noqa: E402
    SUPPORTED_SCHEMA_VERSIONS,
    infer_default_capabilities,
    is_legacy_array_shape,
    is_v1_object_shape,
    validate_catalog_v1,
)


DEFAULT_TARGETS = (
    os.path.join(_REPO_ROOT, "backend", "lab_communicator", "mock", "lab_view", "component_library.json"),
    os.path.join(_REPO_ROOT, "backend", "lab_communicator", "real", "lab_view", "default", "component_library.json"),
)


# ---------------------------------------------------------------------------
# Core migration
# ---------------------------------------------------------------------------

def _normalize_legacy_rows(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Turn a legacy array of rows into ``{tag_id: row}`` keyed dict.

    Rows missing ``tag_id`` are dropped with a warning; the migration is a
    one-shot operation so silent drops are unacceptable here.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            print(f"[migrate]  skip (not an object): {row!r}")
            continue
        tid = row.get("tag_id")
        if not isinstance(tid, str) or not tid:
            print(f"[migrate]  skip (missing tag_id): {row.get('id') or row}")
            continue
        if tid in out:
            raise ValueError(f"duplicate tag_id {tid!r} in legacy catalog")
        out[tid] = dict(row)
    return out


def migrate_one(
    path: str,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> Tuple[str, Dict[str, Any]]:
    """Migrate one ``component_library.json`` and return ``(action, doc)``.

    ``action`` is one of ``"already_v1"``, ``"refreshed"``, ``"migrated"``.
    ``doc`` is the resulting JSON document (whether written or not).
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    components: Dict[str, Dict[str, Any]] = {}
    action: str

    if is_v1_object_shape(data):
        version = data.get("schema_version")
        if version not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(
                f"{path}: schema_version={version!r} not supported "
                f"(this build accepts {sorted(SUPPORTED_SCHEMA_VERSIONS)})"
            )
        components = dict(data.get("components") or {})
        action = "already_v1"
    elif is_legacy_array_shape(data):
        components = _normalize_legacy_rows(list(data))
        action = "migrated"
    else:
        raise ValueError(
            f"{path}: top-level must be a JSON array (legacy) or "
            f"{{schema_version, components}} object (v1)"
        )

    # Backfill / refresh capabilities. The migration script is the
    # authoritative source of *defaults*; hand-edits win unless --force.
    refreshed_any = False
    for tag_id, entry in components.items():
        if not isinstance(entry, dict):
            raise ValueError(f"{path}:components[{tag_id!r}] must be an object")
        entry.setdefault("tag_id", tag_id)
        has_caps = isinstance(entry.get("capabilities"), dict)
        if force or not has_caps:
            entry["capabilities"] = infer_default_capabilities(entry)
            refreshed_any = True

    if action == "already_v1" and refreshed_any:
        action = "refreshed"

    doc: Dict[str, Any] = {
        "schema_version": 1,
        "components": components,
    }

    # Hard-validate the resulting doc (catches typos in presets, mismatched
    # tag_ids, etc. -- §15.1 hard fails).
    validate_catalog_v1(doc, source=path)

    if not dry_run and (action != "already_v1" or force):
        _atomic_write_json(path, doc)

    return action, doc


def _atomic_write_json(path: str, doc: Dict[str, Any]) -> None:
    """Write ``doc`` to ``path`` atomically (tmp file + os.replace)."""
    dir_ = os.path.dirname(os.path.abspath(path))
    os.makedirs(dir_, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".catalog.", suffix=".json.tmp", dir=dir_)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        if os.path.isfile(tmp):
            os.remove(tmp)
        raise


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: Iterable[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    p.add_argument(
        "paths",
        nargs="*",
        help="Catalog files to migrate (default: mock + real bundles)",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Regenerate every component's capabilities block from presets, "
             "overwriting any hand-edits.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the resulting JSON without writing to disk.",
    )
    return p.parse_args(list(argv))


def main(argv: Iterable[str] = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    targets = list(args.paths) or list(DEFAULT_TARGETS)
    rc = 0
    for path in targets:
        rel = os.path.relpath(path, _REPO_ROOT) if path.startswith(_REPO_ROOT) else path
        try:
            action, doc = migrate_one(path, force=args.force, dry_run=args.dry_run)
        except FileNotFoundError:
            print(f"[migrate] SKIP (missing): {rel}")
            continue
        except Exception as exc:  # pragma: no cover -- script-level error reporting
            print(f"[migrate] FAIL ({rel}): {exc}")
            rc = 1
            continue
        n = len(doc.get("components") or {})
        if args.dry_run:
            print(f"[migrate] DRY-RUN {action} ({n} components): {rel}")
            print(json.dumps(doc, indent=2, ensure_ascii=False))
        else:
            print(f"[migrate] {action} ({n} components): {rel}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
