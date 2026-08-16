"""Command Matrix — per-backend multi-thread command scheduler.

See ``docs/COMMAND_MATRIX.md`` and ``docs/BATCH_DAG_AND_MATRIX.md``.
Edge advertises shared threads (arm/sense); coordinator owns routing, mode
locks, barriers, queues, drain, and predecessor gates. Motor columns are
``motor.<tag_id>.<motor_id>`` and are created on demand.
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


class CommandMatrixRefuse(Exception):
    """Admission refused (mode lock, OPTIMIZING, etc.)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ExecutionThreadSpec:
    id: str
    kind: str
    motor_id: Optional[int] = None
    tag_id: Optional[str] = None


@dataclass
class QueuedCommand:
    command_id: str
    action: str
    target_id: Optional[str]
    payload: Dict[str, Any]
    resources: List[str]
    kind: str  # normal | barrier | session_lock
    status: str  # queued | running | done | failed | cancelled
    lease_id: str
    error: Optional[str] = None
    enqueued_at: float = field(default_factory=time.time)
    #: Command ids that must reach ``done`` before this item may claim a thread.
    predecessors: List[str] = field(default_factory=list)


@dataclass
class ThreadState:
    thread_id: str
    kind: str
    queue: List[str] = field(default_factory=list)
    running_id: Optional[str] = None
    motor_id: Optional[int] = None
    tag_id: Optional[str] = None


def make_motor_thread_id(tag_id: str, motor_id: int) -> str:
    """Stable matrix column id: ``motor.<tag_id>.<motor_id>``.

    ``motor_id`` is local to the component (inventory often reuses 1/2 per tag).
    """
    return f"motor.{str(tag_id).strip()}.{int(motor_id)}"


def parse_motor_thread_id(thread_id: str) -> Optional[Tuple[str, int]]:
    """Parse ``motor.<tag_id>.<motor_id>``; return None for legacy ``motor.<n>``."""
    tid = str(thread_id or "").strip()
    if not tid.startswith("motor."):
        return None
    rest = tid[len("motor.") :]
    if "." not in rest:
        return None
    tag_part, mid_part = rest.rsplit(".", 1)
    if not tag_part.strip():
        return None
    try:
        return tag_part.strip(), int(mid_part)
    except (TypeError, ValueError):
        return None


@dataclass
class SessionLock:
    """Exclusive session claim on a thread (Phase 2 TeleOp)."""

    kind: str  # teleop
    tag_id: str
    lease_id: str = ""


# --- Routing tables (Phase 2) -------------------------------------------------

_ARM_ACTIONS = frozenset(
    {
        "MOVE_COMPONENT",
        "STORE_COMPONENT",
        "PLACE_FROM_STORAGE",
        "PICK_COMPONENT",
        "HOVER",
        "PLACE_FROM_HOVER",
        "CONFIRM_HOLDING_TAG",
        "AFFIRM_PLACED_AT_CURRENT",
        "REPACK_STORAGE",
        "RECENTER_IN_STORAGE",
        "REMOVE",
        "SET_LASER_OUTPUT",
        "APPLY_TUNABLES_PATCH",
        "RECORD_TUNABLES",
        "LOCALIZE_COMPONENTS",
        "SYNC_RUNTIME",
        "SCAN",
    }
)

_SENSE_ACTIONS = frozenset(
    {
        "START_LIVE_FEED",
        "END_LIVE_FEED",
        "SET_LIVE_EXPOSURE",
        "SET_EXPOSURE",
        "RECORD_MEASURABLES",
        "EVAL_KERNEL",
    }
)

_MOTOR_ACTIONS = frozenset(
    {
        "MOVE_MOTOR",
        "SET_MOTOR_SETPOINT",
        "MOTOR_SEND_HOME",
        "MOTOR_SET_ZERO",
    }
)

_TELEOP_ACTIONS = frozenset(
    {
        "START_TELEOP",
        "END_TELEOP",
        "TELEOP_JOG",
        "TELEOP_GOTO",
    }
)

_HOLDING_ALLOWED_ARM = frozenset(
    {
        "HOVER",
        "PLACE_FROM_HOVER",
        "CONFIRM_HOLDING_TAG",
    }
)


def parse_execution_threads(
    capabilities: dict | None,
    *,
    motor_ids: list[int] | None = None,
) -> list[ExecutionThreadSpec]:
    """Parse edge ``execution_threads`` or synthesize arm + sense fallback.

    Motor columns are **not** synthesized from bare ``motor_ids`` — inventory
    reuses local ``motor_id`` per tag. The coordinator creates
    ``motor.<tag_id>.<motor_id>`` threads on demand at enqueue.

    ``motor_ids`` is accepted for API compatibility and ignored.
    """
    del motor_ids  # unused — motors are tag-scoped and created lazily
    caps = capabilities if isinstance(capabilities, dict) else {}
    raw = caps.get("execution_threads")
    if isinstance(raw, list) and raw:
        out: list[ExecutionThreadSpec] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            tid = str(entry.get("id") or "").strip()
            kind = str(entry.get("kind") or "").strip()
            if not tid or not kind:
                continue
            mid = entry.get("motor_id")
            motor_id: Optional[int] = None
            if mid is not None:
                try:
                    motor_id = int(mid)
                except (TypeError, ValueError):
                    motor_id = None
            tag_raw = entry.get("tag_id")
            tag_id: Optional[str] = None
            if tag_raw is not None and str(tag_raw).strip():
                tag_id = str(tag_raw).strip()
            parsed = parse_motor_thread_id(tid)
            if kind == "motor" and parsed is not None:
                tag_id = tag_id or parsed[0]
                motor_id = motor_id if motor_id is not None else parsed[1]
            out.append(
                ExecutionThreadSpec(
                    id=tid, kind=kind, motor_id=motor_id, tag_id=tag_id
                )
            )
        if out:
            return out

    return [
        ExecutionThreadSpec(id="arm.0", kind="arm"),
        ExecutionThreadSpec(id="sense.0", kind="sense"),
    ]


def _first_arm_thread_id(threads: dict[str, ThreadState]) -> str:
    for tid, ts in threads.items():
        if ts.kind == "arm":
            return tid
    if "arm.0" in threads:
        return "arm.0"
    if threads:
        return next(iter(threads))
    return "arm.0"


def _first_sense_thread_id(threads: dict[str, ThreadState]) -> str:
    for tid, ts in threads.items():
        if ts.kind == "sense":
            return tid
    if "sense.0" in threads:
        return "sense.0"
    # No sense thread advertised — fall back to arm (safe serialize).
    return _first_arm_thread_id(threads)


def _legacy_global_motor_thread_id(
    motor_id: int, threads: dict[str, ThreadState]
) -> Optional[str]:
    """Match legacy advertised ``motor.<n>`` columns (no tag scope)."""
    want = f"motor.{int(motor_id)}"
    if want in threads:
        return want
    for tid, ts in threads.items():
        if (
            ts.kind == "motor"
            and ts.motor_id == int(motor_id)
            and not ts.tag_id
            and parse_motor_thread_id(tid) is None
        ):
            return tid
    return None


def route_primitive(
    action: str,
    parameters: dict | None,
    threads: dict[str, ThreadState],
    *,
    target_id: str | None = None,
) -> tuple[list[str], str]:
    """Return ``(resource_thread_ids, kind)`` for *action*.

    kind is ``normal`` | ``barrier`` | ``session_lock``.

    Motor primitives route to ``motor.<target_id>.<motor_id>`` (column may be
    created later by ``CommandMatrix.ensure_motor_thread``).
    """
    act = str(action or "").strip().upper()
    params = parameters if isinstance(parameters, dict) else {}
    arm_id = _first_arm_thread_id(threads)
    sense_id = _first_sense_thread_id(threads)
    tag = str(target_id).strip() if target_id is not None and str(target_id).strip() else None

    if act == "OPTIMIZE":
        return (list(threads.keys()), "barrier")

    if act in _TELEOP_ACTIONS:
        return ([arm_id], "session_lock")

    if act in _SENSE_ACTIONS:
        return ([sense_id], "normal")

    if act in _MOTOR_ACTIONS:
        mid_raw = params.get("motor_id")
        try:
            mid = int(mid_raw) if mid_raw is not None else None
        except (TypeError, ValueError):
            mid = None
        if mid is not None and tag:
            return ([make_motor_thread_id(tag, mid)], "normal")
        if mid is not None:
            legacy = _legacy_global_motor_thread_id(mid, threads)
            if legacy:
                return ([legacy], "normal")
        return ([arm_id], "normal")

    if act in _ARM_ACTIONS or act:
        return ([arm_id], "normal")

    return ([arm_id], "normal")


def command_matrix_enabled(backend_id: str | None = None) -> bool:
    """Feature flag: ``CLOUDLABS_COMMAND_MATRIX`` or mock/sim defaults."""
    raw = (os.environ.get("CLOUDLABS_COMMAND_MATRIX") or "").strip().lower()
    if raw in ("1", "true", "on", "yes"):
        return True
    if raw in ("0", "false", "off", "no"):
        return False
    # Env unset: mock/sim on; real.* off; no backend_id → True (dev-friendly)
    if backend_id is None:
        return True
    bid = str(backend_id).strip().lower()
    if bid.startswith("mock.") or bid.startswith("sim."):
        return True
    return False


def _held_tag_id(lab_state: dict | None) -> Optional[str]:
    if not isinstance(lab_state, dict):
        return None
    holding = lab_state.get("holding")
    if isinstance(holding, dict):
        tid = holding.get("tag_id")
        if tid is not None and str(tid).strip():
            return str(tid).strip()
    return None


def _is_holding(lab_state: dict | None) -> bool:
    if not isinstance(lab_state, dict):
        return False
    if str(lab_state.get("system_status") or "").upper() == "HOLDING":
        return True
    return _held_tag_id(lab_state) is not None


def _is_optimizing(lab_state: dict | None) -> bool:
    if not isinstance(lab_state, dict):
        return False
    return str(lab_state.get("system_status") or "").upper() == "OPTIMIZING"


def _resources_are_motor_only(resources: list[str], threads: dict[str, ThreadState]) -> bool:
    if not resources:
        return False
    for rid in resources:
        ts = threads.get(rid)
        if ts is None or ts.kind != "motor":
            return False
    return True


def _resources_are_sense_only(resources: list[str], threads: dict[str, ThreadState]) -> bool:
    if not resources:
        return False
    for rid in resources:
        ts = threads.get(rid)
        if ts is None or ts.kind != "sense":
            return False
    return True


class CommandMatrix:
    """In-process per-backend command matrix (queues + barrier + session locks)."""

    def __init__(
        self,
        backend_id: str,
        threads: dict[str, ThreadState],
        items: dict[str, QueuedCommand] | None = None,
    ) -> None:
        self.backend_id = backend_id
        self.threads = threads
        self.items: dict[str, QueuedCommand] = items if items is not None else {}
        self.session_locks: dict[str, SessionLock] = {}
        self._lock = threading.RLock()

    @classmethod
    def from_capabilities(
        cls,
        backend_id: str,
        capabilities: dict | None = None,
        motor_ids: list[int] | None = None,
    ) -> "CommandMatrix":
        specs = parse_execution_threads(capabilities, motor_ids=motor_ids)
        threads: dict[str, ThreadState] = {}
        for spec in specs:
            threads[spec.id] = ThreadState(
                thread_id=spec.id,
                kind=spec.kind,
                motor_id=spec.motor_id,
                tag_id=spec.tag_id,
            )
        if not threads:
            threads["arm.0"] = ThreadState(thread_id="arm.0", kind="arm")
        # Ensure a sense column exists even if edge forgot it (Phase 2).
        if not any(ts.kind == "sense" for ts in threads.values()):
            threads["sense.0"] = ThreadState(thread_id="sense.0", kind="sense")
        return cls(backend_id=backend_id, threads=threads)

    def ensure_motor_thread(self, tag_id: str, motor_id: int) -> str:
        """Create ``motor.<tag_id>.<motor_id>`` if missing; fence pending barriers."""
        with self._lock:
            return self._ensure_motor_thread_unlocked(tag_id, motor_id)

    def _ensure_motor_thread_unlocked(self, tag_id: str, motor_id: int) -> str:
        tid = make_motor_thread_id(tag_id, motor_id)
        if tid in self.threads:
            return tid
        self.threads[tid] = ThreadState(
            thread_id=tid,
            kind="motor",
            motor_id=int(motor_id),
            tag_id=str(tag_id).strip(),
        )
        # New columns must not sneak past an OPTIMIZE fence already in flight.
        for item in self.items.values():
            if item.kind != "barrier" or item.status not in ("queued", "running"):
                continue
            if tid not in item.resources:
                item.resources.append(tid)
            ts = self.threads[tid]
            if item.command_id in ts.queue:
                continue
            if item.status == "running":
                ts.queue.insert(0, item.command_id)
                ts.running_id = item.command_id
            else:
                ts.queue.append(item.command_id)
        return tid

    def is_idle(self) -> bool:
        with self._lock:
            if self.session_locks:
                return False
            for ts in self.threads.values():
                if ts.running_id is not None:
                    return False
                for cid in ts.queue:
                    item = self.items.get(cid)
                    if item is not None and item.status in ("queued", "running"):
                        return False
            return True

    def has_running_barrier(self) -> bool:
        with self._lock:
            for item in self.items.values():
                if item.kind == "barrier" and item.status == "running":
                    return True
            return False

    def item_status(self, command_id: str) -> Optional[str]:
        with self._lock:
            item = self.items.get(command_id)
            return item.status if item else None

    def snapshot(self) -> dict:
        with self._lock:
            thread_snaps = []
            for tid, ts in self.threads.items():
                queued = []
                for cid in ts.queue:
                    item = self.items.get(cid)
                    if item is None or item.status not in ("queued", "running"):
                        continue
                    blocked_on = self._blocked_on_unlocked(item) if item.status == "queued" else []
                    queued.append(
                        {
                            "command_id": item.command_id,
                            "action": item.action,
                            "target_id": item.target_id,
                            "kind": item.kind,
                            "status": item.status,
                            "predecessors": list(item.predecessors),
                            "blocked_on": blocked_on,
                        }
                    )
                running = None
                if ts.running_id and ts.running_id in self.items:
                    r = self.items[ts.running_id]
                    running = {
                        "command_id": r.command_id,
                        "action": r.action,
                        "target_id": r.target_id,
                        "kind": r.kind,
                        "status": r.status,
                        "predecessors": list(r.predecessors),
                    }
                lock = self.session_locks.get(tid)
                thread_snaps.append(
                    {
                        "id": tid,
                        "kind": ts.kind,
                        "motor_id": ts.motor_id,
                        "tag_id": ts.tag_id,
                        "queue": queued,
                        "running": running,
                        "session_lock": (
                            {
                                "kind": lock.kind,
                                "tag_id": lock.tag_id,
                                "lease_id": lock.lease_id,
                            }
                            if lock
                            else None
                        ),
                    }
                )
            return {
                "backend_id": self.backend_id,
                "threads": thread_snaps,
                "item_count": len(self.items),
                "idle": self._idle_unlocked(),
                "session_locks": {
                    tid: {"kind": lk.kind, "tag_id": lk.tag_id}
                    for tid, lk in self.session_locks.items()
                },
            }

    def _idle_unlocked(self) -> bool:
        if self.session_locks:
            return False
        for ts in self.threads.values():
            if ts.running_id is not None:
                return False
            for cid in ts.queue:
                item = self.items.get(cid)
                if item is not None and item.status in ("queued", "running"):
                    return False
        return True

    def enqueue(
        self,
        payload: dict,
        *,
        lease_id: str,
        lab_state: dict | None = None,
    ) -> dict:
        """Validate, apply mode locks, enqueue; return queued ack fragment."""
        if not isinstance(payload, dict):
            raise CommandMatrixRefuse("command payload must be an object")

        action = str(payload.get("action") or "").strip().upper()
        if not action:
            raise CommandMatrixRefuse("action is required")

        target_id = payload.get("target_id")
        target_s = str(target_id).strip() if target_id is not None and str(target_id).strip() else None
        parameters = payload.get("parameters")
        params = parameters if isinstance(parameters, dict) else {}

        with self._lock:
            if _is_optimizing(lab_state):
                raise CommandMatrixRefuse(
                    "lab is OPTIMIZING; refuse new command enqueue until the ensemble finishes"
                )
            if self.has_running_barrier():
                raise CommandMatrixRefuse(
                    "OPTIMIZE barrier is running; refuse new enqueue until it finishes"
                )

            resources, kind = route_primitive(
                action, params, self.threads, target_id=target_s
            )
            # Materialize tag-scoped motor columns before admission checks.
            if action in _MOTOR_ACTIONS and target_s:
                mid_raw = params.get("motor_id")
                try:
                    mid = int(mid_raw) if mid_raw is not None else None
                except (TypeError, ValueError):
                    mid = None
                if mid is not None:
                    mtid = self._ensure_motor_thread_unlocked(target_s, mid)
                    resources, kind = [mtid], "normal"
            for rid in resources:
                if rid not in self.threads:
                    raise CommandMatrixRefuse(f"unknown execution thread: {rid}")

            # TeleOp session locks (Phase 2)
            if kind == "barrier" and self.session_locks:
                locked = ", ".join(
                    f"{tid}={lk.kind}:{lk.tag_id}" for tid, lk in self.session_locks.items()
                )
                raise CommandMatrixRefuse(
                    f"cannot enqueue OPTIMIZE while session locks active ({locked})"
                )
            if action == "START_TELEOP":
                for rid in resources:
                    existing = self.session_locks.get(rid)
                    if existing is not None:
                        raise CommandMatrixRefuse(
                            f"thread {rid} already has {existing.kind} session "
                            f"on {existing.tag_id}; END_TELEOP first"
                        )
            # While TeleOp owns a thread, refuse new normal arm work on that thread
            # (END_TELEOP for the locked tag is allowed; motors/sense unaffected).
            if kind == "normal":
                for rid in resources:
                    existing = self.session_locks.get(rid)
                    if existing is not None and existing.kind == "teleop":
                        raise CommandMatrixRefuse(
                            f"thread {rid} held by TELEOP on {existing.tag_id}; "
                            f"END_TELEOP before {action}"
                        )
            if action == "END_TELEOP":
                for rid in resources:
                    existing = self.session_locks.get(rid)
                    if existing is None:
                        break
                    if target_s and existing.tag_id != target_s:
                        raise CommandMatrixRefuse(
                            f"END_TELEOP target {target_s} does not match "
                            f"session lock on {existing.tag_id}"
                        )

            # HOLDING mode lock (arm only; motors + sense stay open)
            arm_locked_context = (
                _is_holding(lab_state)
                and not _resources_are_motor_only(resources, self.threads)
                and not _resources_are_sense_only(resources, self.threads)
            )
            if arm_locked_context:
                held = _held_tag_id(lab_state)
                if kind == "session_lock":
                    if held and target_s and target_s != held:
                        raise CommandMatrixRefuse(
                            f"HOLDING {held}: TELEOP only allowed for the held tag"
                        )
                    if held and not target_s:
                        raise CommandMatrixRefuse(
                            f"HOLDING {held}: TELEOP requires target_id matching the held tag"
                        )
                elif kind == "barrier":
                    raise CommandMatrixRefuse(
                        f"HOLDING {held or 'part'}: cannot enqueue OPTIMIZE barrier while holding"
                    )
                else:
                    if action not in _HOLDING_ALLOWED_ARM:
                        raise CommandMatrixRefuse(
                            f"HOLDING {held or 'part'}: only HOVER / PLACE_FROM_HOVER / "
                            f"CONFIRM_HOLDING_TAG (same tag) or TELEOP allowed on arm; "
                            f"refused {action}"
                        )
                    if held and target_s and target_s != held:
                        raise CommandMatrixRefuse(
                            f"HOLDING {held}: {action} must target the held tag"
                        )
                    if held and not target_s:
                        raise CommandMatrixRefuse(
                            f"HOLDING {held}: {action} requires target_id matching the held tag"
                        )

            command_id = str(payload.get("command_id") or "").strip() or f"cmd_{uuid.uuid4().hex[:12]}"
            if command_id in self.items:
                raise CommandMatrixRefuse(f"duplicate command_id: {command_id}")

            predecessors = self._parse_predecessors_unlocked(payload, command_id)

            item = QueuedCommand(
                command_id=command_id,
                action=action,
                target_id=target_s,
                payload=dict(payload),
                resources=list(resources),
                kind=kind,
                status="queued",
                lease_id=str(lease_id or ""),
                predecessors=predecessors,
            )
            self.items[command_id] = item

            # Append to every resource thread queue (barrier = all threads)
            for rid in resources:
                self.threads[rid].queue.append(command_id)

            return {
                "status": "queued",
                "command_id": command_id,
                "action": action,
                "resources": list(resources),
                "kind": kind,
                "predecessors": list(predecessors),
                "message": f"queued on {', '.join(resources)}",
            }

    def enqueue_batch(
        self,
        envelopes: list,
        *,
        lease_id: str,
        lab_state: dict | None = None,
    ) -> dict:
        """Enqueue a declared batch, remapping ``plan_step_id`` predecessors.

        Each envelope may carry ``plan_step_id`` / ``predecessors`` referring to
        other steps in this batch (from ``plan_batch``). Those local ids are
        rewritten to concrete ``command_id`` values before admission. Enqueue
        order must respect the DAG (planner topo order is sufficient).
        """
        from lab_model.coordinator.state.batch_plan import strip_plan_meta

        if not isinstance(envelopes, list) or not envelopes:
            raise CommandMatrixRefuse("commands must be a non-empty list")

        prepared: list[tuple[str, dict]] = []
        step_map: dict[str, str] = {}
        for index, raw in enumerate(envelopes):
            if not isinstance(raw, dict):
                raise CommandMatrixRefuse(f"commands[{index}] must be an object")
            env = dict(raw)
            plan_step_id = str(env.get("plan_step_id") or "").strip()
            command_id = str(env.get("command_id") or "").strip() or f"cmd_{uuid.uuid4().hex[:12]}"
            if plan_step_id:
                if plan_step_id in step_map:
                    raise CommandMatrixRefuse(
                        f"duplicate plan_step_id {plan_step_id!r} in batch"
                    )
                step_map[plan_step_id] = command_id
            prepared.append((command_id, env))

        # Refuse unknown local preds before any enqueue mutates the matrix.
        known_steps = set(step_map.keys())
        for command_id, env in prepared:
            for entry in env.get("predecessors") or []:
                pid = str(entry or "").strip()
                if not pid:
                    continue
                if pid in known_steps or pid in step_map.values():
                    continue
                # May already exist on the matrix — checked at enqueue time.
                continue

        acks: list[dict] = []
        command_ids: list[str] = []
        for command_id, env in prepared:
            remapped: list[str] = []
            seen: set[str] = set()
            for entry in env.get("predecessors") or []:
                pid = str(entry or "").strip()
                if not pid:
                    continue
                resolved = step_map.get(pid, pid)
                if resolved in seen:
                    continue
                seen.add(resolved)
                remapped.append(resolved)
            payload = strip_plan_meta(env)
            payload["command_id"] = command_id
            payload["predecessors"] = remapped
            ack = self.enqueue(payload, lease_id=lease_id, lab_state=lab_state)
            acks.append(ack)
            command_ids.append(str(ack.get("command_id") or command_id))

        return {
            "status": "queued",
            "command_ids": command_ids,
            "acks": acks,
            "steps": len(command_ids),
        }

    def cancel_queued(self, command_id: str) -> bool:
        """Cancel a queued (not running) command. Returns True if cancelled."""
        with self._lock:
            return self._cancel_queued_unlocked(command_id)

    def cancel_all_queued(self) -> list[str]:
        """Cancel every queued command. Returns cancelled command_ids."""
        with self._lock:
            ids = [
                cid
                for cid, item in self.items.items()
                if item.status == "queued"
            ]
            cancelled: list[str] = []
            for cid in ids:
                if self._cancel_queued_unlocked(cid):
                    cancelled.append(cid)
            return cancelled

    def _cancel_queued_unlocked(self, command_id: str) -> bool:
        item = self.items.get(command_id)
        if item is None or item.status != "queued":
            return False
        item.status = "cancelled"
        for rid in item.resources:
            ts = self.threads.get(rid)
            if ts is None:
                continue
            try:
                ts.queue.remove(command_id)
            except ValueError:
                pass
        return True

    def statuses(self, command_ids: list[str]) -> dict[str, dict]:
        """Return status snapshots for the given command ids."""
        with self._lock:
            out: dict[str, dict] = {}
            for cid in command_ids:
                item = self.items.get(str(cid))
                if item is None:
                    out[str(cid)] = {"status": "unknown"}
                    continue
                out[str(cid)] = {
                    "status": item.status,
                    "action": item.action,
                    "target_id": item.target_id,
                    "kind": item.kind,
                    "error": item.error,
                    "resources": list(item.resources),
                    "predecessors": list(item.predecessors),
                    "blocked_on": (
                        self._blocked_on_unlocked(item) if item.status == "queued" else []
                    ),
                }
            return out

    def clear_on_lease_release(self) -> dict:
        """Safe-stop on lease release (Phase 3).

        - Cancel all **queued** commands.
        - Clear TeleOp session locks.
        - Mark **running** items failed with ``lease_released`` (hardware may
          still finish the in-flight southbound call; no new work drains).
        """
        with self._lock:
            cancelled = [
                cid
                for cid, item in self.items.items()
                if item.status == "queued"
            ]
            for cid in cancelled:
                self._cancel_queued_unlocked(cid)

            orphaned: list[str] = []
            for cid, item in list(self.items.items()):
                if item.status != "running":
                    continue
                item.status = "failed"
                item.error = "lease_released"
                orphaned.append(cid)
                for rid in item.resources:
                    ts = self.threads.get(rid)
                    if ts is None:
                        continue
                    if ts.running_id == cid:
                        ts.running_id = None
                    try:
                        ts.queue.remove(cid)
                    except ValueError:
                        pass

            locks_cleared = list(self.session_locks.keys())
            self.session_locks.clear()
            return {
                "cancelled_queued": cancelled,
                "orphaned_running": orphaned,
                "session_locks_cleared": locks_cleared,
            }

    def _parse_predecessors_unlocked(
        self, payload: dict, command_id: str
    ) -> list[str]:
        raw = payload.get("predecessors")
        if raw is None:
            return []
        if not isinstance(raw, list):
            raise CommandMatrixRefuse("predecessors must be a list of command_id strings")
        out: list[str] = []
        seen: set[str] = set()
        for entry in raw:
            pid = str(entry or "").strip()
            if not pid:
                continue
            if pid == command_id:
                raise CommandMatrixRefuse("command cannot list itself as a predecessor")
            if pid in seen:
                continue
            seen.add(pid)
            if pid not in self.items:
                raise CommandMatrixRefuse(
                    f"unknown predecessor {pid!r}; enqueue dependencies first"
                )
            out.append(pid)
        return out

    def _blocked_on_unlocked(self, item: QueuedCommand) -> list[str]:
        """Predecessor ids that are not yet ``done`` (queued/running only)."""
        pending: list[str] = []
        for pid in item.predecessors:
            pred = self.items.get(pid)
            if pred is None:
                pending.append(pid)
                continue
            if pred.status == "done":
                continue
            if pred.status in ("failed", "cancelled"):
                continue
            pending.append(pid)
        return pending

    def _predecessor_gate_unlocked(
        self, item: QueuedCommand
    ) -> tuple[bool, Optional[str]]:
        """Return ``(ok_to_run, fatal_error)``.

        Fatal when a predecessor is missing / failed / cancelled — caller should
        fail the dependent so the thread queue does not stick forever.
        """
        for pid in item.predecessors:
            pred = self.items.get(pid)
            if pred is None:
                return False, f"missing predecessor {pid}"
            if pred.status == "done":
                continue
            if pred.status in ("failed", "cancelled"):
                return False, f"predecessor {pid} {pred.status}"
            # queued or running — head stays blocked
            return False, None
        return True, None

    def _fail_queued_unlocked(self, item: QueuedCommand, error: str) -> None:
        item.status = "failed"
        item.error = error
        for rid in item.resources:
            ts = self.threads.get(rid)
            if ts is None:
                continue
            try:
                ts.queue.remove(item.command_id)
            except ValueError:
                pass

    def _barrier_runnable(self, item: QueuedCommand) -> bool:
        """Barrier may run only when head on every resource thread, idle, no session locks."""
        if self.session_locks:
            return False
        for rid in item.resources:
            ts = self.threads.get(rid)
            if ts is None:
                return False
            if ts.running_id is not None:
                return False
            if not ts.queue or ts.queue[0] != item.command_id:
                return False
        for ts in self.threads.values():
            if ts.running_id is not None:
                return False
        return True

    def pop_runnable(self, thread_id: str) -> Optional[QueuedCommand]:
        """Return head command for *thread_id* if it is legal to start now (does not claim)."""
        with self._lock:
            return self._peek_runnable_locked(thread_id)

    def claim_runnable(self, thread_id: str) -> Optional[QueuedCommand]:
        """Atomically claim the next runnable command for *thread_id* (marks running).

        For multi-resource / barrier items, only the lexicographically first
        resource thread may claim — prevents double southbound execute.
        """
        with self._lock:
            item = self._peek_runnable_locked(thread_id)
            if item is None:
                return None
            primary = sorted(item.resources)[0] if item.resources else thread_id
            if primary != thread_id:
                return None
            item.status = "running"
            for rid in item.resources:
                ts = self.threads.get(rid)
                if ts is not None:
                    ts.running_id = item.command_id
            self._apply_session_lock_on_start(item)
            return item

    def _peek_runnable_locked(self, thread_id: str) -> Optional[QueuedCommand]:
        ts = self.threads.get(thread_id)
        if ts is None:
            return None
        if ts.running_id is not None:
            return None
        if not ts.queue:
            return None
        head_id = ts.queue[0]
        item = self.items.get(head_id)
        if item is None or item.status != "queued":
            ts.queue.pop(0)
            return None

        # TeleOp session lock: only END_TELEOP for the locked tag may claim this thread.
        lock = self.session_locks.get(thread_id)
        if lock is not None and lock.kind == "teleop":
            if item.action != "END_TELEOP":
                return None
            if item.target_id and item.target_id != lock.tag_id:
                return None

        preds_ok, fatal = self._predecessor_gate_unlocked(item)
        if fatal:
            self._fail_queued_unlocked(item, fatal)
            return None
        if not preds_ok:
            # Head stays queued but blocked on predecessors (other threads may still drain).
            return None

        if item.kind == "barrier":
            if not self._barrier_runnable(item):
                return None
            return item
        return item

    def _apply_session_lock_on_start(self, item: QueuedCommand) -> None:
        if item.action == "START_TELEOP" and item.target_id:
            for rid in item.resources:
                self.session_locks[rid] = SessionLock(
                    kind="teleop",
                    tag_id=item.target_id,
                    lease_id=item.lease_id,
                )

    def _clear_session_locks_for_item(self, item: QueuedCommand) -> None:
        for rid in item.resources:
            self.session_locks.pop(rid, None)

    def mark_running(self, command_id: str) -> None:
        with self._lock:
            item = self.items.get(command_id)
            if item is None:
                return
            item.status = "running"
            for rid in item.resources:
                ts = self.threads.get(rid)
                if ts is not None:
                    ts.running_id = command_id
            self._apply_session_lock_on_start(item)

    def mark_done(self, command_id: str, error: Optional[str] = None) -> None:
        with self._lock:
            item = self.items.get(command_id)
            if item is None:
                return
            item.status = "failed" if error else "done"
            item.error = error
            for rid in item.resources:
                ts = self.threads.get(rid)
                if ts is None:
                    continue
                if ts.running_id == command_id:
                    ts.running_id = None
                try:
                    ts.queue.remove(command_id)
                except ValueError:
                    pass
            if item.action == "END_TELEOP":
                self._clear_session_locks_for_item(item)
            elif item.action == "START_TELEOP" and error:
                self._clear_session_locks_for_item(item)
