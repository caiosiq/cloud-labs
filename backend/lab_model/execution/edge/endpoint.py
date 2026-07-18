"""Configured Edge Contract endpoint for a backend (Phase 3)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class EdgeEndpointConfig:
    """Optional southbound Edge Contract HTTP endpoint from ``backends.json``.

    When ``base_url`` is set, the coordinator prefers ``HttpEdgeClient`` unless a
    Step-B poll agent is already attached for that backend.
    """

    base_url: Optional[str] = None
    contract_version: str = "1.0.0"
    lan_direct_ok: bool = False

    @property
    def configured(self) -> bool:
        return bool((self.base_url or "").strip())

    def normalized_base_url(self) -> Optional[str]:
        raw = (self.base_url or "").strip().rstrip("/")
        return raw or None

    def to_api_dict(self) -> Dict[str, Any]:
        return {
            "base_url": self.normalized_base_url(),
            "contract_version": self.contract_version,
            "lan_direct_ok": bool(self.lan_direct_ok),
            "configured": self.configured,
        }


def parse_edge_endpoint(raw: Any) -> EdgeEndpointConfig:
    if not isinstance(raw, dict):
        return EdgeEndpointConfig()
    base = raw.get("base_url")
    cv = str(raw.get("contract_version") or "1.0.0").strip() or "1.0.0"
    return EdgeEndpointConfig(
        base_url=str(base).strip() if base else None,
        contract_version=cv,
        lan_direct_ok=bool(raw.get("lan_direct_ok", False)),
    )


__all__ = ["EdgeEndpointConfig", "parse_edge_endpoint"]
