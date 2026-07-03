"""Ensemble optimization — spec, paths, normalization, pre-flight, session loop."""

from .backend import EnsembleEvaluationBackend
from .errors import EnsemblePreflightError, PathResolveError
from .metrics import evaluate_weighted_sum
from .normalize import NormalizedSearchSpace
from .paths import VariablePathResolver
from .preflight import ensemble_scope_tag_ids, parse_ensemble_parameters, preflight_ensemble
from .presets import compile_legacy_strategy
from .router import ActuatorRouter, StubActuatorRouter
from .session import EnsembleOptimizationResult, run_ensemble_optimization
from .solvers.block_cobyla import run_block_cobyla
from .spec import OptimizeEnsembleParameters, VariableRef

__all__ = [
    "ActuatorRouter",
    "EnsembleEvaluationBackend",
    "EnsembleOptimizationResult",
    "EnsemblePreflightError",
    "NormalizedSearchSpace",
    "OptimizeEnsembleParameters",
    "PathResolveError",
    "StubActuatorRouter",
    "VariablePathResolver",
    "VariableRef",
    "compile_legacy_strategy",
    "ensemble_scope_tag_ids",
    "evaluate_weighted_sum",
    "parse_ensemble_parameters",
    "preflight_ensemble",
    "run_block_cobyla",
    "run_ensemble_optimization",
]