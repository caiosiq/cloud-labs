"""Capture source stub — Phase 2 wires latched in-process reads."""
from __future__ import annotations
from cloudlabs_edge_dev.optimization import EdgeTensor

class EdgeCaptureSource:
    def capture(self, capture_id: str) -> EdgeTensor:
        raise NotImplementedError(f"Phase 2: capture({capture_id!r})")
    def read_measurable(self, path: str, *, tag_id: str) -> float:
        raise NotImplementedError(f"Phase 2: read_measurable({path!r})")
