"""Edge kernel registry (Phase F — builtins + approved TorchScript)."""

from .registry import (
    KernelDescriptor,
    apply_kernel_hooks,
    ensure_torchscript_ready,
    get_kernel,
    list_kernels,
    validate_kernel_ids,
)

__all__ = [
    "KernelDescriptor",
    "apply_kernel_hooks",
    "ensure_torchscript_ready",
    "get_kernel",
    "list_kernels",
    "validate_kernel_ids",
]
