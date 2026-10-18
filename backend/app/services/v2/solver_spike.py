"""Optional, non-production solver adapters for tiny known-answer fixtures.

This module is intentionally not registered as a Study node. A solver becomes
available to authored Studies only after its correctness, image-size, licence,
and performance gates have been reviewed.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from importlib.metadata import PackageNotFoundError, version
from importlib.util import find_spec
from typing import Literal


@dataclass(frozen=True)
class SolverResult:
    status: Literal["optimal", "feasible", "infeasible", "timeout", "unavailable", "error"]
    solver: str
    solver_version: str | None
    objective: float | None
    assignments: list[dict[str, int]]
    best_bound: float | None
    gap: float | None
    time_limit_seconds: float
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


def _status_from_milp(status: int, has_incumbent: bool) -> str:
    return {0: "optimal", 1: "feasible" if has_incumbent else "timeout", 2: "infeasible"}.get(status, "error")


def capabilities() -> dict[str, dict[str, str | bool | None]]:
    scipy_available = find_spec("scipy") is not None
    ortools_available = find_spec("ortools") is not None
    def package_version(package: str) -> str | None:
        try:
            return version(package)
        except PackageNotFoundError:
            return None

    return {
        "highs_via_scipy_milp": {
            "available": scipy_available,
            "version": package_version("scipy"),
            "status": "optional_spike_only" if scipy_available else "capability_unavailable",
        },
        "ortools_cp_sat": {
            "available": ortools_available,
            "version": package_version("ortools"),
            "status": "optional_spike_only" if ortools_available else "capability_unavailable",
        },
    }


def solve_assignment(costs: list[list[float]], *, allowed: list[list[bool]] | None = None,
                     time_limit_seconds: float = 5.0) -> SolverResult:
    """Minimize a rectangular one-task-per-resource assignment using optional HiGHS."""
    if time_limit_seconds <= 0:
        raise ValueError("time_limit_seconds must be positive")
    if not costs or not costs[0] or any(len(row) != len(costs[0]) for row in costs):
        raise ValueError("costs must be a non-empty rectangular matrix")
    if allowed is not None and (
        len(allowed) != len(costs) or any(len(row) != len(costs[0]) for row in allowed)
    ):
        raise ValueError("allowed must have the same shape as costs")
    if len(costs) > len(costs[0]):
        return SolverResult("infeasible", "highs-via-scipy", None, None, [], None, None,
                            time_limit_seconds, "There are fewer resources than tasks.")
    try:
        import numpy as np
        import scipy
        from scipy.optimize import Bounds, LinearConstraint, milp
        from scipy.sparse import lil_matrix
    except ImportError:
        return SolverResult("unavailable", "highs-via-scipy", None, None, [], None, None,
                            time_limit_seconds, "Optional SciPy/HiGHS dependency is not installed.")

    task_count = len(costs)
    resource_count = len(costs[0])
    variable_count = task_count * resource_count
    constraints = lil_matrix((task_count + resource_count, variable_count), dtype=float)
    for task_index in range(task_count):
        for resource_index in range(resource_count):
            variable = task_index * resource_count + resource_index
            constraints[task_index, variable] = 1
            constraints[task_count + resource_index, variable] = 1
    lower = [1.0] * task_count + [0.0] * resource_count
    upper = [1.0] * task_count + [1.0] * resource_count
    variable_upper_bounds = np.ones(variable_count)
    if allowed is not None:
        variable_upper_bounds = np.asarray(allowed, dtype=float).reshape(variable_count)
    result = milp(
        c=np.asarray(costs, dtype=float).reshape(variable_count),
        integrality=np.ones(variable_count),
        bounds=Bounds(np.zeros(variable_count), variable_upper_bounds),
        constraints=LinearConstraint(constraints.tocsr(), lower, upper),
        options={"time_limit": float(time_limit_seconds)},
    )
    status = _status_from_milp(result.status, result.x is not None)
    assignments = []
    if result.x is not None:
        assignments = [
            {"task": task, "resource": int(np.argmax(result.x[task * resource_count:(task + 1) * resource_count]))}
            for task in range(task_count)
        ]
    return SolverResult(
        status=status,
        solver="highs-via-scipy-milp",
        solver_version=scipy.__version__,
        objective=float(result.fun) if result.fun is not None else None,
        assignments=assignments,
        best_bound=float(result.mip_dual_bound) if getattr(result, "mip_dual_bound", None) is not None else None,
        gap=float(result.mip_gap) if getattr(result, "mip_gap", None) is not None else None,
        time_limit_seconds=time_limit_seconds,
        message=str(result.message),
    )
