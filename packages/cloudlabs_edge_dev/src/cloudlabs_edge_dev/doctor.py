"""Local readiness checks for an edge tree + installed kit (no live edge required)."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from cloudlabs_edge_dev import CONTRACT_VERSION, __version__ as KIT_VERSION
from cloudlabs_edge_dev.edge_data import (
    LibraryValidationError,
    capabilities_wants_runtime_sync,
    load_inventory,
    load_library,
    validate_inventory_against_library,
    validate_library_document,
)
from cloudlabs_edge_dev.scaffold import REQUIRED_EDGE_FILES as _REQUIRED_EDGE_FILES
from cloudlabs_edge_dev.schemas_path import contract_schemas_dir, load_schema

_SCHEMA_FILES = (
    "capabilities.schema.json",
    "bench.schema.json",
    "library.schema.json",
    "inventory.schema.json",
    "execute_request.schema.json",
    "execute_response.schema.json",
    "measurable_live_decl.schema.json",
    "teleop_ws_client.schema.json",
    "teleop_ws_server.schema.json",
    "epoch_packet.schema.json",
)

_RUNTIME_SYNC_PRIMITIVES = frozenset({"RECORD_TUNABLES", "SYNC_RUNTIME"})


def _schema_registry() -> Registry:
    registry: Registry = Registry()
    for name in _SCHEMA_FILES:
        try:
            schema = load_schema(name)
        except FileNotFoundError:
            continue
        resource = Resource.from_contents(schema)
        uri = schema.get("$id") or name
        registry = registry.with_resource(uri, resource)
        registry = registry.with_resource(name, resource)
    return registry


def _validate_instance(instance: Any, schema_name: str) -> str | None:
    try:
        schema = load_schema(schema_name)
        Draft202012Validator(schema, registry=_schema_registry()).validate(instance)
        return None
    except Exception as e:  # noqa: BLE001
        return str(e)

_STUB_PRIMITIVES = frozenset(
    {
        "START_LIVE_FEED",
        "END_LIVE_FEED",
        "START_TELEOP",
        "END_TELEOP",
        "RECORD_MEASURABLES",
        "EVAL_KERNEL",
    }
)


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""
    level: str = "error"  # error | warn | info


@dataclass
class DoctorReport:
    """Static readiness report (kit + optional local ``cloudlabs_edge/`` tree)."""

    kit_version: str
    contract_version_expected: str
    edge_root: Optional[str] = None
    results: list[CheckResult] = field(default_factory=list)
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results if r.level == "error")

    @property
    def warnings(self) -> list[CheckResult]:
        return [r for r in self.results if r.level == "warn" and not r.ok]

    def add(
        self,
        name: str,
        ok: bool,
        detail: str = "",
        *,
        level: str = "error",
    ) -> None:
        self.results.append(CheckResult(name=name, ok=ok, detail=detail, level=level))

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "generated_at": self.generated_at,
            "kit_version": self.kit_version,
            "contract_version_expected": self.contract_version_expected,
            "edge_root": self.edge_root,
            "checks": [
                {
                    "name": r.name,
                    "ok": r.ok,
                    "detail": r.detail,
                    "level": r.level,
                }
                for r in self.results
            ],
            "warning_count": len(self.warnings),
        }

    def print(self, file=sys.stdout) -> None:
        status = "PASS" if self.ok else "FAIL"
        root = self.edge_root or "(no edge path)"
        print(
            f"[{status}] edge doctor kit={self.kit_version} "
            f"contract={self.contract_version_expected} path={root}",
            file=file,
        )
        for r in self.results:
            if r.ok:
                mark = "ok"
            elif r.level == "warn":
                mark = "WARN"
            else:
                mark = "FAIL"
            extra = f" - {r.detail}" if r.detail else ""
            line = f"  [{mark}] {r.name}{extra}"
            try:
                print(line, file=file)
            except UnicodeEncodeError:
                print(line.encode("ascii", "replace").decode("ascii"), file=file)


def _find_monorepo_kit_version(start: Path) -> Optional[str]:
    """If running inside cloud-labs, read pyproject version for drift detection."""
    for parent in [start, *start.parents]:
        pyproject = parent / "packages" / "cloudlabs_edge_dev" / "pyproject.toml"
        if not pyproject.is_file():
            continue
        try:
            text = pyproject.read_text(encoding="utf-8")
        except OSError:
            return None
        for line in text.splitlines():
            if line.strip().startswith("version"):
                # version = "0.1.0"
                _, _, rest = line.partition("=")
                return rest.strip().strip('"').strip("'")
    return None


def _resolve_edge_root(path: Optional[Path]) -> Optional[Path]:
    if path is not None:
        return path.expanduser().resolve()
    cwd = Path.cwd().resolve()
    candidates = (
        cwd / "cloudlabs_edge",
        cwd,
    )
    for c in candidates:
        if (c / "capabilities.json").is_file() or (c / "contract_version.txt").is_file():
            return c
    return None


def run_doctor(edge_path: Optional[Path] = None) -> DoctorReport:
    """Inspect kit install + optional local edge tree (no HTTP)."""
    report = DoctorReport(
        kit_version=KIT_VERSION,
        contract_version_expected=CONTRACT_VERSION,
    )

    # --- kit / schemas ---
    report.add("kit_version readable", True, KIT_VERSION, level="info")
    report.add(
        "contract_version constant",
        bool(CONTRACT_VERSION),
        CONTRACT_VERSION,
        level="info",
    )

    try:
        schemas = contract_schemas_dir()
        report.add("edge contract schemas found", True, str(schemas))
        for name in (
            "capabilities.schema.json",
            "bench.schema.json",
            "library.schema.json",
            "inventory.schema.json",
            "execute_request.schema.json",
            "execute_response.schema.json",
        ):
            ok = (schemas / name).is_file()
            report.add(f"schema {name}", ok, str(schemas / name) if ok else "missing")
    except FileNotFoundError as e:
        report.add("edge contract schemas found", False, str(e))

    mono_ver = _find_monorepo_kit_version(Path.cwd())
    if mono_ver is not None:
        report.add(
            "installed kit matches monorepo pyproject",
            mono_ver == KIT_VERSION,
            f"installed={KIT_VERSION!r} monorepo={mono_ver!r} "
            "(reinstall: pip install -e ./packages/cloudlabs_edge_dev)",
        )
    else:
        report.add(
            "monorepo kit version",
            True,
            "not in cloud-labs tree (skipped)",
            level="info",
        )

    # --- local edge tree ---
    root = _resolve_edge_root(edge_path)
    if root is None:
        if edge_path is not None:
            report.edge_root = str(edge_path.expanduser().resolve())
            report.add(
                "edge tree present",
                False,
                f"path not found or empty: {report.edge_root}",
            )
        else:
            report.add(
                "edge tree present",
                True,
                "no ./cloudlabs_edge found (kit-only doctor); "
                "pass --path to check a lab tree",
                level="info",
            )
        return report

    report.edge_root = str(root)
    if not root.is_dir():
        report.add("edge tree present", False, f"not a directory: {root}")
        return report

    report.add("edge tree present", True, str(root))

    missing = [rel for rel in _REQUIRED_EDGE_FILES if not (root / rel).is_file()]
    report.add(
        "scaffold files present",
        not missing,
        "missing: " + ", ".join(missing) if missing else "all required files found",
    )

    # contract_version.txt
    cv_path = root / "contract_version.txt"
    if cv_path.is_file():
        local_cv = cv_path.read_text(encoding="utf-8").strip()
        report.add(
            "contract_version.txt pin",
            local_cv == CONTRACT_VERSION,
            f"got {local_cv!r}, want {CONTRACT_VERSION!r}",
        )
    else:
        report.add("contract_version.txt pin", False, "file missing")

    # capabilities.json
    caps: dict[str, Any] | None = None
    caps_path = root / "capabilities.json"
    if caps_path.is_file():
        try:
            caps = json.loads(caps_path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            report.add("capabilities.json parse", False, str(e))
            caps = None
        if isinstance(caps, dict):
            cv = caps.get("contract_version")
            report.add(
                "capabilities contract_version",
                cv == CONTRACT_VERSION,
                f"got {cv!r}, want {CONTRACT_VERSION!r}",
            )
            err = _validate_instance(caps, "capabilities.schema.json")
            report.add("capabilities.json schema", err is None, err or "")

            declared = set(caps.get("supported_primitives") or [])
            missing_prims = sorted(_STUB_PRIMITIVES - declared)
            report.add(
                "stub-profile primitives declared",
                not missing_prims,
                (
                    "missing: " + ", ".join(missing_prims)
                    if missing_prims
                    else f"{len(declared)} primitives"
                ),
            )
            backend_id = caps.get("backend_id")
            has_backend_id = isinstance(backend_id, str) and bool(backend_id.strip())
            report.add(
                "backend_id set",
                has_backend_id,
                repr(backend_id),
                level="error" if not has_backend_id else "info",
            )

            wants_sync = capabilities_wants_runtime_sync(caps)
            features = caps.get("features") if isinstance(caps.get("features"), dict) else {}
            feature_flag = bool(features.get("runtime_sync"))
            missing_sync_prims = sorted(_RUNTIME_SYNC_PRIMITIVES - declared)
            if wants_sync or feature_flag:
                report.add(
                    "runtime_sync primitives declared",
                    not missing_sync_prims,
                    (
                        "missing: " + ", ".join(missing_sync_prims)
                        if missing_sync_prims
                        else "RECORD_TUNABLES + SYNC_RUNTIME present"
                    ),
                )
                if feature_flag and "SYNC_RUNTIME" not in declared:
                    report.add(
                        "features.runtime_sync implies SYNC_RUNTIME",
                        False,
                        "capabilities.features.runtime_sync=true but "
                        "SYNC_RUNTIME not in supported_primitives "
                        "(fix the declaration; do not paper this over at runtime)",
                    )
            else:
                report.add(
                    "runtime_sync primitives declared",
                    True,
                    "features.runtime_sync not set (skipped)",
                    level="info",
                )
    else:
        report.add("capabilities.json parse", False, "file missing")

    # bench/layout.json
    bench_path = root / "bench" / "layout.json"
    if bench_path.is_file():
        try:
            bench = json.loads(bench_path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            report.add("bench/layout.json schema", False, str(e))
        else:
            err = _validate_instance(bench, "bench.schema.json")
            report.add("bench/layout.json schema", err is None, err or "")
    else:
        report.add("bench/layout.json schema", False, "file missing")

    # data/library.json + inventory — validate, never backfill
    lib_path = root / "data" / "library.json"
    inv_path = root / "data" / "inventory.json"
    strict_recordable = bool(
        isinstance(caps, dict) and capabilities_wants_runtime_sync(caps)
    )
    if lib_path.is_file():
        try:
            library = json.loads(lib_path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            report.add("data/library.json parse", False, str(e))
            library = None
        if isinstance(library, dict):
            err = _validate_instance(library, "library.schema.json")
            report.add("data/library.json schema", err is None, err or "")
            try:
                warns = validate_library_document(
                    library,
                    source=str(lib_path),
                    strict_recordable=strict_recordable,
                )
                report.add(
                    "library tunable recordable/set_at_init",
                    True,
                    (
                        f"strict_recordable={strict_recordable}; "
                        + (
                            f"{len(warns)} soft warning(s) — "
                            "declare recordable explicitly before enabling "
                            "features.runtime_sync"
                            if warns
                            else "ok"
                        )
                    ),
                )
                for w in warns[:12]:
                    report.add(
                        "library tunable metadata warning",
                        False,
                        w,
                        level="warn",
                    )
                if len(warns) > 12:
                    report.add(
                        "library tunable metadata warning",
                        False,
                        f"... and {len(warns) - 12} more",
                        level="warn",
                    )
            except LibraryValidationError as e:
                report.add(
                    "library tunable recordable/set_at_init",
                    False,
                    str(e),
                )
    else:
        report.add("data/library.json parse", False, "file missing")

    if inv_path.is_file() and lib_path.is_file():
        try:
            library = load_library(root, strict_recordable=False, validate=True)
            inventory = load_inventory(root)
            validate_inventory_against_library(library, inventory)
            err = _validate_instance(inventory, "inventory.schema.json")
            report.add("data/inventory.json schema", err is None, err or "")
            report.add("inventory tags ⊆ library", True, "ok")
        except Exception as e:  # noqa: BLE001
            report.add("inventory tags ⊆ library", False, str(e))
    elif inv_path.is_file():
        report.add(
            "inventory tags ⊆ library",
            False,
            "inventory present but library missing",
        )

    # adapters / latch / kernel_host: warn while still Phase-5 NotImplementedError
    skeleton_modules = [
        rel
        for rel in _REQUIRED_EDGE_FILES
        if rel.startswith("adapters/") and rel.endswith(".py") and rel != "adapters/__init__.py"
    ] + ["latch.py", "kernel_host.py"]
    placeholder_hits: list[str] = []
    for rel in skeleton_modules:
        p = root / rel
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        if "NotImplementedError" in text:
            placeholder_hits.append(rel)
    if placeholder_hits:
        report.add(
            "adapters implemented (not NotImplementedError)",
            False,
            "still NotImplementedError in: " + ", ".join(placeholder_hits)
            + " (OK for Phase 5; clear before hardware certify)",
            level="warn",
        )
    else:
        report.add(
            "adapters implemented (not NotImplementedError)",
            True,
            "no NotImplementedError markers in skeleton modules",
            level="info",
        )

    dispatch_py = root / "dispatch.py"
    if dispatch_py.is_file():
        text = dispatch_py.read_text(encoding="utf-8", errors="replace")
        report.add(
            "dispatch.py maps primitives",
            "PRIMITIVE_HANDLERS" in text,
            "expected PRIMITIVE_HANDLERS table",
        )

    # main.py should speak the contract
    main_py = root / "main.py"
    if main_py.is_file():
        text = main_py.read_text(encoding="utf-8", errors="replace")
        speaks = "create_app" in text or "FastAPI" in text or "capabilities" in text
        report.add(
            "main.py looks like an edge app",
            speaks,
            "expected create_app / FastAPI / capabilities references",
        )

    return report


def main_doctor(edge_path: Optional[Path] = None, *, as_json: bool = False) -> int:
    report = run_doctor(edge_path)
    if as_json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        report.print()
    return 0 if report.ok else 1
