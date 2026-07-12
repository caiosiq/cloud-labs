"""Ensemble optimization — spec, paths, normalization, pre-flight, session loop."""

from .backend import EnsembleEvaluationBackend
from .errors import EnsemblePreflightError, PathResolveError
from .metrics import evaluate_weighted_sum
from .normalize import NormalizedSearchSpace
from .paths import VariablePathResolver
from .compiler import compile_objective_graph, compile_objective_payload
from .graph import ObjectiveGraphSpec, ObjectiveGraphTermSpec
from .preflight import ensemble_scope_tag_ids, parse_ensemble_parameters, preflight_ensemble, preflight_objective_sources
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
    "ObjectiveGraphSpec",
    "ObjectiveGraphTermSpec",
    "OptimizeEnsembleParameters",
    "PathResolveError",
    "StubActuatorRouter",
    "VariablePathResolver",
    "VariableRef",
    "compile_legacy_strategy",
    "compile_objective_graph",
    "compile_objective_payload",
    "ensemble_scope_tag_ids",
    "evaluate_weighted_sum",
    "parse_ensemble_parameters",
    "preflight_ensemble",
    "preflight_objective_sources",
    "run_block_cobyla",
    "run_ensemble_optimization",
]