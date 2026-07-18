"""Cloud Labs imperative Python SDK.

Install::

    pip install -e ./packages/cloudlabs

Then::

    from cloudlabs import connect, resolve_backend_id
"""

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
from .jobs import (
    cancel_job,
    get_job,
    list_jobs,
    submit_closed_loop_optimize,
    submit_compiled_dag,
    submit_job,
    wait_for_job,
)
from .measurable import MeasurableHandle
from .components import ComponentProxy, ComponentsNamespace
from .objective import (
    ObjectiveGraphBuilder,
    compile_objective,
    objective_term,
    preflight_compiled_objective,
)
from .reconcile import LoadedSnapshot, ReconcileResult, ReconcileStep
from .tensor import LazyRef, MeasurableTensor
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
    "ComponentProxy",
    "ComponentsNamespace",
    "KernelMatchSpec",
    "LazyRef",
    "LoadedSnapshot",
    "MeasurableHandle",
    "MeasurableTensor",
    "ObjectiveGraphBuilder",
    "ReconcileResult",
    "ReconcileStep",
    "SessionLease",
    "VariableSpec",
    "cancel_job",
    "compile_objective",
    "configure_logging",
    "connect",
    "describe_component_row",
    "get_job",
    "list_backends",
    "list_jobs",
    "measurable_script_handle",
    "objective_term",
    "preflight_compiled_objective",
    "resolve_backend_id",
    "submit_closed_loop_optimize",
    "submit_compiled_dag",
    "submit_job",
    "tunable_script_handles",
    "wait_for_job",
]

# Intent helpers (prepare / run_optimize / run_cobyla / register_kernel / …)
# live on CloudLabsClient via connect(); export_session_kernel_packages is a
# public client method for advanced job handoff.
