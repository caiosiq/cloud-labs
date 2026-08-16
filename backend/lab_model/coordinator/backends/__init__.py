"""Multi-backend registry — user-selected communicators per session."""

from .context import backend_context, bind_backend_context, reset_backend_context
from .registry import BackendRegistry, BackendRuntime, BackendSpec

__all__ = [
    "BackendRegistry",
    "BackendRuntime",
    "BackendSpec",
    "JobManagerHub",
    "backend_context",
    "bind_backend_context",
    "reset_backend_context",
]


def __getattr__(name: str):
    # Lazy: avoid circular import jobs → state → backends → job_hub → jobs
    if name == "JobManagerHub":
        from .job_hub import JobManagerHub

        return JobManagerHub
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
