"""Typed errors for the CloudLabs imperative Python SDK."""
from __future__ import annotations

from typing import Any, Dict, Optional


class CloudLabsError(Exception):
    """Base SDK error."""


class CloudLabsConnectionError(CloudLabsError):
    """HTTP transport or server unreachable."""


class CloudLabsLeaseError(CloudLabsError):
    """Lease acquire, release, or validation failed."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        detail: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


class CloudLabsCommandError(CloudLabsError):
    """Primitive dispatch refused or failed."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        detail: Any = None,
        action: Optional[str] = None,
        target_id: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail
        self.action = action
        self.target_id = target_id


class CloudLabsPathError(CloudLabsError):
    """Tunable or measurable path could not be resolved."""

    def __init__(self, path: str, tag_id: str, reason: str) -> None:
        self.path = path
        self.tag_id = tag_id
        self.reason = reason
        super().__init__(f"{tag_id}: {path} — {reason}")


class CloudLabsTimeoutError(CloudLabsError):
    """``wait_until_idle`` or lease heartbeat exceeded a deadline."""


class CloudLabsReconcileError(CloudLabsError):
    """Snapshot load or hardware reconcile failed."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        detail: Any = None,
        step_index: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail
        self.step_index = step_index
