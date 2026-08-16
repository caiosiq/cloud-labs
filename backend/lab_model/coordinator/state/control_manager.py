"""Configuration version history (branch graph) — not lab_automation hardware."""

from __future__ import annotations

import copy
import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set

from lab_model.coordinator.state.control_documents import (
    DEFAULT_REPO_ID,
    configuration_document,
    observations_document,
    setup_document,
)
from lab_model.coordinator.state.diff import configuration_diff, observation_diff
from lab_model.coordinator.state.projections import (
    EMPTY_CONFIGURATION,
    build_setup,
    configuration_versions_storage,
    extract_configuration,
    extract_configuration_metadata,
    extract_observations,
    infer_configuration_metadata_from_document,
    lab_configuration,
    normalize_stored_slot_only_in_configuration,
    strip_non_reconcile_tunables_from_configuration,
    table_configuration,
)
from lab_model.coordinator.state.batch_plan import (
    BatchPlanError,
    BatchPlanResult,
    parse_staging_seats,
    staging_commit_issues,
)
from lab_model.coordinator.state.reconcile import plan_reconcile, plan_reconcile_detailed
from lab_model.coordinator.state.checkout_compatibility import build_checkout_compatibility_report


REPO_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: str, payload: Mapping[str, Any]) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    os.replace(tmp, path)


def _control_capabilities(
    *,
    dirty: bool,
    detached: bool,
    has_stash: bool,
    has_commits: bool,
    unadopted: bool = False,
) -> Dict[str, bool]:
    """Single definition of the Git-like action rules.

    These are the *bench-side* gates, independent of the read-only node preview
    (which is a pure frontend overlay). The frontend additionally suppresses
    every mutating action while previewing a node. Backend endpoints enforce the
    same rules, so UI affordances and server enforcement can never disagree.

    ``unadopted`` means the repo has been entered but no current node has been
    established yet (the bench is not diffed against anything). In that state the
    only ways forward are to *Set as reference* an existing commit
    (``can_set_node``) or, for a fresh repo with no history, to commit the
    current bench as the root node.

    ``can_set_node`` stays true whenever history exists — retargeting the
    applied pointer is a soft ``git reset --soft`` (no robot motion) and must
    work even while dirty so operators can branch from another base without
    stashing first. Soft preview is also always allowed; only hard apply-on-bench
    / branch switches stay dirty-gated.
    """
    if unadopted:
        return {
            # Adopt an existing node as the base (no robot motion).
            "can_set_node": has_commits,
            # A brand-new repo (no history) commits the current bench as root.
            "can_commit": not has_commits,
            "commit_recommended": not has_commits,
            "can_stash": False,
            "can_pop_stash": False,
            "can_drop_stash": has_stash,
            "can_fork": False,
            # Soft preview is always fine; hard checkout stays gated elsewhere.
            "can_checkout_other": True,
        }
    return {
        # Soft retarget of the applied pointer (no motion) — allowed while dirty.
        "can_set_node": has_commits,
        # Commit is allowed unless you are on a detached (older) commit; forking
        # is the escape hatch from detached.
        "can_commit": not detached,
        # Highlight commit only when there is something worth recording.
        "commit_recommended": (dirty or not has_commits) and not detached,
        # Stash needs uncommitted changes and a free (single) stash slot.
        "can_stash": dirty and not has_stash,
        # Pop lands the snapshot as uncommitted work, which can only live on a
        # clean HEAD.
        "can_pop_stash": has_stash and not dirty and not detached,
        "can_drop_stash": has_stash,
        # Forking requires a parent commit to branch from.
        "can_fork": has_commits,
        # Soft preview of another node is always allowed; hard apply stays
        # dirty-gated in the checkout endpoint / UI.
        "can_checkout_other": True,
    }


def _bench_origin_path(control_root: str) -> str:
    return os.path.join(control_root, "bench_origin.json")


def read_bench_origin(control_root: str) -> Dict[str, Optional[str]]:
    """Which (repo, commit) physically realized the current global bench.

    The bench is a single shared physical lab; only one repo "owns" it at a
    time (the repo it was last hard-checked-out / committed from). Switching
    repos does NOT move the bench, so a non-owning repo treats the bench as
    uncommitted work on top of the shared empty (zeroth) state.
    """
    path = _bench_origin_path(control_root)
    if not os.path.isfile(path):
        return {"repo_id": None, "configuration_id": None}
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            doc = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {"repo_id": None, "configuration_id": None}
    if not isinstance(doc, dict):
        return {"repo_id": None, "configuration_id": None}
    return {
        "repo_id": str(doc["repo_id"]) if doc.get("repo_id") else None,
        "configuration_id": (
            str(doc["configuration_id"]) if doc.get("configuration_id") else None
        ),
    }


def write_bench_origin(
    control_root: str,
    repo_id: Optional[str],
    configuration_id: Optional[str],
) -> None:
    _atomic_write_json(
        _bench_origin_path(control_root),
        {"repo_id": repo_id, "configuration_id": configuration_id},
    )


def repo_owns_bench(control_root: str, repo_id: str) -> bool:
    """True when ``repo_id`` is the physical owner of the current bench.

    Until the first hard checkout / commit writes an origin marker, ownership
    is unknown and every repo is treated as owner (legacy applied-based
    behavior), preserving existing single-repo workflows.
    """
    origin = read_bench_origin(control_root)
    owner = origin.get("repo_id")
    return owner is None or owner == repo_id


class ControlManager:
    """File-backed configuration DAG under ``{lab_view}/control/{repo_id}/``."""

    def __init__(self, control_root: str, repo_id: str = DEFAULT_REPO_ID) -> None:
        self.repo_id = repo_id
        self.repo_dir = os.path.join(control_root, repo_id)
        self.configurations_dir = os.path.join(self.repo_dir, "configurations")
        self.observations_dir = os.path.join(self.repo_dir, "observations")
        self.setups_dir = os.path.join(self.repo_dir, "setups")
        self.blobs_dir = os.path.join(self.repo_dir, "blobs")
        self.refs_path = os.path.join(self.repo_dir, "refs.json")
        self.stash_path = os.path.join(self.repo_dir, "stash.json")
        os.makedirs(self.configurations_dir, exist_ok=True)
        os.makedirs(self.observations_dir, exist_ok=True)
        os.makedirs(self.setups_dir, exist_ok=True)
        os.makedirs(self.blobs_dir, exist_ok=True)
        if not os.path.isfile(self.refs_path):
            _atomic_write_json(
                self.refs_path,
                {
                    "heads": {"main": None},
                    "applied": {"configuration_id": None, "branch": None},
                    "viewing": None,
                },
            )
        #: Optional override / provider for layout ``reconcile_staging_seats``.
        self.staging_seats: Optional[List[Dict[str, float]]] = None
        self.staging_seats_provider: Optional[Any] = None

    def reconcile_staging_seats(self) -> List[Dict[str, float]]:
        """Park buffers from the active edge bench layout (any backend).

        Populated from that edge's ``GET /bench`` / ``bench/layout.json``
        ``reconcile_staging_seats`` via :meth:`bind_edge_staging_seats` /
        ``main._get_control_manager`` — not mock-specific. Empty declaration
        ⇒ swap plans fail closed with ``needs_staging_n``.
        """
        if self.staging_seats is not None:
            return parse_staging_seats(self.staging_seats)
        provider = self.staging_seats_provider
        if callable(provider):
            try:
                return parse_staging_seats(provider())
            except Exception:
                return []
        return []

    def bind_edge_staging_seats(self, layout: Any) -> List[Dict[str, float]]:
        """Refresh Park buffers from a flat edge layout document (any edge)."""
        seats = parse_staging_seats(layout)
        self.staging_seats = seats
        return seats

    def _refs(self) -> Dict[str, Any]:
        # utf-8-sig tolerates a stray BOM (e.g. a file hand-edited on Windows).
        with open(self.refs_path, "r", encoding="utf-8-sig") as handle:
            doc = json.load(handle)
        if not isinstance(doc, dict):
            return {"heads": {"main": None}}
        heads = doc.get("heads")
        if not isinstance(heads, dict):
            doc["heads"] = {"main": None}
        return doc

    def _save_refs(self, refs: Mapping[str, Any]) -> None:
        _atomic_write_json(self.refs_path, refs)

    def get_head(self, branch: str = "main") -> Optional[str]:
        heads = self._refs().get("heads") or {}
        commit_id = heads.get(branch)
        return str(commit_id) if commit_id else None

    def set_head(self, branch: str, commit_id: Optional[str]) -> None:
        refs = self._refs()
        heads = refs.setdefault("heads", {})
        heads[branch] = commit_id
        self._save_refs(refs)

    def get_applied(self) -> Dict[str, Optional[str]]:
        refs = self._refs()
        applied = refs.get("applied")
        if not isinstance(applied, dict):
            return {"configuration_id": None, "branch": None}
        cid = applied.get("configuration_id")
        branch = applied.get("branch")
        return {
            "configuration_id": str(cid) if cid else None,
            "branch": str(branch) if branch else None,
        }

    def set_applied(
        self,
        configuration_id: Optional[str],
        *,
        branch: Optional[str] = None,
    ) -> None:
        refs = self._refs()
        refs["applied"] = {
            "configuration_id": configuration_id,
            "branch": branch,
        }
        self._save_refs(refs)

    def get_viewing(self) -> Optional[str]:
        """Configuration currently soft-checked-out as a preview (or ``None``).

        Tracked on the backend so dirty-detection can distinguish a *preview*
        projection of another node (runtime temporarily mirrors that node) from
        genuine uncommitted edits on the applied configuration.
        """
        viewing = self._refs().get("viewing")
        return str(viewing) if viewing else None

    def set_viewing(self, configuration_id: Optional[str]) -> None:
        refs = self._refs()
        refs["viewing"] = configuration_id or None
        self._save_refs(refs)

    def _configuration_path(self, commit_id: str) -> str:
        return os.path.join(self.configurations_dir, f"{commit_id}.json")

    def save_configuration_document(self, document: Mapping[str, Any]) -> str:
        commit_id = str(document.get("id") or "")
        if not commit_id:
            raise ValueError("configuration document requires id")
        _atomic_write_json(self._configuration_path(commit_id), document)
        return commit_id

    def get_configuration(self, commit_id: str) -> Dict[str, Any]:
        path = self._configuration_path(commit_id)
        if not os.path.isfile(path):
            raise FileNotFoundError(commit_id)
        with open(path, "r", encoding="utf-8-sig") as handle:
            doc = json.load(handle)
        if not isinstance(doc, dict):
            raise ValueError(f"invalid configuration document: {commit_id}")
        return doc

    def list_configuration_ids(self) -> List[str]:
        if not os.path.isdir(self.configurations_dir):
            return []
        return [
            name[:-5]
            for name in os.listdir(self.configurations_dir)
            if name.endswith(".json")
        ]

    def backfill_overlays(
        self,
        *,
        alignment_guides: Iterable[Mapping[str, Any]],
        laser_lines: Mapping[str, Any],
    ) -> int:
        """Inject alignment overlays into every commit (and the stash) that
        predates line-versioning. Idempotent: only fills missing keys. Returns
        the number of documents updated.

        Commit ids are random UUIDs (not content hashes), so rewriting the
        ``configuration`` body in place is safe.
        """
        guides = list(alignment_guides)
        laser = dict(laser_lines or {})
        updated = 0
        for cid in self.list_configuration_ids():
            try:
                doc = self.get_configuration(cid)
            except (FileNotFoundError, ValueError):
                continue
            cfg = doc.get("configuration")
            if not isinstance(cfg, dict):
                continue
            changed = False
            if "alignment_guides" not in cfg:
                cfg["alignment_guides"] = copy.deepcopy(guides)
                changed = True
            if "laser_lines" not in cfg:
                cfg["laser_lines"] = copy.deepcopy(laser)
                changed = True
            if changed:
                self.save_configuration_document(doc)
                updated += 1
        stash = self.get_stash()
        if isinstance(stash, dict):
            cfg = stash.get("configuration")
            if isinstance(cfg, dict):
                changed = False
                if "alignment_guides" not in cfg:
                    cfg["alignment_guides"] = copy.deepcopy(guides)
                    changed = True
                if "laser_lines" not in cfg:
                    cfg["laser_lines"] = copy.deepcopy(laser)
                    changed = True
                if changed:
                    _atomic_write_json(self.stash_path, stash)
                    updated += 1
        return updated

    def _latest_observations_by_configuration_id(self) -> Dict[str, Dict[str, Any]]:
        """Map configuration id → observations payload from the newest pin per commit."""
        index: Dict[str, Dict[str, Any]] = {}
        if not os.path.isdir(self.observations_dir):
            return index
        pins: List[Dict[str, Any]] = []
        for name in os.listdir(self.observations_dir):
            if not name.endswith(".json"):
                continue
            path = os.path.join(self.observations_dir, name)
            try:
                with open(path, "r", encoding="utf-8-sig") as handle:
                    doc = json.load(handle)
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(doc, dict):
                pins.append(doc)
        pins.sort(key=lambda item: str(item.get("created_at") or ""))
        for pin in pins:
            cid = pin.get("configuration_id")
            if isinstance(cid, str) and cid:
                obs = pin.get("observations")
                index[cid] = obs if isinstance(obs, dict) else {}
        return index

    def _backfill_optimization_on_document(
        self,
        doc: Dict[str, Any],
        *,
        observations: Mapping[str, Any] | None = None,
    ) -> bool:
        """Idempotently add optimization metadata and strip legacy placement tunables."""
        cfg = doc.get("configuration")
        if not isinstance(cfg, dict):
            return False
        changed = False
        inferred = infer_configuration_metadata_from_document(
            doc,
            observations=observations,
        )
        if inferred:
            meta = doc.get("metadata")
            if not isinstance(meta, dict):
                meta = {}
                doc["metadata"] = meta
            opt = meta.get("optimization")
            if not isinstance(opt, dict):
                opt = {}
                meta["optimization"] = opt
            for tag_id, entry in (inferred.get("optimization") or {}).items():
                if tag_id not in opt:
                    opt[tag_id] = copy.deepcopy(entry)
                    changed = True
        if strip_non_reconcile_tunables_from_configuration(cfg):
            changed = True
        return changed

    def backfill_optimization_metadata(self) -> int:
        """One-time migration: infer ``metadata.optimization`` on legacy commits.

        Uses legacy ``tunables.placement.mode`` plus observation pins linked to
        each configuration id. Also strips ``placement`` from stored tunables.
        Idempotent — safe to run repeatedly.
        """
        obs_by_config = self._latest_observations_by_configuration_id()
        updated = 0
        for cid in self.list_configuration_ids():
            try:
                doc = self.get_configuration(cid)
            except (FileNotFoundError, ValueError):
                continue
            if self._backfill_optimization_on_document(
                doc,
                observations=obs_by_config.get(cid),
            ):
                self.save_configuration_document(doc)
                updated += 1
        stash = self.get_stash()
        if isinstance(stash, dict) and self._backfill_optimization_on_document(stash):
            _atomic_write_json(self.stash_path, stash)
            updated += 1
        return updated

    def backfill_storage_slot_only(self) -> int:
        """Strip stored ``nominal_pose`` / ``reported_pose``; keep slot only.

        Idempotent. Rewrites configuration commits and the stash in place
        (commit ids are UUIDs, not content hashes).
        """
        updated = 0
        for cid in self.list_configuration_ids():
            try:
                doc = self.get_configuration(cid)
            except (FileNotFoundError, ValueError):
                continue
            cfg = doc.get("configuration")
            if not isinstance(cfg, dict):
                continue
            if normalize_stored_slot_only_in_configuration(cfg):
                self.save_configuration_document(doc)
                updated += 1
        stash = self.get_stash()
        if isinstance(stash, dict):
            cfg = stash.get("configuration")
            if isinstance(cfg, dict) and normalize_stored_slot_only_in_configuration(cfg):
                _atomic_write_json(self.stash_path, stash)
                updated += 1
        return updated

    def commit_configuration(
        self,
        configuration: Mapping[str, Any],
        *,
        message: str,
        branch: str = "main",
        parent_id: Optional[str] = None,
        author: Optional[str] = None,
        catalog_hash: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        if parent_id is None:
            parent_id = self.get_head(branch)
        commit_id = uuid.uuid4().hex
        meta_copy = copy.deepcopy(dict(metadata)) if metadata else None
        document = configuration_document(
            commit_id=commit_id,
            repo_id=self.repo_id,
            branch=branch,
            parent_id=parent_id,
            message=message.strip() or "configuration commit",
            configuration=copy.deepcopy(dict(configuration)),
            metadata=meta_copy,
            catalog_hash=catalog_hash,
            created_at=_utc_now(),
            author=author,
        )
        self.save_configuration_document(document)
        self.set_head(branch, commit_id)
        self.set_applied(commit_id, branch=branch)
        self.set_viewing(None)
        return document

    def commit_from_runtime(
        self,
        runtime: Mapping[str, Any],
        *,
        message: str,
        branch: str = "main",
        parent_id: Optional[str] = None,
        author: Optional[str] = None,
        catalog_hash: Optional[str] = None,
        staging_seats: Optional[Iterable[Mapping[str, Any]]] = None,
    ) -> Dict[str, Any]:
        configuration = extract_configuration(runtime)
        seats = (
            parse_staging_seats(list(staging_seats))
            if staging_seats is not None
            else self.reconcile_staging_seats()
        )
        if seats:
            blocking = staging_commit_issues(configuration, seats)
            if blocking:
                raise ValueError(
                    "; ".join(str(i.get("message") or i.get("kind")) for i in blocking)
                )
        metadata = extract_configuration_metadata(runtime)
        return self.commit_configuration(
            configuration,
            message=message,
            branch=branch,
            parent_id=parent_id,
            author=author,
            catalog_hash=catalog_hash,
            metadata=metadata,
        )

    def fork_branch(self, *, branch: str, parent_id: str) -> None:
        """Create ``branch`` at ``parent_id`` and land the bench on its HEAD.

        Forking is the way to start editing from a detached node: the new branch
        HEAD becomes ``parent_id``, and ``applied`` is repointed onto it so the
        bench is "on HEAD" again (editing unlocked). The live runtime is left
        untouched, so any uncommitted changes are carried onto the new branch
        (mirrors ``git switch -c``).
        """
        self.get_configuration(parent_id)
        self.set_head(branch, parent_id)
        self.set_applied(parent_id, branch=branch)
        self.set_viewing(None)

    def _load_all_nodes(self) -> Dict[str, Dict[str, Any]]:
        nodes: Dict[str, Dict[str, Any]] = {}
        if not os.path.isdir(self.configurations_dir):
            return nodes
        for name in os.listdir(self.configurations_dir):
            if not name.endswith(".json"):
                continue
            path = os.path.join(self.configurations_dir, name)
            try:
                with open(path, "r", encoding="utf-8-sig") as handle:
                    doc = json.load(handle)
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(doc, dict):
                continue
            commit_id = doc.get("id")
            if not isinstance(commit_id, str) or not commit_id:
                continue
            nodes[commit_id] = {
                "id": commit_id,
                "branch": doc.get("branch"),
                "parent_id": doc.get("parent_id"),
                "message": doc.get("message"),
                "created_at": doc.get("created_at"),
            }
        return nodes

    def list_history(self, branch: Optional[str] = None) -> List[Dict[str, Any]]:
        all_nodes = self._load_all_nodes()
        if branch is None:
            nodes = list(all_nodes.values())
        else:
            included: set[str] = set()
            head = self.get_head(branch)
            current: Optional[str] = head
            while current and current not in included:
                included.add(current)
                node = all_nodes.get(current)
                if not node:
                    break
                parent = node.get("parent_id")
                current = str(parent) if isinstance(parent, str) and parent else None
            for commit_id, node in all_nodes.items():
                if node.get("branch") == branch:
                    included.add(commit_id)
            nodes = [all_nodes[cid] for cid in included if cid in all_nodes]
        nodes.sort(key=lambda item: str(item.get("created_at") or ""))
        return nodes

    def diff_configurations(self, from_id: str, to_id: str) -> List[Dict[str, Any]]:
        from_doc = self.get_configuration(from_id)
        to_doc = self.get_configuration(to_id)
        return configuration_diff(
            lab_configuration(from_doc.get("configuration") or {}),
            lab_configuration(to_doc.get("configuration") or {}),
        )

    def plan_checkout(
        self,
        from_id: str,
        to_id: str,
        *,
        staging_seats: Optional[Iterable[Mapping[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        from_doc = self.get_configuration(from_id)
        to_doc = self.get_configuration(to_id)
        seats = (
            parse_staging_seats(list(staging_seats))
            if staging_seats is not None
            else self.reconcile_staging_seats()
        )
        return plan_reconcile(
            lab_configuration(from_doc.get("configuration") or {}),
            lab_configuration(to_doc.get("configuration") or {}),
            staging_seats=seats,
        )

    def plan_checkout_from_runtime(
        self,
        runtime: Mapping[str, Any],
        to_id: str,
        *,
        staging_seats: Optional[Iterable[Mapping[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Plan a hard checkout from the *actual* bench state to ``to_id``.

        Hard checkout physically moves the bench, so the plan must start from
        where the bench really is (the live runtime), not from a branch HEAD
        or ``applied`` pointer that may have diverged (e.g. after applying a
        non-HEAD commit). Diffing against a stale pointer yields no-op steps
        the hardware refuses (store an already-stored part, place an already-
        placed part) and false "no motion required" results.
        """
        to_doc = self.get_configuration(to_id)
        seats = (
            parse_staging_seats(list(staging_seats))
            if staging_seats is not None
            else self.reconcile_staging_seats()
        )
        return plan_reconcile(
            extract_configuration(runtime),
            lab_configuration(to_doc.get("configuration") or {}),
            staging_seats=seats,
        )

    def plan_checkout_from_runtime_detailed(
        self,
        runtime: Mapping[str, Any],
        to_id: str,
        *,
        staging_seats: Optional[Iterable[Mapping[str, Any]]] = None,
    ) -> BatchPlanResult:
        """Like :meth:`plan_checkout_from_runtime` but includes the batch report."""
        to_doc = self.get_configuration(to_id)
        seats = (
            parse_staging_seats(list(staging_seats))
            if staging_seats is not None
            else self.reconcile_staging_seats()
        )
        result = plan_reconcile_detailed(
            extract_configuration(runtime),
            lab_configuration(to_doc.get("configuration") or {}),
            staging_seats=seats,
        )
        if not result.ready:
            raise BatchPlanError(result.report)
        return result

    def plan_runtime_to_configuration(
        self,
        runtime: Mapping[str, Any],
        target_configuration: Mapping[str, Any],
        *,
        staging_seats: Optional[Iterable[Mapping[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Plan reconcile from the live bench to an arbitrary configuration.

        Used for stash (target = applied node) and stash pop (target = the
        stashed snapshot, which is not a committed node).
        """
        seats = (
            parse_staging_seats(list(staging_seats))
            if staging_seats is not None
            else self.reconcile_staging_seats()
        )
        return plan_reconcile(
            extract_configuration(runtime),
            lab_configuration(target_configuration or {}),
            staging_seats=seats,
        )

    def checkout_compatibility_report(
        self,
        configuration_id: str,
        runtime: Mapping[str, Any],
        *,
        current_catalog_hash: Optional[str] = None,
        catalog_tag_ids: Optional[Iterable[str]] = None,
        library_tag_ids: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        document = self.get_configuration(configuration_id)
        configuration = document.get("configuration") or {}
        report = build_checkout_compatibility_report(
            configuration=configuration,
            runtime=runtime,
            commit_catalog_hash=document.get("catalog_hash"),
            current_catalog_hash=current_catalog_hash,
            catalog_tag_ids=catalog_tag_ids,
            library_tag_ids=library_tag_ids,
        )
        report["configuration_id"] = configuration_id
        return report

    def pin_observations(
        self,
        runtime: Mapping[str, Any],
        *,
        configuration_id: str,
        message: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.get_configuration(configuration_id)
        pin_id = uuid.uuid4().hex
        document = observations_document(
            pin_id=pin_id,
            repo_id=self.repo_id,
            configuration_id=configuration_id,
            observations=extract_observations(runtime),
            created_at=_utc_now(),
            message=message,
        )
        path = os.path.join(self.observations_dir, f"{pin_id}.json")
        _atomic_write_json(path, document)
        return document

    def save_setup(
        self,
        *,
        name: str,
        configuration_id: str,
        observations_id: Optional[str] = None,
        message: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.get_configuration(configuration_id)
        if observations_id is not None:
            obs_path = os.path.join(self.observations_dir, f"{observations_id}.json")
            if not os.path.isfile(obs_path):
                raise FileNotFoundError(obs_path)
        setup_id = uuid.uuid4().hex
        document = setup_document(
            setup_id=setup_id,
            repo_id=self.repo_id,
            name=name,
            configuration_id=configuration_id,
            observations_id=observations_id,
            message=message,
            created_at=_utc_now(),
        )
        path = os.path.join(self.setups_dir, f"{setup_id}.json")
        _atomic_write_json(path, document)
        return document

    def build_setup_from_runtime(
        self,
        runtime: Mapping[str, Any],
        *,
        name: str,
        configuration_id: Optional[str] = None,
        message: Optional[str] = None,
    ) -> Dict[str, Any]:
        configuration = extract_configuration(runtime)
        observations = extract_observations(runtime)
        if configuration_id is None:
            commit = self.commit_configuration(
                configuration,
                message=message or f"setup {name}",
            )
            configuration_id = str(commit["id"])
        pin = self.pin_observations(
            runtime,
            configuration_id=configuration_id,
            message=message,
        )
        setup = self.save_setup(
            name=name,
            configuration_id=configuration_id,
            observations_id=str(pin["id"]),
            message=message,
        )
        setup["configuration"] = configuration
        setup["observations"] = observations
        setup["setup_payload"] = build_setup(configuration, observations)
        return setup

    def compare_observations(
        self,
        from_id: str,
        to_id: str,
        *,
        position_mm: float = 0.1,
        yaw_deg: float = 0.1,
    ) -> List[Dict[str, Any]]:
        def _load(pin_id: str) -> Dict[str, Any]:
            path = os.path.join(self.observations_dir, f"{pin_id}.json")
            with open(path, "r", encoding="utf-8") as handle:
                doc = json.load(handle)
            if not isinstance(doc, dict):
                raise ValueError(pin_id)
            return doc

        from_doc = _load(from_id)
        to_doc = _load(to_id)
        return observation_diff(
            from_doc.get("observations") or {},
            to_doc.get("observations") or {},
            position_mm=position_mm,
            yaw_deg=yaw_deg,
        )

    # ----- single-slot stash -------------------------------------------------

    def get_stash(self) -> Optional[Dict[str, Any]]:
        if not os.path.isfile(self.stash_path):
            return None
        try:
            with open(self.stash_path, "r", encoding="utf-8-sig") as handle:
                doc = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None
        return doc if isinstance(doc, dict) else None

    def save_stash(
        self,
        configuration: Mapping[str, Any],
        *,
        base_configuration_id: Optional[str],
        base_branch: Optional[str],
        message: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        document = {
            "id": uuid.uuid4().hex,
            "repo_id": self.repo_id,
            "base_configuration_id": base_configuration_id,
            "base_branch": base_branch,
            "configuration": copy.deepcopy(dict(configuration)),
            "message": (message or "").strip() or None,
            "created_at": _utc_now(),
        }
        if metadata:
            document["metadata"] = copy.deepcopy(dict(metadata))
        _atomic_write_json(self.stash_path, document)
        return document

    def clear_stash(self) -> None:
        try:
            os.remove(self.stash_path)
        except FileNotFoundError:
            pass

    def stash_summary(self) -> Optional[Dict[str, Any]]:
        """Stash metadata for status payloads (excludes the bulky snapshot)."""
        stash = self.get_stash()
        if stash is None:
            return None
        return {
            "id": stash.get("id"),
            "base_configuration_id": stash.get("base_configuration_id"),
            "base_branch": stash.get("base_branch"),
            "message": stash.get("message"),
            "created_at": stash.get("created_at"),
        }

    # ----- working-tree (bench) state ---------------------------------------

    def runtime_is_dirty(
        self,
        runtime: Mapping[str, Any],
        *,
        owns_bench: bool = True,
    ) -> bool:
        """True when the live bench has uncommitted edits vs the applied node.

        Preview is now a read-only frontend overlay (soft checkout no longer
        mutates the runtime), so the live runtime always reflects the real bench
        and a runtime/applied diff is always a genuine edit. With no applied node
        the bench is diffed against the shared empty (zeroth) state, so any
        active component reads as uncommitted work.
        """
        applied_id = self.get_applied().get("configuration_id") if owns_bench else None
        if not applied_id:
            # No applied node: the bench is uncommitted work on top of the
            # shared empty (zeroth) state. Ambient storage inventory does not
            # dirty an empty baseline — only breadboard (active table) parts.
            base_cfg = EMPTY_CONFIGURATION
            live_cfg = table_configuration(extract_configuration(runtime))
        else:
            try:
                base_cfg = lab_configuration(
                    self.get_configuration(applied_id).get("configuration") or {}
                )
            except FileNotFoundError:
                return False
            live_cfg = extract_configuration(runtime)
            # Legacy commits omit storage: ignore ambient inventory so old
            # nodes do not permanently read dirty against a filled rack.
            if not configuration_versions_storage(base_cfg):
                base_cfg = table_configuration(base_cfg)
                live_cfg = table_configuration(live_cfg)
        changes = configuration_diff(base_cfg, live_cfg)
        return len(changes) > 0

    def working_state(
        self,
        runtime: Mapping[str, Any],
        *,
        owns_bench: bool = True,
    ) -> Dict[str, Any]:
        """Git-like snapshot of where the bench is and whether it is dirty.

        When this repo does not own the physical bench (``owns_bench=False``),
        the bench is foreign — treated as uncommitted work on top of the shared
        empty state — so ``applied`` reads as empty and ``on_head`` is true (you
        are free to commit a new root node or stash).
        """
        heads = self._refs().get("heads") or {}
        has_commits = any(bool(v) for v in heads.values())
        stash_summary = self.stash_summary()
        if not owns_bench:
            # Repo entered but no current node established yet (unadopted). The
            # bench is NOT diffed against anything — not even the empty/zeroth
            # state — so we report it as un-dirty. The user must *Set as node* an
            # existing commit (or commit the current bench in a fresh repo) to
            # establish a base before stash/fork/commit-child become meaningful.
            state = {
                "applied": {"configuration_id": None, "branch": None},
                "head": None,
                "on_head": True,
                "detached": False,
                "viewing": self.get_viewing(),
                "dirty": False,
                "unadopted": True,
                "stash": stash_summary,
                "owns_bench": False,
            }
            state["capabilities"] = _control_capabilities(
                dirty=False,
                detached=False,
                has_stash=stash_summary is not None,
                has_commits=has_commits,
                unadopted=True,
            )
            return state
        applied = self.get_applied()
        applied_id = applied.get("configuration_id")
        applied_branch = applied.get("branch")
        head = self.get_head(applied_branch) if applied_branch else None
        viewing = self.get_viewing()
        on_head = applied_id is None or applied_id == head
        detached = applied_id is not None and applied_id != head
        dirty = self.runtime_is_dirty(runtime, owns_bench=True)
        state = {
            "applied": applied,
            "head": head,
            "on_head": on_head,
            "detached": detached,
            "viewing": viewing,
            "dirty": dirty,
            "unadopted": False,
            "stash": stash_summary,
            "owns_bench": True,
        }
        state["capabilities"] = _control_capabilities(
            dirty=dirty,
            detached=detached,
            has_stash=stash_summary is not None,
            has_commits=has_commits,
        )
        return state

    def status(
        self,
        runtime: Optional[Mapping[str, Any]] = None,
        *,
        owns_bench: bool = True,
    ) -> Dict[str, Any]:
        refs = self._refs()
        payload: Dict[str, Any] = {
            "repo_id": self.repo_id,
            "heads": refs.get("heads") or {},
            "applied": self.get_applied(),
            "viewing": self.get_viewing(),
            "stash": self.stash_summary(),
            "configuration_count": len(
                [name for name in os.listdir(self.configurations_dir) if name.endswith(".json")]
            ),
        }
        if runtime is not None:
            payload["working"] = self.working_state(runtime, owns_bench=owns_bench)
        return payload


def _read_repo_meta(repo_dir: str) -> Dict[str, Any]:
    path = os.path.join(repo_dir, "repo.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            doc = json.load(handle)
        return doc if isinstance(doc, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def validate_repo_id(repo_id: str) -> str:
    safe = (repo_id or "").strip().lower()
    if not safe or not REPO_ID_PATTERN.match(safe):
        raise ValueError(
            "repo_id must be 1–32 chars: lowercase letters, digits, hyphen, underscore; "
            "must start with a letter or digit"
        )
    return safe


def list_control_repos(control_root: str) -> List[Dict[str, Any]]:
    """Discover repos under ``control_root`` (directories with ``refs.json``)."""
    if not os.path.isdir(control_root):
        return []
    repos: List[Dict[str, Any]] = []
    for name in sorted(os.listdir(control_root)):
        repo_dir = os.path.join(control_root, name)
        refs_path = os.path.join(repo_dir, "refs.json")
        if not os.path.isdir(repo_dir) or not os.path.isfile(refs_path):
            continue
        mgr = ControlManager(control_root, name)
        summary = mgr.status()
        meta = _read_repo_meta(repo_dir)
        repos.append(
            {
                "repo_id": name,
                "display_name": str(meta.get("display_name") or name),
                "created_at": meta.get("created_at"),
                "heads": summary.get("heads") or {},
                "applied": summary.get("applied") or {},
                "configuration_count": summary.get("configuration_count", 0),
            }
        )
    return repos


def create_control_repo(
    control_root: str,
    repo_id: str,
    *,
    display_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Create an empty control repo (no commits until first Commit)."""
    safe = validate_repo_id(repo_id)
    repo_dir = os.path.join(control_root, safe)
    if os.path.exists(repo_dir):
        raise FileExistsError(safe)
    os.makedirs(control_root, exist_ok=True)
    mgr = ControlManager(control_root, safe)
    label = (display_name or safe).strip() or safe
    meta = {"display_name": label, "created_at": _utc_now()}
    _atomic_write_json(os.path.join(repo_dir, "repo.json"), meta)
    return {
        "repo_id": safe,
        "display_name": label,
        "created_at": meta["created_at"],
        **mgr.status(),
    }
