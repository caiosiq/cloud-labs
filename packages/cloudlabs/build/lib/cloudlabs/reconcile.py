"""Monitored hardware reconcile — step-by-step primitive execution with telemetry."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Mapping, Optional, Sequence

SnapshotSource = Literal["local", "catalog"]


@dataclass(frozen=True)
class LoadedSnapshot:
    """Configuration selected by :meth:`CloudLabsClient.load_snapshot`.

    ``source`` distinguishes a live local branch head from a frozen catalog pin.
    Both resolve to the same apply path (``configuration_id``).
    """

    repo_id: str
    branch: str
    configuration_id: str
    source: SnapshotSource = "local"
    pin_id: Optional[str] = None
    display_name: Optional[str] = None

    def label(self) -> str:
        if self.source == "catalog":
            name = self.display_name or self.pin_id or self.configuration_id
            return f"Catalog · {name}"
        return f"Local · {self.repo_id} @{self.branch}"

    def to_job_snapshot(self) -> Dict[str, Any]:
        """Job / script envelope with an explicit ``source`` field."""
        if self.source == "catalog":
            out: Dict[str, Any] = {
                "source": "catalog",
                "pin_id": self.pin_id,
                "repo_id": self.repo_id,
                "branch": self.branch,
                "commit": self.configuration_id,
            }
            if self.display_name:
                out["display_name"] = self.display_name
            return out
        return {
            "source": "local",
            "repo_id": self.repo_id,
            "branch": self.branch,
            "commit": self.configuration_id,
        }


@dataclass
class ReconcileStep:
    """One primitive in a reconcile plan."""

    index: int
    action: str
    target_id: Optional[str]
    parameters: Dict[str, Any]
    status: str = "pending"  # pending | active | done | error

    @property
    def label(self) -> str:
        if self.target_id:
            return f"{self.action} → {self.target_id}"
        return self.action

    def to_envelope(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "target_id": self.target_id,
            "parameters": dict(self.parameters),
        }


@dataclass
class ReconcileResult:
    """Outcome of :meth:`CloudLabsClient.reconcile_hardware`."""

    repo_id: str
    configuration_id: str
    branch: str
    steps_total: int
    steps_executed: int
    steps: List[ReconcileStep] = field(default_factory=list)
    finalized: bool = False
    message: str = ""

    def succeeded(self) -> bool:
        return self.finalized and self.steps_executed == self.steps_total


ProgressCallback = Callable[[str, ReconcileStep, int, int], None]


def build_reconcile_steps(plan: Sequence[Mapping[str, Any]]) -> List[ReconcileStep]:
    """Normalize a checkout reconcile plan into tracked steps."""
    steps: List[ReconcileStep] = []
    for index, envelope in enumerate(plan):
        if not isinstance(envelope, dict):
            continue
        action = str(envelope.get("action") or "")
        target = envelope.get("target_id")
        params = envelope.get("parameters")
        steps.append(
            ReconcileStep(
                index=index,
                action=action,
                target_id=str(target) if target is not None else None,
                parameters=dict(params) if isinstance(params, dict) else {},
            )
        )
    return steps


def default_progress_logger(message: str, step: ReconcileStep, done: int, total: int) -> None:
    import logging

    logging.getLogger("cloudlabs").info(
        "%s [%d/%d] %s",
        message,
        done,
        total,
        step.label if step else "-",
    )
