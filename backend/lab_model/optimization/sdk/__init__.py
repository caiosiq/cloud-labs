"""Imperative Python SDK for cloud-labs (Phase B)."""

from lab_model.measurables.tensor import LazyRef, MeasurableTensor

from .client import (
    CloudLabsClient,
    KernelMatchSpec,
    SessionLease,
    VariableSpec,
    configure_logging,
    connect,
    list_backends,
    resolve_backend_id,
)
from .exceptions import (
    CloudLabsCommandError,
    CloudLabsConnectionError,
    CloudLabsError,
    CloudLabsLeaseError,
    CloudLabsPathError,
    CloudLabsReconcileError,
    CloudLabsTimeoutError,
)
from .reconcile import LoadedSnapshot, ReconcileResult, ReconcileStep
from .measurable import MeasurableHandle
from .jobs import (
    cancel_job,
    get_job,
    list_jobs,
    submit_closed_loop_optimize,
    submit_compiled_dag,
    submit_job,
    wait_for_job,
)
from .objective import (
    ObjectiveGraphBuilder,
    compile_objective,
    objective_term,
    preflight_compiled_objective,
)
from .wiki import describe_component_row, measurable_script_handle, tunable_script_handles

__all__ = [
    "CloudLabsClient",
    "CloudLabsCommandError",
    "CloudLabsConnectionError",
    "CloudLabsError",
    "CloudLabsLeaseError",
    "CloudLabsPathError",
    "CloudLabsReconcileError",
    "CloudLabsTimeoutError",
    "KernelMatchSpec",
    "MeasurableHandle",
    "MeasurableTensor",
    "ObjectiveGraphBuilder",
    "LazyRef",
    "ReconcileResult",
    "ReconcileStep",
    "SessionLease",
    "VariableSpec",
    "compile_objective",
    "configure_logging",
    "connect",
    "cancel_job",
    "describe_component_row",
    "measurable_script_handle",
    "objective_term",
    "preflight_compiled_objective",
    "list_backends",
    "resolve_backend_id",
    "get_job",
    "list_jobs",
    "submit_closed_loop_optimize",
    "submit_compiled_dag",
    "submit_job",
    "tunable_script_handles",
    "wait_for_job",
]

# Intent helpers (prepare / run_optimize / run_cobyla / register_kernel / …)
# live on CloudLabsClient via connect(); export_session_kernel_packages is a
# public client method for advanced job handoff.
