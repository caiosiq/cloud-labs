"""Shim — use ``cloudlabs.client``."""
from cloudlabs.client import (  # noqa: F401
    CloudLabsClient,
    KernelMatchSpec,
    SessionLease,
    VariableSpec,
    configure_logging,
    connect,
    list_backends,
    resolve_backend_id,
)
