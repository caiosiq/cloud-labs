"""Edge-side optimization engine (no ``cloudlabs`` / ``lab_model`` imports).

General half of the tensor pipeline: tensors, kernels, metrics, normalize,
stepper, session. Lab seams (capture + router) live in each edge tree.
"""
from __future__ import annotations

from .capture import CaptureSource, StaticCaptureSource
from .kernels import (
    KernelError,
    clear_caches,
    eval_kernel,
    get_manifest_entry,
    kernels_http_catalog,
    list_kernels,
    load_manifest,
    provision_bytes,
    resolve_artifact_path,
    torch_available,
)
from .metrics import (
    METRIC_REGISTRY,
    MeasurementInvalid,
    evaluate_weighted_sum,
    get_metric,
)
from .normalize import DimensionBounds, NormalizedSearchSpace
from .guards import ClearanceError, MaxDeltaError, max_delta_limits
from .router import ActuatorRouter, RecordingRouter, StubActuatorRouter
from .session import OptimizationResult, run_optimization_session
from .spec import OptimizationPipeline, parse_pipeline
from .stepper import run_block_cobyla
from .tensors import EdgeTensor, camera_bgr_tensor, scalar_tensor

__all__ = [
    "ActuatorRouter",
    "CaptureSource",
    "ClearanceError",
    "DimensionBounds",
    "EdgeTensor",
    "KernelError",
    "METRIC_REGISTRY",
    "MaxDeltaError",
    "MeasurementInvalid",
    "NormalizedSearchSpace",
    "OptimizationPipeline",
    "OptimizationResult",
    "RecordingRouter",
    "StaticCaptureSource",
    "StubActuatorRouter",
    "camera_bgr_tensor",
    "clear_caches",
    "eval_kernel",
    "evaluate_weighted_sum",
    "get_manifest_entry",
    "get_metric",
    "kernels_http_catalog",
    "list_kernels",
    "load_manifest",
    "max_delta_limits",
    "parse_pipeline",
    "provision_bytes",
    "resolve_artifact_path",
    "run_block_cobyla",
    "run_optimization_session",
    "scalar_tensor",
    "torch_available",
]
