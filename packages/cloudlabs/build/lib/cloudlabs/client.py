"""Imperative HTTP client for cloud-labs (Phase B SDK).

Acquire an exclusive backend lease via the context manager, then issue
primitives with ``move_component`` and ``capture_measurable``.

Example::

    from cloudlabs import connect

    with connect("mock.default") as lab:
        lab.move_component("tag_20", "tunables.nominal_pose.x", 12.5)
        lab.wait_until_idle()
        image = lab.capture_measurable("tag_22", "measurables.camera_image")
"""
from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Literal, Mapping, Optional, Sequence, Tuple, Union

import requests

from .paths import parse_variable_path

from .exceptions import (
    CloudLabsCommandError,
    CloudLabsConnectionError,
    CloudLabsError,
    CloudLabsLeaseError,
    CloudLabsPathError,
    CloudLabsReconcileError,
    CloudLabsTimeoutError,
)
from .reconcile import (
    LoadedSnapshot,
    ProgressCallback,
    ReconcileResult,
    ReconcileStep,
    build_reconcile_steps,
    default_progress_logger,
)
from .intent import Bounds, KernelMatchSpec, VariableSpec

__all__ = [
    "CloudLabsClient",
    "KernelMatchSpec",
    "LoadedSnapshot",
    "ReconcileResult",
    "SessionLease",
    "VariableSpec",
    "configure_logging",
    "connect",
    "list_backends",
    "resolve_backend_id",
]

_LOG = logging.getLogger(__name__)
_SESSION_LOG = logging.getLogger("cloudlabs")

ExecutionMode = Literal["imperative", "compiled_dag", "closed_loop"]
BackendId = str
LeaseId = str

_MEASURABLE_PATH = re.compile(r"^measurables\.(?P<field>[A-Za-z0-9_]+)$")


def configure_logging(
    level: Union[int, str] = logging.INFO,
    *,
    format: str = "%(asctime)s %(levelname)s %(message)s",
) -> logging.Logger:
    """Configure root + ``cloudlabs`` loggers for scripts (see logging_config)."""
    from .logging_config import configure_logging as _configure

    return _configure(level, format=format)


@dataclass(frozen=True)
class SessionLease:
    """Exclusive session lock returned by ``POST /api/jobs/lease/acquire``."""

    lease_id: LeaseId
    backend_id: BackendId
    holder: str
    mode: ExecutionMode
    issued_at: datetime
    expires_at: datetime
    snapshot_ref: Optional[str] = None

    @classmethod
    def from_api(cls, payload: Mapping[str, Any]) -> "SessionLease":
        return cls(
            lease_id=str(payload["lease_id"]),
            backend_id=str(payload["backend_id"]),
            holder=str(payload["holder"]),
            mode=payload.get("mode", "imperative"),  # type: ignore[arg-type]
            issued_at=_parse_iso(str(payload["issued_at"])),
            expires_at=_parse_iso(str(payload["expires_at"])),
            snapshot_ref=payload.get("snapshot_ref"),
        )


def _parse_iso(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def connect(
    backend_id: BackendId,
    *,
    base_url: str = "http://127.0.0.1:8000",
    holder: Optional[str] = None,
    mode: ExecutionMode = "imperative",
    snapshot_ref: Optional[str] = None,
    initialization_policy: str = "force_reconcile",
    timeout_s: float = 30.0,
    heartbeat_s: Optional[float] = 60.0,
    verbose: bool = True,
) -> "CloudLabsClient":
    """Factory for :class:`CloudLabsClient` (preferred entry point)."""
    return CloudLabsClient(
        backend_id=backend_id,
        base_url=base_url,
        holder=holder,
        mode=mode,
        snapshot_ref=snapshot_ref,
        initialization_policy=initialization_policy,
        timeout_s=timeout_s,
        heartbeat_s=heartbeat_s,
        verbose=verbose,
    )


def list_backends(base_url: str = "http://127.0.0.1:8000", *, timeout_s: float = 10.0) -> List[Dict[str, Any]]:
    """List registered backends from GET /api/backends."""
    url = f"{base_url.rstrip('/')}/api/backends"
    try:
        resp = requests.get(url, timeout=timeout_s)
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as exc:
        raise CloudLabsConnectionError(f"GET /api/backends failed: {exc}") from exc
    rows = payload.get("backends") if isinstance(payload, dict) else []
    return list(rows) if isinstance(rows, list) else []


def resolve_backend_id(
    base_url: str = "http://127.0.0.1:8000",
    *,
    timeout_s: float = 10.0,
    prefer: Optional[str] = None,
) -> str:
    """Pick a backend_id: explicit prefer, else first ready backend from /api/backends."""
    if prefer and prefer.strip():
        return prefer.strip()
    rows = list_backends(base_url, timeout_s=timeout_s)
    ready = [r for r in rows if str(r.get("availability")) == "ready"]
    pool = ready or rows
    if not pool:
        raise CloudLabsConnectionError("no backends registered on server")
    bid = str(pool[0].get("backend_id") or "").strip()
    if not bid:
        raise CloudLabsConnectionError("backends response missing backend_id")
    return bid


class CloudLabsClient:
    """HTTP façade for imperative cloud-labs control.

    Parameters
    ----------
    backend_id:
        Target bench id, e.g. ``"mock.default"`` or ``"real.mit_bench_1"``.
    base_url:
        FastAPI origin (no trailing slash).
    holder:
        Lease owner label. Defaults to ``sdk:<uuid>``.
    mode:
        Execution mode recorded on the lease (imperative for scripts).
    snapshot_ref:
        Optional control-repo pin applied at lease acquire.
    timeout_s:
        Per-request HTTP timeout.
    heartbeat_s:
        Lease heartbeat interval in seconds (``60`` default). Set ``0`` to disable.
    """

    _DEFAULT_HEARTBEAT_S = 60.0

    def __init__(
        self,
        backend_id: BackendId,
        *,
        base_url: str = "http://127.0.0.1:8000",
        holder: Optional[str] = None,
        mode: ExecutionMode = "imperative",
        snapshot_ref: Optional[str] = None,
        initialization_policy: str = "force_reconcile",
        timeout_s: float = 30.0,
        heartbeat_s: Optional[float] = 60.0,
        verbose: bool = True,
    ) -> None:
        self.backend_id = backend_id.strip()
        if not self.backend_id:
            raise ValueError("backend_id must be a non-empty string")

        self.base_url = base_url.rstrip("/")
        self.holder = holder or f"sdk:{uuid.uuid4().hex[:12]}"
        self.mode = mode
        self.snapshot_ref = snapshot_ref
        self.initialization_policy = initialization_policy
        self.timeout_s = timeout_s
        self.heartbeat_s = heartbeat_s
        self.verbose = bool(verbose)

        self._session = requests.Session()
        self._session.headers["X-CloudLabs-Backend"] = self.backend_id
        self._lease: Optional[SessionLease] = None
        self._released = False
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._loaded_snapshot: Optional[LoadedSnapshot] = None
        self._wiki_registries: Optional[Dict[str, Any]] = None
        self._wiki_rows_cache: Optional[Dict[str, Dict[str, Any]]] = None
        self._wiki_active_tags: Optional[set] = None
        # Survives lease release/reacquire between closed-loop jobs in one script.
        self._session_kernel_cache: Dict[str, Dict[str, Any]] = {}
        self._components_ns: Optional[Any] = None

    @property
    def components(self) -> "ComponentsNamespace":
        """Fluent proxies: ``lab.components.tag_20.move(x=12.5)``."""
        from .components import ComponentsNamespace

        if self._components_ns is None:
            self._components_ns = ComponentsNamespace(self)
        return self._components_ns

    def _vlog(self, msg: str, *args: Any) -> None:
        if self.verbose:
            _SESSION_LOG.info(msg, *args)

    @classmethod
    def connect(
        cls,
        backend_id: BackendId,
        **kwargs: Any,
    ) -> "CloudLabsClient":
        """Alias for module-level :func:`connect`."""
        return connect(backend_id, **kwargs)

    # --- Context manager (lease lifecycle) ---------------------------------

    def __enter__(self) -> "CloudLabsClient":
        self.acquire_lease()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.release_lease()

    @property
    def lease(self) -> Optional[SessionLease]:
        """Active lease, or ``None`` if not acquired."""
        return self._lease

    @property
    def lease_id(self) -> Optional[LeaseId]:
        return self._lease.lease_id if self._lease else None

    def acquire_lease(self) -> SessionLease:
        """Acquire an exclusive backend lease (idempotent while already held)."""
        if self._lease is not None:
            return self._lease

        payload: Dict[str, Any] = {
            "backend_id": self.backend_id,
            "holder": self.holder,
            "mode": self.mode,
        }
        if self.snapshot_ref:
            payload["snapshot_ref"] = self.snapshot_ref

        self._vlog(
            "acquiring lease backend=%s holder=%s",
            self.backend_id,
            self.holder,
        )
        data = self._post_json("/api/jobs/lease/acquire", payload)
        self._lease = SessionLease.from_api(data)
        self._released = False
        self._start_heartbeat()
        self._vlog(
            "lease acquired id=%s expires=%s",
            self._lease.lease_id,
            self._lease.expires_at.isoformat(),
        )
        return self._lease

    def release_lease(self) -> None:
        """Release the active lease (safe to call multiple times)."""
        self._stop_heartbeat()
        if self._released or self._lease is None:
            return

        lease_id = self._lease.lease_id
        try:
            self._vlog("releasing lease id=%s", lease_id)
            self._post_json(
                "/api/jobs/lease/release",
                {"lease_id": lease_id},
                include_lease=False,
            )
        except CloudLabsLeaseError as exc:
            # Best-effort teardown — script crash recovery should not mask the
            # original exception raised inside the ``with`` block.
            _LOG.warning("cloudlabs: lease release failed id=%s: %s", lease_id, exc)
        finally:
            self._released = True
            self._lease = None

    def _require_lease(self) -> SessionLease:
        if self._lease is None:
            raise CloudLabsLeaseError(
                "No active lease. Use ``with CloudLabsClient.connect(...) as lab`` "
                "or call ``acquire_lease()`` before primitives."
            )
        return self._lease

    @property
    def loaded_snapshot(self) -> Optional[LoadedSnapshot]:
        """Snapshot selected by :meth:`load_snapshot`, if any."""
        return self._loaded_snapshot

    def _start_heartbeat(self) -> None:
        interval = self.heartbeat_s
        if interval is not None and interval <= 0:
            return
        period = float(interval if interval is not None else self._DEFAULT_HEARTBEAT_S)
        self._heartbeat_stop.clear()

        def _loop() -> None:
            while not self._heartbeat_stop.wait(period):
                lease = self._lease
                if lease is None or self._released:
                    break
                try:
                    self._post_json(
                        "/api/jobs/lease/heartbeat",
                        {"lease_id": lease.lease_id},
                        include_lease=False,
                    )
                    _LOG.debug("cloudlabs: lease heartbeat ok id=%s", lease.lease_id)
                except CloudLabsError as exc:
                    _LOG.warning("cloudlabs: lease heartbeat failed: %s", exc)

        self._heartbeat_thread = threading.Thread(
            target=_loop,
            name="cloudlabs-lease-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread.start()

    def _stop_heartbeat(self) -> None:
        self._heartbeat_stop.set()
        thread = self._heartbeat_thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._heartbeat_thread = None

    # --- Runtime queries ---------------------------------------------------

    def get_lab_state(self) -> Dict[str, Any]:
        """Fetch the live runtime JSON (``GET /api/lab-state``)."""
        return self._get_json("/api/lab-state")

    def wait_until_idle(
        self,
        *,
        poll_interval_s: float = 0.25,
        timeout_s: float = 120.0,
    ) -> Dict[str, Any]:
        """Poll ``system_status`` until it is ``IDLE`` (or terminal holding)."""
        import time

        deadline = time.monotonic() + timeout_s
        last: Dict[str, Any] = {}
        while time.monotonic() < deadline:
            last = self.get_lab_state()
            status = str(last.get("system_status", "")).upper()
            if status in {"IDLE", "HOLDING"}:
                return last
            if status not in {"BUSY", "OPTIMIZING", "TELEOP"}:
                return last
            time.sleep(poll_interval_s)

        raise CloudLabsTimeoutError(
            f"Timed out after {timeout_s}s waiting for IDLE; last status="
            f"{last.get('system_status')!r}"
        )

    def wait_for_primitive_settled(
        self,
        *,
        poll_interval_s: float = 0.1,
        busy_appear_timeout_s: float = 1.5,
        timeout_s: float = 120.0,
    ) -> Dict[str, Any]:
        """Wait until a dispatched primitive finishes (reconcile-friendly).

        Mirrors the UI reconcile runner: tolerates fast no-op steps that never
        enter ``BUSY``, but requires ``BUSY`` → ``IDLE`` when motion occurs.
        """
        deadline = time.monotonic() + timeout_s
        start = time.monotonic()
        saw_busy = False
        last: Dict[str, Any] = {}

        while time.monotonic() < deadline:
            last = self.get_lab_state()
            status = str(last.get("system_status", "")).upper()
            if status in {"BUSY", "OPTIMIZING", "TELEOP"}:
                saw_busy = True
            if status in {"IDLE", "HOLDING"}:
                if saw_busy or (time.monotonic() - start) > busy_appear_timeout_s:
                    return last
            time.sleep(poll_interval_s)

        raise CloudLabsTimeoutError(
            f"Timed out after {timeout_s}s waiting for primitive settle; "
            f"last status={last.get('system_status')!r}"
        )

    # --- Snapshot + reconcile ----------------------------------------------

    def list_control_repos(self) -> List[Dict[str, Any]]:
        """List local version-control repos on this lab deployment."""
        payload = self._get_json("/api/control/repos")
        repos = payload.get("repos")
        return list(repos) if isinstance(repos, list) else []

    def list_catalog_pins(self) -> List[Dict[str, Any]]:
        """List approved remote catalog pins for this backend (frozen snapshots)."""
        payload = self._get_json("/api/catalog/pins")
        pins = payload.get("pins") if isinstance(payload, dict) else None
        return list(pins) if isinstance(pins, list) else []

    def request_publish(
        self,
        *,
        repo_id: str,
        configuration_id: str,
        branch: str = "main",
        message: str = "",
        pin_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Ask to promote a local commit to the remote catalog (owner approves on real)."""
        return self._post_json(
            "/api/catalog/publish-requests",
            {
                "repo_id": repo_id,
                "configuration_id": configuration_id,
                "branch": branch,
                "message": message,
                "requested_by": self.holder,
                "pin_id": pin_id,
                "backend_id": self.backend_id,
            },
            include_lease=False,
        )

    def commit_configuration(
        self,
        *,
        repo: str,
        message: str = "",
        branch: str = "main",
        parent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Snapshot the live bench into a new local control-repo commit."""
        self._require_lease()
        repo_id = repo.strip()
        if not repo_id:
            raise CloudLabsReconcileError("repo id must be non-empty")
        return self._post_json(
            f"/api/control/{repo_id}/configurations",
            {
                "message": message,
                "branch": branch.strip() or "main",
                "parent_id": parent_id,
            },
            include_lease=False,
        )

    def fork_branch(
        self,
        *,
        repo: str,
        branch: str,
        parent_id: str,
    ) -> Dict[str, Any]:
        """Fork a new branch from ``parent_id`` in a local control repo."""
        self._require_lease()
        repo_id = repo.strip()
        branch_name = branch.strip()
        parent = parent_id.strip()
        if not repo_id or not branch_name or not parent:
            raise CloudLabsReconcileError("repo, branch, and parent_id are required")
        return self._post_json(
            f"/api/control/{repo_id}/branches",
            {"branch": branch_name, "parent_id": parent},
            include_lease=False,
        )

    def stash(
        self,
        *,
        repo: str,
        message: str = "",
    ) -> Dict[str, Any]:
        """Stash uncommitted bench changes and reconcile back to the applied node."""
        self._require_lease()
        repo_id = repo.strip()
        if not repo_id:
            raise CloudLabsReconcileError("repo id must be non-empty")
        return self._post_json(
            f"/api/control/{repo_id}/stash",
            {"message": message, "preview": False},
            include_lease=False,
        )

    def pop_stash(self, *, repo: str) -> Dict[str, Any]:
        """Restore the stash onto the bench as uncommitted edits."""
        self._require_lease()
        repo_id = repo.strip()
        if not repo_id:
            raise CloudLabsReconcileError("repo id must be non-empty")
        return self._post_json(
            f"/api/control/{repo_id}/stash/pop",
            {"preview": False},
            include_lease=False,
        )

    def drop_stash(self, *, repo: str) -> Dict[str, Any]:
        """Discard the current stash without moving the bench."""
        self._require_lease()
        repo_id = repo.strip()
        if not repo_id:
            raise CloudLabsReconcileError("repo id must be non-empty")
        url = f"{self.base_url}/api/control/{repo_id}/stash"
        try:
            resp = self._session.delete(url, timeout=self.timeout_s)
        except requests.RequestException as exc:
            raise CloudLabsConnectionError(f"DELETE {url} failed: {exc}") from exc
        if resp.status_code >= 400:
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text
            raise CloudLabsCommandError(
                f"drop_stash failed: {detail}",
                status_code=resp.status_code,
                detail=detail,
            )
        if not resp.content:
            return {"status": "ok"}
        try:
            payload = resp.json()
        except Exception:
            return {"status": "ok"}
        return payload if isinstance(payload, dict) else {"status": "ok"}

    def load_snapshot(
        self,
        repo: Optional[str] = None,
        branch: str = "main",
        *,
        catalog_pin: Optional[str] = None,
    ) -> LoadedSnapshot:
        """Resolve a local branch head or a frozen catalog pin for this session.

        Pass either ``repo`` (+ optional ``branch``) **or** ``catalog_pin=…``,
        never both. Does not move hardware — call :meth:`reconcile_hardware`
        afterward to apply the configuration on the bench.
        """
        self._require_lease()
        if catalog_pin is not None and repo is not None:
            raise CloudLabsReconcileError(
                "pass either repo/branch (local) or catalog_pin=… (catalog), not both"
            )
        if catalog_pin is not None:
            pin_id = catalog_pin.strip()
            if not pin_id:
                raise CloudLabsReconcileError("catalog_pin must be non-empty")
            match = None
            for row in self.list_catalog_pins():
                if isinstance(row, dict) and str(row.get("pin_id")) == pin_id:
                    match = row
                    break
            if match is None:
                raise CloudLabsReconcileError(f"unknown catalog pin {pin_id!r}")
            repo_id = str(match.get("repo_id") or "").strip()
            branch_name = str(match.get("branch") or "main").strip() or "main"
            configuration_id = str(
                match.get("configuration_id") or match.get("commit") or ""
            ).strip()
            display_name = str(match.get("display_name") or "").strip() or None
            if not repo_id or not configuration_id:
                raise CloudLabsReconcileError(f"catalog pin {pin_id!r} is incomplete")
            loaded = LoadedSnapshot(
                repo_id=repo_id,
                branch=branch_name,
                configuration_id=configuration_id,
                source="catalog",
                pin_id=pin_id,
                display_name=display_name,
            )
            self._loaded_snapshot = loaded
            self.snapshot_ref = f"catalog:{pin_id}:{configuration_id}"
            self._vlog(
                "loaded catalog pin=%s commit=%s (%s)",
                pin_id,
                configuration_id,
                loaded.label(),
            )
            return loaded

        if not repo:
            raise CloudLabsReconcileError(
                "repo id must be non-empty when catalog_pin is omitted"
            )
        repo_id = repo.strip()
        branch_name = branch.strip() or "main"
        if not repo_id:
            raise CloudLabsReconcileError("repo id must be non-empty")

        known = {str(r.get("repo_id")) for r in self.list_control_repos()}
        if repo_id not in known:
            raise CloudLabsReconcileError(
                f"unknown control repo {repo_id!r}; available={sorted(known)}"
            )

        history = self._get_json(
            f"/api/control/{repo_id}/history",
            params={"branch": branch_name},
        )
        configuration_id = history.get("head")
        if not isinstance(configuration_id, str) or not configuration_id.strip():
            raise CloudLabsReconcileError(
                f"branch {branch_name!r} has no head commit in repo {repo_id!r}"
            )

        loaded = LoadedSnapshot(
            repo_id=repo_id,
            branch=branch_name,
            configuration_id=configuration_id.strip(),
            source="local",
        )
        self._loaded_snapshot = loaded
        self.snapshot_ref = f"{repo_id}@{branch_name}:{loaded.configuration_id}"
        self._vlog(
            "loaded local snapshot repo=%s branch=%s commit=%s",
            loaded.repo_id,
            loaded.branch,
            loaded.configuration_id,
        )
        return loaded

    def reconcile_hardware(
        self,
        *,
        repo: Optional[str] = None,
        branch: Optional[str] = None,
        configuration_id: Optional[str] = None,
        step_delay_s: float = 0.05,
        timeout_s: float = 120.0,
        on_progress: Optional[ProgressCallback] = None,
    ) -> ReconcileResult:
        """Apply the loaded snapshot to hardware with visible step telemetry.

        Executes each reconcile primitive through ``/api/command`` (one step at a
        time), logs progress like the UI reconcile runner, then finalizes the
        checkout pointer when all steps succeed.

        Parameters
        ----------
        repo, branch, configuration_id:
            Override the snapshot from :meth:`load_snapshot`. If ``configuration_id``
            is omitted, ``repo`` + ``branch`` must resolve to a branch head.
        on_progress:
            Optional callback ``(message, step, done, total)`` for notebooks/UI.
        """
        self._require_lease()
        if on_progress is not None:
            progress = on_progress
        elif self.verbose:
            progress = default_progress_logger
        else:
            progress = lambda *_args, **_kwargs: None

        snap = self._resolve_reconcile_target(
            repo=repo,
            branch=branch,
            configuration_id=configuration_id,
        )

        progress(
            f"Status: Reconciling bench coordinates for {snap.repo_id}@{snap.branch}…",
            ReconcileStep(0, "PLAN", None, {}),
            0,
            0,
        )

        try:
            preview = self._post_json(
                f"/api/control/{snap.repo_id}/checkout",
                {
                    "configuration_id": snap.configuration_id,
                    "mode": "hard",
                    "preview": True,
                    "initialization_policy": self.initialization_policy,
                },
                include_lease=True,
            )
        except CloudLabsCommandError as exc:
            raise CloudLabsReconcileError(
                str(exc),
                status_code=exc.status_code,
                detail=exc.detail,
            ) from exc

        plan_raw = preview.get("plan")
        if not isinstance(plan_raw, list):
            plan_raw = []
        steps = build_reconcile_steps(plan_raw)
        total = len(steps)

        if total == 0:
            progress(
                "Status: Bench already matches snapshot — finalizing checkout.",
                ReconcileStep(0, "NOOP", None, {}),
                0,
                0,
            )
            self._finalize_checkout(snap)
            return ReconcileResult(
                repo_id=snap.repo_id,
                branch=snap.branch,
                configuration_id=snap.configuration_id,
                steps_total=0,
                steps_executed=0,
                steps=[],
                finalized=True,
                message="No reconcile primitives required.",
            )

        progress(
            f"Status: Reconciling bench coordinates — {total} step(s) planned.",
            steps[0],
            0,
            total,
        )

        executed = 0
        try:
            for step in steps:
                step.status = "active"
                progress(
                    f"Status: Reconciling bench coordinates — step {step.index + 1}/{total}",
                    step,
                    executed,
                    total,
                )
                envelope = step.to_envelope()
                if envelope.get("target_id") is None:
                    envelope.pop("target_id", None)
                self._post_command(envelope)
                self.wait_for_primitive_settled(timeout_s=timeout_s)
                step.status = "done"
                executed += 1
                if step_delay_s > 0 and executed < total:
                    time.sleep(step_delay_s)
        except (CloudLabsCommandError, CloudLabsTimeoutError) as exc:
            step.status = "error"
            progress(
                f"Status: Reconcile FAILED at step {step.index + 1}/{total}: {exc}",
                step,
                executed,
                total,
            )
            raise CloudLabsReconcileError(
                f"Reconcile failed at step {step.index + 1} ({step.label}): {exc}",
                step_index=step.index,
            ) from exc

        progress(
            "Status: Reconcile steps complete — finalizing checkout.",
            steps[-1],
            total,
            total,
        )
        self._finalize_checkout(snap)
        self._loaded_snapshot = snap

        result = ReconcileResult(
            repo_id=snap.repo_id,
            branch=snap.branch,
            configuration_id=snap.configuration_id,
            steps_total=total,
            steps_executed=executed,
            steps=steps,
            finalized=True,
            message=f"Reconciled {executed} primitive(s) to {snap.configuration_id}.",
        )
        progress(
            f"Status: Reconcile complete ({executed}/{total} steps).",
            steps[-1],
            total,
            total,
        )
        return result

    def _resolve_reconcile_target(
        self,
        *,
        repo: Optional[str],
        branch: Optional[str],
        configuration_id: Optional[str],
    ) -> LoadedSnapshot:
        if configuration_id and repo:
            branch_name = (branch or "main").strip() or "main"
            return LoadedSnapshot(
                repo_id=repo.strip(),
                branch=branch_name,
                configuration_id=configuration_id.strip(),
                source="local",
            )
        if self._loaded_snapshot is not None and not repo and not branch and not configuration_id:
            return self._loaded_snapshot
        if repo:
            return self.load_snapshot(repo, branch or "main")
        raise CloudLabsReconcileError(
            "No snapshot loaded. Call load_snapshot(repo, branch) or "
            "load_snapshot(catalog_pin=…) first, or pass repo + branch / "
            "configuration_id to reconcile_hardware()."
        )

    def _finalize_checkout(self, snap: LoadedSnapshot) -> Dict[str, Any]:
        try:
            return self._post_json(
                f"/api/control/{snap.repo_id}/checkout",
                {
                    "configuration_id": snap.configuration_id,
                    "mode": "hard",
                    "finalize": True,
                    "initialization_policy": self.initialization_policy,
                },
                include_lease=True,
            )
        except CloudLabsCommandError as exc:
            raise CloudLabsReconcileError(
                f"Checkout finalize failed: {exc}",
                status_code=exc.status_code,
                detail=exc.detail,
            ) from exc

    # --- Primitive proxies -------------------------------------------------

    def move_component(
        self,
        tag_id: str,
        tunable_path: str,
        value: Union[int, float],
    ) -> Dict[str, Any]:
        """Command a tunable on ``tag_id`` via the matching primitive.

        Supported ``tunable_path`` values (same allowlist as ensemble variables):

        - ``tunables.nominal_motor_positions.<motor_id>`` → ``SET_MOTOR_SETPOINT``
        - ``tunables.nominal_pose.x|y|rotation`` → ``MOVE_COMPONENT`` (full pose)
        """
        self._require_lease()
        tag_id = tag_id.strip()
        path = tunable_path.strip()

        try:
            parsed = parse_variable_path(path)
        except Exception as exc:
            raise CloudLabsPathError(path, tag_id, str(exc)) from exc

        numeric = float(value)
        self._vlog(
            "set tunable tag=%s path=%s value=%s",
            tag_id,
            path,
            numeric,
        )

        if parsed.kind == "motor":
            assert parsed.motor_id is not None
            return self._post_command(
                {
                    "action": "SET_MOTOR_SETPOINT",
                    "target_id": tag_id,
                    "parameters": {
                        "motor_id": int(parsed.motor_id),
                        "angle_deg": numeric,
                    },
                }
            )

        assert parsed.axis is not None
        state = self.get_lab_state()
        pose = _read_nominal_pose(state, tag_id)
        pose[parsed.axis] = numeric
        return self._post_command(
            {
                "action": "MOVE_COMPONENT",
                "target_id": tag_id,
                "parameters": {
                    "target_x": float(pose["x"]),
                    "target_y": float(pose["y"]),
                    "rotation": float(pose.get("rotation", 0.0)),
                },
            }
        )

    def capture_measurable(
        self,
        tag_id: str,
        measurable_path: str,
    ) -> Any:
        """Record fresh measurables and return the field at ``measurable_path``.

        ``measurable_path`` may be ``measurables.<field>`` or the short
        ``<field>`` form (e.g. ``camera_image``).
        """
        self._require_lease()
        tag_id = tag_id.strip()
        field = _normalize_measurable_path(measurable_path)

        self._vlog(
            "capture measurable tag=%s field=%s",
            tag_id,
            field,
        )
        result = self._post_command(
            {
                "action": "RECORD_MEASURABLES",
                "target_id": tag_id,
                "parameters": {},
            }
        )
        measurables = result.get("measurables")
        if not isinstance(measurables, dict):
            raise CloudLabsCommandError(
                "RECORD_MEASURABLES did not return measurables",
                action="RECORD_MEASURABLES",
                target_id=tag_id,
            )
        if field not in measurables:
            raise CloudLabsPathError(
                measurable_path,
                tag_id,
                f"field {field!r} missing after capture; keys={sorted(measurables)}",
            )
        return measurables[field]

    def measurable(self, tag_id: str, measurable_path: str) -> "MeasurableHandle":
        """Return a handle for lazy tensor resolve (Phase D).

        Example::

            tensor = lab.measurable("tag_22", "camera_image").resolve(record=True)
            arr = tensor.data  # numpy HxWx3 uint8 after resolve
        """
        from .measurable import MeasurableHandle

        return MeasurableHandle(
            client=self,
            tag_id=tag_id.strip(),
            field=_normalize_measurable_path(measurable_path),
        )

    def refresh_pose(
        self,
        tag_id: str,
        *,
        include_measurables: bool = True,
    ) -> Dict[str, Any]:
        """Read nominal_pose and optional measurables for one tag from live state."""
        tag_id = tag_id.strip()
        state = self.get_lab_state()
        components = state.get("components")
        if not isinstance(components, dict):
            raise CloudLabsPathError("components", tag_id, "runtime has no components map")
        entry = components.get(tag_id)
        if not isinstance(entry, dict):
            raise CloudLabsPathError("statecontrol", tag_id, f"tag {tag_id!r} not found")
        sc = entry.get("statecontrol")
        if not isinstance(sc, dict):
            raise CloudLabsPathError("statecontrol", tag_id, "statecontrol missing")
        tunables = sc.get("tunables")
        if not isinstance(tunables, dict):
            raise CloudLabsPathError("tunables", tag_id, "tunables missing")
        pose = tunables.get("nominal_pose")
        if not isinstance(pose, dict):
            raise CloudLabsPathError("tunables.nominal_pose", tag_id, "nominal_pose missing")
        out: Dict[str, Any] = {
            "tag_id": tag_id,
            "nominal_pose": {
                "x": float(pose.get("x", 0.0)),
                "y": float(pose.get("y", 0.0)),
                "rotation": float(pose.get("rotation", 0.0)),
            },
        }
        if include_measurables:
            meas = sc.get("measurables")
            out["measurables"] = dict(meas) if isinstance(meas, dict) else {}
        return out

    def refresh_state(self) -> Dict[str, Any]:
        """Alias for :meth:`get_lab_state` (SDK naming parity)."""
        return self.get_lab_state()

    # --- Capability wiki / discovery ---------------------------------------

    def _backend_params(self) -> Dict[str, str]:
        return {"backend_id": self.backend_id}

    def _ensure_wiki_catalog(self, *, force: bool = False) -> None:
        if (
            not force
            and self._wiki_rows_cache is not None
            and self._wiki_active_tags is not None
        ):
            return
        rows = self._get_payload(
            "/api/catalog/library-rows",
            params=self._backend_params(),
        )
        if isinstance(rows, list):
            library_rows = rows
        elif isinstance(rows, dict) and isinstance(rows.get("components"), list):
            library_rows = rows["components"]
        else:
            library_rows = []
        active = self._get_json(
            "/api/catalog/active-tags",
            params=self._backend_params(),
        )
        cache: Dict[str, Dict[str, Any]] = {}
        if isinstance(library_rows, list):
            for row in library_rows:
                if isinstance(row, dict) and row.get("tag_id"):
                    cache[str(row["tag_id"])] = row
        self._wiki_rows_cache = cache
        tags = active.get("tag_ids") if isinstance(active, dict) else None
        self._wiki_active_tags = {
            str(t) for t in (tags or []) if t is not None
        }

    def _ensure_wiki_registries(self, *, force: bool = False) -> Dict[str, Any]:
        if not force and self._wiki_registries is not None:
            return self._wiki_registries
        payload = self._get_json("/api/platform/registries")
        self._wiki_registries = payload if isinstance(payload, dict) else {}
        return self._wiki_registries

    def list_components(self, *, refresh: bool = False) -> List[Dict[str, Any]]:
        """List capability-catalog components for this backend (wiki index).

        Returns light rows: ``tag_id``, ``name``, ``type``, ``on_bench``.
        """
        self._ensure_wiki_catalog(force=refresh)
        assert self._wiki_rows_cache is not None
        assert self._wiki_active_tags is not None
        out: List[Dict[str, Any]] = []
        for tag_id, row in sorted(self._wiki_rows_cache.items()):
            out.append(
                {
                    "tag_id": tag_id,
                    "name": row.get("name"),
                    "type": row.get("type"),
                    "id": row.get("id"),
                    "on_bench": tag_id in self._wiki_active_tags,
                }
            )
        return out

    def describe_component(
        self,
        tag_id: str,
        *,
        refresh: bool = False,
    ) -> Dict[str, Any]:
        """Wiki-style description: tunables, measurables, parameters, primitives.

        Each tunable/measurable row includes a ``snippet`` suitable for copy-paste
        into scripts (same strings as the ``/wiki`` UI).
        """
        from .wiki import describe_component_row

        tag_id = tag_id.strip()
        self._ensure_wiki_catalog(force=refresh)
        self._ensure_wiki_registries(force=refresh)
        assert self._wiki_rows_cache is not None
        assert self._wiki_active_tags is not None
        row = self._wiki_rows_cache.get(tag_id)
        if row is None:
            raise CloudLabsPathError(
                "catalog",
                tag_id,
                f"tag {tag_id!r} not in component library for backend {self.backend_id!r}",
            )
        return describe_component_row(
            row,
            on_bench=tag_id in self._wiki_active_tags,
            registries=self._wiki_registries,
        )

    def script_handle(
        self,
        tag_id: str,
        path: str,
        *,
        refresh: bool = False,
    ) -> str:
        """Return the copy-paste SDK snippet for a tunable or measurable path.

        ``path`` may be ``measurables.camera_image``, ``camera_image``,
        ``tunables.nominal_pose.x``, or ``nominal_pose.x``.
        """
        desc = self.describe_component(tag_id, refresh=refresh)
        raw = path.strip()
        if raw.startswith("measurables."):
            field = raw[len("measurables.") :]
            for row in desc["measurables"]:
                if row.get("field") == field or row.get("path") == raw:
                    return str(row["snippet"])
            raise CloudLabsPathError(raw, tag_id, "measurable not in capability contract")
        if raw.startswith("tunables."):
            needle = raw
        else:
            needle = f"tunables.{raw}" if not raw.startswith("measurables.") else raw
        # Prefer measurable short form
        for row in desc["measurables"]:
            if row.get("field") == raw:
                return str(row["snippet"])
        for row in desc["tunables"]:
            if row.get("path") == needle or row.get("path") == raw:
                return str(row["snippet"])
        raise CloudLabsPathError(path, tag_id, "path not found in capability contract")

    def optimize(
        self,
        target_id: str,
        parameters: Mapping[str, Any],
        *,
        wait: bool = True,
        poll_interval_s: float = 0.5,
        timeout_s: float = 3600.0,
        snapshot: Optional[Mapping[str, Any]] = None,
        kernels: Optional[List[str]] = None,
        on_success: Optional[Mapping[str, Any]] = None,
        kernel_packages: Optional[List[Mapping[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Submit a closed-loop ensemble OPTIMIZE job and optionally wait."""
        from .jobs import wait_for_job

        params = dict(parameters)
        params.setdefault("mode", "ensemble")
        submit_body: Dict[str, Any] = {
            "mode": "closed_loop",
            "backend_id": self.backend_id,
            "command": {
                "action": "OPTIMIZE",
                "target_id": target_id.strip(),
                "parameters": params,
            },
        }
        if snapshot:
            submit_body["snapshot"] = dict(snapshot)
        elif self._loaded_snapshot is not None:
            submit_body["snapshot"] = self._loaded_snapshot.to_job_snapshot()
        if kernels:
            submit_body["kernels"] = list(kernels)
        if kernel_packages:
            submit_body["kernel_packages"] = [dict(p) for p in kernel_packages]
        if on_success:
            submit_body["on_success"] = dict(on_success)
        if self.initialization_policy:
            submit_body["initialization_policy"] = self.initialization_policy
        self._vlog(
            "optimize submit target=%s wait=%s kernels=%s",
            target_id.strip(),
            wait,
            kernels or params.get("kernels"),
        )
        # Prefer inline packages (no lease) so the job can acquire the backend.
        include_lease = (
            self._lease is not None
            and not self._released
            and not kernel_packages
        )
        submitted = self._post_json(
            "/api/jobs/submit",
            submit_body,
            include_lease=include_lease,
        )
        if not wait:
            return submitted
        job_id = str(submitted.get("job_id") or "")
        if not job_id:
            raise CloudLabsCommandError("job submit did not return job_id")
        self._vlog("optimize waiting job_id=%s", job_id)
        return wait_for_job(
            self,
            job_id,
            poll_interval_s=poll_interval_s,
            timeout_s=timeout_s,
        )

    # --- Intent helpers (clean authoring) ----------------------------------

    def prepare(
        self,
        repo: Optional[str] = None,
        branch: str = "main",
        *,
        catalog_pin: Optional[str] = None,
        reconcile: bool = True,
    ) -> LoadedSnapshot:
        """Load a snapshot and optionally reconcile hardware onto it."""
        snap = self.load_snapshot(repo=repo, branch=branch, catalog_pin=catalog_pin)
        if reconcile:
            self.reconcile_hardware()
        return snap

    def set_tunable(
        self,
        tag_id: str,
        path: str,
        value: Union[int, float],
    ) -> Dict[str, Any]:
        """Alias for :meth:`move_component` with a clearer authoring name."""
        return self.move_component(tag_id, path, value)

    def register_kernel_file(
        self,
        name: str,
        path: str,
        *,
        output_kind: str = "scalar",
        feature_names: Optional[Sequence[str]] = None,
        label: Optional[str] = None,
        description: str = "",
    ) -> str:
        """Upload a prebuilt ``.pt`` as a session kernel; return ``kernel_id``."""
        import base64
        from pathlib import Path

        data = Path(path).read_bytes()
        artifact_b64 = base64.b64encode(data).decode("ascii")
        self._vlog("register_kernel_file name=%s bytes=%s kind=%s", name, len(data), output_kind)
        body: Dict[str, Any] = {
            "name": name,
            "artifact_b64": artifact_b64,
            "output_kind": output_kind,
            "label": label or name,
            "description": description,
            "backend_id": self.backend_id,
        }
        if feature_names:
            body["feature_names"] = list(feature_names)
        resp = self._post_json("/api/kernels/session", body, include_lease=True)
        kernel = resp.get("kernel") or {}
        kid = str(kernel.get("id") or "").strip()
        if not kid:
            raise CloudLabsCommandError("register_kernel_file did not return kernel id")
        self._session_kernel_cache[kid] = {
            "kernel_id": kid,
            "artifact_b64": artifact_b64,
            "digest": kernel.get("digest"),
            "output_kind": kernel.get("output_kind") or output_kind,
            "feature_names": list(kernel.get("feature_names") or feature_names or []),
            "label": kernel.get("label") or label or name,
            "description": kernel.get("description") or description,
            "inputs": kernel.get("inputs"),
            "outputs": kernel.get("outputs"),
        }
        self._vlog("registered session kernel id=%s", kid)
        return kid

    def register_kernel(
        self,
        name: str,
        *,
        module: Any = None,
        path: Optional[str] = None,
        output_kind: str = "scalar",
        feature_names: Optional[Sequence[str]] = None,
        label: Optional[str] = None,
        description: str = "",
    ) -> str:
        """Register a session TorchScript kernel from ``nn.Module`` or ``.pt`` path."""
        if path:
            return self.register_kernel_file(
                name,
                path,
                output_kind=output_kind,
                feature_names=feature_names,
                label=label,
                description=description,
            )
        if module is None:
            raise CloudLabsCommandError("register_kernel requires module= or path=")
        import tempfile
        from pathlib import Path

        try:
            import torch
        except ImportError as exc:
            raise CloudLabsCommandError(
                "PyTorch required to compile module; pass path= to a .pt instead"
            ) from exc

        if hasattr(module, "forward") and not isinstance(module, torch.jit.ScriptModule):
            scripted = torch.jit.script(module)
        else:
            scripted = module
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "model.pt"
            scripted.save(str(out))
            return self.register_kernel_file(
                name,
                str(out),
                output_kind=output_kind,
                feature_names=feature_names,
                label=label,
                description=description,
            )

    def eval_kernel(
        self,
        tag_id: str,
        field: str,
        *,
        kernel_id: str,
    ) -> Union[float, List[float]]:
        """Capture a measurable and run a TorchScript kernel on the edge.

        Both catalog and ``session.*`` kernels are evaluated via
        ``POST /api/kernels/eval`` so the laptop never needs local ``.pt``
        artifacts or the monorepo kernel runtime.
        """
        kid = kernel_id.strip()
        self._vlog(
            "eval_kernel tag=%s field=%s kernel=%s",
            tag_id,
            field,
            kid,
        )
        resp = self._post_json(
            "/api/kernels/eval",
            {
                "kernel_id": kid,
                "tag_id": tag_id.strip(),
                "field": field,
                "backend_id": self.backend_id,
            },
            include_lease=True,
        )
        if str(resp.get("kind") or "") == "features":
            feats = [float(x) for x in (resp.get("features") or [])]
            self._vlog("eval_kernel features=%s", feats)
            return feats
        scalar = float(resp.get("scalar") or 0.0)
        self._vlog("eval_kernel result=%s", scalar)
        return scalar

    def variable(
        self,
        tag_id: str,
        path: str,
        *,
        bounds: Bounds,
        variable_id: Optional[str] = None,
        unit: str = "deg",
        delta: bool = False,
    ) -> VariableSpec:
        """Build one continuous ensemble variable (no raw IR dict)."""
        return VariableSpec(
            tag_id=tag_id,
            path=path,
            bounds=bounds,
            variable_id=variable_id,
            unit=unit,
            delta=delta,
        )

    def kernel_match(
        self,
        tag_id: str,
        field: str,
        *,
        kernel_id: str,
        target: Any,
        term_id: str = "match_m0",
        weight: float = 1.0,
        metric: Optional[str] = None,
        feature_index: Optional[Any] = None,
    ) -> KernelMatchSpec:
        """One objective term matching a TorchScript kernel to a frozen target.

        Pass ``target=(x, y)`` for centroid-style feature kernels
        (``builtin.roi_centroid``); pass a float for scalar kernels or a single
        feature channel (with ``feature_index``).
        """
        return KernelMatchSpec(
            tag_id=tag_id,
            field=field,
            kernel_id=kernel_id,
            target=target,
            term_id=term_id,
            weight=weight,
            metric=metric,
            feature_index=feature_index,
        )

    def run_optimize(
        self,
        *,
        variables: Sequence[Union[VariableSpec, Mapping[str, Any]]],
        objective: Any,
        max_evals: int = 25,
        kernels: Optional[Sequence[str]] = None,
        wait: bool = True,
        poll_interval_s: float = 0.5,
        timeout_s: float = 3600.0,
        settle_ms: int = 50,
        session_label: Optional[str] = None,
        reacquire_lease: bool = True,
    ) -> Dict[str, Any]:
        """Build ensemble IR from any objective, submit, wait, reacquire.

        ``objective`` may be a compiled runtime dict, an authoring graph dict,
        or an :class:`ObjectiveGraphBuilder` (compiled inside).
        Session kernels referenced by terms (or ``kernels=``) are exported as
        inline packages so the job runner can acquire the backend.
        """
        from .intent import build_ensemble_parameters
        from .jobs import wait_for_job
        from .objective import ObjectiveGraphBuilder, compile_objective

        if isinstance(objective, ObjectiveGraphBuilder):
            obj = objective.compile()
        elif isinstance(objective, Mapping):
            # Already runtime (has terms[].source) or authoring graph — normalize.
            obj = compile_objective(objective)
        else:
            raise CloudLabsCommandError(
                "run_optimize: objective must be a dict or ObjectiveGraphBuilder"
            )

        params = build_ensemble_parameters(
            variables=variables,
            objective=obj,
            max_evals=max_evals,
            session_label=session_label,
            settle_ms=settle_ms,
            kernels=kernels,
        )
        first = variables[0]
        if isinstance(first, VariableSpec):
            target_id = first.tag_id
        else:
            target_id = str(first.get("tag_id") or "").strip()
        if not target_id:
            raise CloudLabsCommandError("run_optimize: variables[0] missing tag_id")

        kernel_list: List[str] = list(params.get("kernels") or [])
        self._vlog(
            "run_optimize max_evals=%s target=%s kernels=%s",
            max_evals,
            target_id,
            kernel_list,
        )

        had_lease = self._lease is not None and not self._released
        packages: List[Dict[str, Any]] = []
        session_kids = [k for k in kernel_list if str(k).startswith("session.")]
        if session_kids and had_lease:
            packages = self.export_session_kernel_packages(session_kids)
        # Job runner always acquires its own lease — release imperative hold first.
        if had_lease:
            self.release_lease()

        submitted = self.optimize(
            target_id,
            params,
            wait=False,
            kernels=kernel_list or None,
            kernel_packages=packages or None,
        )
        try:
            if not wait:
                return submitted
            job_id = str(submitted.get("job_id") or "")
            if not job_id:
                raise CloudLabsCommandError("job submit did not return job_id")
            return wait_for_job(
                self,
                job_id,
                poll_interval_s=poll_interval_s,
                timeout_s=timeout_s,
            )
        finally:
            if reacquire_lease and self._lease is None:
                try:
                    self.acquire_lease()
                except CloudLabsLeaseError as exc:
                    _LOG.warning(
                        "cloudlabs: lease reacquire after run_optimize failed: %s", exc
                    )

    def run_cobyla(
        self,
        *,
        variables: Sequence[Union[VariableSpec, Mapping[str, Any]]],
        match_kernel: Union[KernelMatchSpec, Mapping[str, Any]],
        max_evals: int = 25,
        wait: bool = True,
        poll_interval_s: float = 0.5,
        timeout_s: float = 3600.0,
        settle_ms: int = 50,
        session_label: Optional[str] = None,
        reacquire_lease: bool = True,
    ) -> Dict[str, Any]:
        """Thin wrapper: scalar kernel-match objective via :meth:`run_optimize`."""
        from .intent import build_cobyla_ensemble

        params = build_cobyla_ensemble(
            variables=variables,
            match_kernel=match_kernel,
            max_evals=max_evals,
            session_label=session_label,
            settle_ms=settle_ms,
        )
        return self.run_optimize(
            variables=variables,
            objective=params["objective"],
            max_evals=max_evals,
            kernels=list(params.get("kernels") or []),
            wait=wait,
            poll_interval_s=poll_interval_s,
            timeout_s=timeout_s,
            settle_ms=settle_ms,
            session_label=params.get("session_label") or session_label,
            reacquire_lease=reacquire_lease,
        )

    def export_session_kernel_packages(
        self, kernel_ids: Sequence[str]
    ) -> List[Dict[str, Any]]:
        """Download session artifacts for inline job submit (``kernel_packages``).

        Prefers the client-side cache filled by :meth:`register_kernel_file` so
        packages survive lease release/reacquire between closed-loop jobs.
        """
        out: List[Dict[str, Any]] = []
        for kid in kernel_ids:
            kid = str(kid).strip()
            if not kid.startswith("session."):
                continue
            cached = self._session_kernel_cache.get(kid)
            if cached and cached.get("artifact_b64"):
                out.append(dict(cached))
                continue
            art = self._get_json(
                f"/api/kernels/session/{kid}/artifact", include_lease=True
            )
            meta_rows = self._get_json(
                "/api/kernels/session", include_lease=True
            ).get("kernels") or []
            meta = next((r for r in meta_rows if str(r.get("id")) == kid), {})
            pkg = {
                "kernel_id": kid,
                "artifact_b64": art.get("artifact_b64"),
                "digest": art.get("digest") or meta.get("digest"),
                "output_kind": art.get("output_kind")
                or meta.get("output_kind")
                or "scalar",
                "feature_names": art.get("feature_names")
                or meta.get("feature_names")
                or [],
                "label": meta.get("label"),
                "description": meta.get("description") or "",
                "inputs": meta.get("inputs"),
                "outputs": meta.get("outputs"),
            }
            self._session_kernel_cache[kid] = dict(pkg)
            out.append(pkg)
        return out

    def cancel_job(self, job_id: str) -> Dict[str, Any]:
        """Request best-effort cancellation for a queued or running job."""
        return self._post_json(
            f"/api/jobs/{job_id.strip()}/cancel",
            {},
            include_lease=False,
        )

    def list_kernels(self, *, backend: Optional[str] = None) -> List[Dict[str, Any]]:
        """List registered edge kernels (builtins + approved TorchScript)."""
        params: Dict[str, Any] = {}
        if backend:
            params["backend"] = backend
        payload = self._get_json("/api/kernels", params=params)
        rows = payload.get("kernels")
        return list(rows) if isinstance(rows, list) else []

    def describe_kernel(self, kernel_id: str) -> Dict[str, Any]:
        """Return one kernel descriptor (raises if unknown)."""
        kid = kernel_id.strip()
        for row in self.list_kernels():
            if str(row.get("id") or "") == kid:
                return dict(row)
        raise CloudLabsPathError(
            "kernels",
            kid,
            f"unknown kernel {kid!r}; call lab.list_kernels()",
        )

    # --- HTTP helpers ------------------------------------------------------

    def _lease_headers(self) -> Dict[str, str]:
        lease = self._require_lease()
        return {"X-CloudLabs-Lease": lease.lease_id}

    def _get_json(
        self,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        include_lease: bool = False,
    ) -> Dict[str, Any]:
        data = self._get_payload(path, params=params, include_lease=include_lease)
        if not isinstance(data, dict):
            raise CloudLabsConnectionError(f"{path}: expected JSON object")
        return data

    def _get_payload(
        self,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        include_lease: bool = False,
    ) -> Any:
        url = f"{self.base_url}{path}"
        headers: Dict[str, str] = {}
        if include_lease and self._lease is not None and not self._released:
            headers["X-CloudLabs-Lease"] = self._lease.lease_id
        try:
            resp = self._session.get(
                url, params=params, headers=headers or None, timeout=self.timeout_s
            )
        except requests.RequestException as exc:
            raise CloudLabsConnectionError(f"GET {path} failed: {exc}") from exc
        return self._parse_payload(resp, context=path)

    def _post_json(
        self,
        path: str,
        body: Dict[str, Any],
        *,
        include_lease: bool = True,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers: Dict[str, str] = {}
        if include_lease and self._lease is not None:
            headers["X-CloudLabs-Lease"] = self._lease.lease_id
        try:
            resp = self._session.post(
                url,
                json=body,
                headers=headers,
                timeout=self.timeout_s,
            )
        except requests.RequestException as exc:
            raise CloudLabsConnectionError(f"POST {path} failed: {exc}") from exc
        data = self._parse_payload(resp, context=path)
        if not isinstance(data, dict):
            raise CloudLabsConnectionError(f"{path}: expected JSON object")
        return data

    def _post_command(self, body: Dict[str, Any]) -> Dict[str, Any]:
        payload = {**body, "lease_id": self._require_lease().lease_id}
        try:
            return self._post_json("/api/command", payload)
        except CloudLabsLeaseError as exc:
            raise CloudLabsCommandError(
                str(exc),
                status_code=exc.status_code,
                detail=exc.detail,
                action=str(body.get("action")),
                target_id=str(body.get("target_id")),
            ) from exc

    def _parse_response(self, resp: requests.Response, *, context: str) -> Dict[str, Any]:
        data = self._parse_payload(resp, context=context)
        if not isinstance(data, dict):
            raise CloudLabsConnectionError(f"{context}: expected JSON object")
        return data

    def _parse_payload(self, resp: requests.Response, *, context: str) -> Any:
        if resp.status_code == 409:
            detail = _response_detail(resp)
            if detail == "backend_locked" or "locked by" in str(detail).lower():
                raise CloudLabsLeaseError(
                    f"Backend locked during {context}: {detail}",
                    status_code=409,
                    detail=detail,
                )
            raise CloudLabsCommandError(
                f"Conflict during {context}: {detail}",
                status_code=409,
                detail=detail,
            )
        if resp.status_code >= 400:
            detail = _response_detail(resp)
            if "lease" in context or "/api/jobs/lease/" in context:
                raise CloudLabsLeaseError(
                    f"{context} failed ({resp.status_code}): {detail}",
                    status_code=resp.status_code,
                    detail=detail,
                )
            if "/api/control/" in context:
                raise CloudLabsReconcileError(
                    f"{context} failed ({resp.status_code}): {detail}",
                    status_code=resp.status_code,
                    detail=detail,
                )
            raise CloudLabsCommandError(
                f"{context} failed ({resp.status_code}): {detail}",
                status_code=resp.status_code,
                detail=detail,
            )
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError as exc:
            raise CloudLabsConnectionError(
                f"{context}: response was not JSON ({resp.status_code})"
            ) from exc


def _response_detail(resp: requests.Response) -> Any:
    try:
        payload = resp.json()
    except ValueError:
        return resp.text or resp.reason
    if isinstance(payload, dict):
        return payload.get("detail", payload)
    return payload


def _normalize_measurable_path(path: str) -> str:
    path = path.strip()
    if not path:
        raise CloudLabsPathError(path, "", "measurable path must be non-empty")
    match = _MEASURABLE_PATH.match(path)
    if match:
        return match.group("field")
    if "." in path:
        raise CloudLabsPathError(
            path,
            "",
            "expected measurables.<field> or a bare field name",
        )
    return path


def _read_nominal_pose(state: Mapping[str, Any], tag_id: str) -> Dict[str, float]:
    components = state.get("components")
    if not isinstance(components, dict):
        raise CloudLabsPathError(
            "tunables.nominal_pose",
            tag_id,
            "runtime has no components map",
        )
    entry = components.get(tag_id)
    if not isinstance(entry, dict):
        raise CloudLabsPathError(
            "tunables.nominal_pose",
            tag_id,
            f"tag {tag_id!r} not found in runtime",
        )
    sc = entry.get("statecontrol")
    if not isinstance(sc, dict):
        raise CloudLabsPathError(
            "tunables.nominal_pose",
            tag_id,
            "statecontrol missing",
        )
    tunables = sc.get("tunables")
    if not isinstance(tunables, dict):
        raise CloudLabsPathError(
            "tunables.nominal_pose",
            tag_id,
            "tunables missing",
        )
    pose = tunables.get("nominal_pose")
    if not isinstance(pose, dict):
        raise CloudLabsPathError(
            "tunables.nominal_pose",
            tag_id,
            "nominal_pose missing",
        )
    try:
        return {
            "x": float(pose["x"]),
            "y": float(pose["y"]),
            "rotation": float(pose.get("rotation", 0.0)),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise CloudLabsPathError(
            "tunables.nominal_pose",
            tag_id,
            f"nominal_pose incomplete: {pose!r}",
        ) from exc
