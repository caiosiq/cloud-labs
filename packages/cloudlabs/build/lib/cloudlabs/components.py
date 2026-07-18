"""Fluent component proxies: ``lab.components.tag_20.move(x=12.5)``.

Thin ergonomics over existing :class:`~cloudlabs.client.CloudLabsClient`
imperative methods — no new HTTP primitives.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Union

if TYPE_CHECKING:
    from .client import CloudLabsClient, KernelMatchSpec, VariableSpec
    from .intent import Bounds
    from .measurable import MeasurableHandle


@dataclass
class ComponentProxy:
    """Bound to one ``tag_id``; methods delegate to the parent client."""

    client: "CloudLabsClient"
    tag_id: str

    def move(
        self,
        *,
        x: Optional[float] = None,
        y: Optional[float] = None,
        rotation: Optional[float] = None,
    ) -> "ComponentProxy":
        """Update one or more ``tunables.nominal_pose`` axes (each axis = one command)."""
        updates = (("x", x), ("y", y), ("rotation", rotation))
        if all(v is None for _, v in updates):
            raise ValueError("move() requires at least one of x, y, rotation")
        for axis, value in updates:
            if value is None:
                continue
            self.client.move_component(
                self.tag_id,
                f"tunables.nominal_pose.{axis}",
                float(value),
            )
        return self

    def motor(self, motor_id: Union[int, str], angle_deg: float) -> "ComponentProxy":
        """Set a motor setpoint (``tunables.nominal_motor_positions.<id>``)."""
        mid = str(motor_id).strip()
        self.client.move_component(
            self.tag_id,
            f"tunables.nominal_motor_positions.{mid}",
            float(angle_deg),
        )
        return self

    def set(self, path: str, value: float) -> "ComponentProxy":
        """Pass-through to :meth:`CloudLabsClient.set_tunable`."""
        self.client.set_tunable(self.tag_id, path, value)
        return self

    def capture(self, field: str = "camera_image") -> Any:
        """``RECORD_MEASURABLES`` and return the named field."""
        return self.client.capture_measurable(self.tag_id, field)

    def measurable(self, field: str) -> "MeasurableHandle":
        return self.client.measurable(self.tag_id, field)

    def refresh_pose(self, *, include_measurables: bool = True) -> Dict[str, Any]:
        return self.client.refresh_pose(
            self.tag_id,
            include_measurables=include_measurables,
        )

    def describe(self, *, refresh: bool = False) -> Dict[str, Any]:
        return self.client.describe_component(self.tag_id, refresh=refresh)

    def variable(
        self,
        path: str,
        *,
        bounds: "Bounds",
        variable_id: Optional[str] = None,
        unit: str = "deg",
        delta: bool = False,
    ) -> "VariableSpec":
        return self.client.variable(
            self.tag_id,
            path,
            bounds=bounds,
            variable_id=variable_id,
            unit=unit,
            delta=delta,
        )

    def kernel_match(
        self,
        field: str,
        *,
        kernel_id: str,
        target: Any,
        term_id: str = "match_m0",
        weight: float = 1.0,
        metric: Optional[str] = None,
        feature_index: Optional[Any] = None,
    ) -> "KernelMatchSpec":
        return self.client.kernel_match(
            self.tag_id,
            field,
            kernel_id=kernel_id,
            target=target,
            term_id=term_id,
            weight=weight,
            metric=metric,
            feature_index=feature_index,
        )

    def eval_kernel(self, field: str, *, kernel_id: str) -> Union[float, List[float]]:
        return self.client.eval_kernel(self.tag_id, field, kernel_id=kernel_id)

    def wait_until_idle(
        self,
        *,
        poll_interval_s: float = 0.25,
        timeout_s: float = 120.0,
    ) -> None:
        """Lab-wide idle wait (same as ``lab.wait_until_idle``); convenient after move."""
        self.client.wait_until_idle(
            poll_interval_s=poll_interval_s,
            timeout_s=timeout_s,
        )


class ComponentsNamespace:
    """``lab.components.tag_20`` / ``lab.components[\"tag_20\"]`` factory."""

    def __init__(self, client: "CloudLabsClient") -> None:
        self._client = client

    def __getitem__(self, tag_id: str) -> ComponentProxy:
        tid = str(tag_id).strip()
        if not tid:
            raise KeyError("tag_id must be a non-empty string")
        return ComponentProxy(self._client, tid)

    def __getattr__(self, tag_id: str) -> ComponentProxy:
        if tag_id.startswith("_"):
            raise AttributeError(tag_id)
        return self[tag_id]

    def __contains__(self, tag_id: object) -> bool:
        if not isinstance(tag_id, str):
            return False
        return any(r.get("tag_id") == tag_id for r in self.list())

    def list(self, *, refresh: bool = False) -> List[Dict[str, Any]]:
        """Catalog/active-tag rows from :meth:`CloudLabsClient.list_components`."""
        return self._client.list_components(refresh=refresh)

    def keys(self, *, refresh: bool = False) -> List[str]:
        return [str(r.get("tag_id")) for r in self.list(refresh=refresh) if r.get("tag_id")]


__all__ = ["ComponentProxy", "ComponentsNamespace"]
