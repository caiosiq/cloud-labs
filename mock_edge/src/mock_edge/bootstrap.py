"""Bootstrap mock edge host against mock_edge/lab_view."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional, Tuple

_PKG_ROOT = Path(__file__).resolve().parents[2]  # mock_edge/
_REPO_ROOT = _PKG_ROOT.parent  # cloud-labs/
_DEFAULT_LAB_VIEW = _PKG_ROOT / "lab_view"


def default_lab_view_path() -> Path:
    env = (os.environ.get("MOCK_EDGE_LAB_VIEW") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return _DEFAULT_LAB_VIEW.resolve()


def bootstrap_host(*, lab_view: Optional[Path] = None) -> Tuple[Any, Any]:
    """Return ``(lab_proxy, runtime_manager)`` with lab_view context bound."""
    from lab_model.coordinator.backends.context import backend_context
    from lab_model.coordinator.backends.lab_view_config import load_lab_view_bundle
    from lab_model.language.domain import motor_rotation_store as motor_rot
    from mock_edge.host.communicator import MockLabCommunicator
    from mock_edge.host.runtime_mode import RuntimeLabProxy

    root = (lab_view or default_lab_view_path()).resolve()
    os.environ["LAB_VIEW_PATH"] = str(root)
    # load_lab_view_bundle(project_root, relative_or_abs)
    paths, manifest = load_lab_view_bundle(str(_REPO_ROOT), str(root))
    with backend_context(paths, manifest):
        motor_rot.configure(paths.motor_rotations_json)
        lab = MockLabCommunicator()
        runtime_manager = RuntimeLabProxy(lab)
        return runtime_manager, runtime_manager
