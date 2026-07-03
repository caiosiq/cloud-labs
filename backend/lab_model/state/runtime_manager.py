"""Single-writer gate for live runtime JSON."""

from __future__ import annotations

import copy
import json
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Deque, Dict, List, Mapping, Optional

from lab_model.domain.holding import SYSTEM_STATUS_IDLE, empty_holding

from .projections import (
    apply_configuration_metadata,
    apply_configuration_to_components,
    clear_observations_in_runtime,
    extract_configuration,
    extract_observations,
)


class MutationKind(str, Enum):
    PRIMITIVE_COMMIT = "primitive_commit"
    OBSERVATION_COMMIT = "observation_commit"
    PROCESS_TRANSITION = "process_transition"
    TELEMETRY_COMMIT = "telemetry_commit"
    ADMINISTRATIVE_LOAD = "administrative_load"
    RECOVERY_PATCH = "recovery_patch"
    BOOT_HYDRATE = "boot_hydrate"
    PROJECTION_APPLY = "projection_apply"


@dataclass(frozen=True)
class RuntimeMutationRecord:
    kind: MutationKind
    source: str
    at: str


def default_runtime_state() -> Dict[str, Any]:
    return {
        "system_status": SYSTEM_STATUS_IDLE,
        "last_updated": datetime.now().isoformat(),
        "components": {},
        "optimization_step": 0,
        "optimization_run_dir": None,
        "optimization_target_id": None,
        "holding": empty_holding(),
    }


class RuntimeManager:
    """Owns the runtime dict and serializes all writes through typed mutations."""

    def __init__(
        self,
        initial_state: Optional[Dict[str, Any]] = None,
        *,
        mutation_log_limit: int = 200,
    ) -> None:
        self._lock = threading.RLock()
        self._state = copy.deepcopy(initial_state or default_runtime_state())
        self._mutation_log: Deque[RuntimeMutationRecord] = deque(maxlen=mutation_log_limit)

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    @property
    def state(self) -> Dict[str, Any]:
        return self._state

    def snapshot_raw(self) -> Dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._state)

    def mutation_log(self) -> List[RuntimeMutationRecord]:
        with self._lock:
            return list(self._mutation_log)

    def record_mutation(self, kind: MutationKind, source: str = "") -> None:
        self._mutation_log.append(
            RuntimeMutationRecord(
                kind=kind,
                source=source,
                at=datetime.now().isoformat(),
            )
        )

    def replace_state(
        self,
        new_state: Dict[str, Any],
        *,
        kind: MutationKind,
        source: str = "",
    ) -> None:
        with self._lock:
            self._state = copy.deepcopy(new_state)
            self._state["last_updated"] = datetime.now().isoformat()
        self.record_mutation(kind, source)

    def mutate(
        self,
        fn: Callable[[Dict[str, Any]], None],
        *,
        kind: MutationKind,
        source: str = "",
        bump_timestamp: bool = True,
    ) -> None:
        with self._lock:
            fn(self._state)
            if bump_timestamp:
                self._state["last_updated"] = datetime.now().isoformat()
        self.record_mutation(kind, source)

    def extract_configuration(self) -> Dict[str, Any]:
        with self._lock:
            return extract_configuration(self._state)

    def extract_observations(self) -> Dict[str, Any]:
        with self._lock:
            return extract_observations(self._state)

    def apply_configuration_projection(
        self,
        configuration: Dict[str, Any],
        *,
        source: str = "soft_checkout",
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        def _apply(state: Dict[str, Any]) -> None:
            apply_configuration_to_components(state, configuration)
            apply_configuration_metadata(state, metadata)

        self.mutate(_apply, kind=MutationKind.PROJECTION_APPLY, source=source)

    def apply_hard_checkout_projection(
        self,
        configuration: Dict[str, Any],
        *,
        source: str = "hard_checkout",
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Apply target configuration tunables and clear observations (hard checkout)."""

        def _apply(state: Dict[str, Any]) -> None:
            apply_configuration_to_components(state, configuration)
            clear_observations_in_runtime(state)
            apply_configuration_metadata(state, metadata)

        self.mutate(_apply, kind=MutationKind.PROJECTION_APPLY, source=source)

    def configuration_json(self) -> str:
        return json.dumps(self.extract_configuration(), sort_keys=True, default=str)
