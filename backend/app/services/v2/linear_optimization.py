"""Bounded, domain-neutral linear and mixed-integer optimization contract."""
from __future__ import annotations

from math import isfinite


class OptimizationError(ValueError):
    pass


def _number(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise OptimizationError(f"{label} must be a finite number")
    return float(value)


def optimize(variables, constraints, *, objective_sense="min", time_limit_seconds=10.0):
    """Solve a caller-authored bounded LP/MILP using SciPy's HiGHS adapter.

    The contract deliberately knows nothing about plants, orders, lots or other
    domain concepts. Dataset-specific columns must be mapped into this model by
    a Study row-mapping node or by an explicitly versioned Function.
    """
    if objective_sense not in {"min", "max"}:
        raise OptimizationError("objective_sense must be min or max")
    limit = _number(time_limit_seconds, "time_limit_seconds")
    if not 0.1 <= limit <= 300:
        raise OptimizationError("time_limit_seconds must be between 0.1 and 300")
    if not isinstance(variables, list) or not 1 <= len(variables) <= 500:
        raise OptimizationError("variables must contain 1..500 rows")
    if not isinstance(constraints, list) or len(constraints) > 2000:
        raise OptimizationError("constraints must contain at most 2000 rows")

    ids, lower, upper, objective, integrality = [], [], [], [], []
    for row in variables:
        if not isinstance(row, dict) or not str(row.get("id", "")).strip():
            raise OptimizationError("every variable requires an id")
        key = str(row["id"])
        if key in ids:
            raise OptimizationError(f"duplicate variable: {key}")
        kind = row.get("kind", "continuous")
        if kind not in {"continuous", "integer", "binary"}:
            raise OptimizationError(f"invalid variable kind: {kind}")
        lo = _number(row.get("lower", 0), f"{key}.lower")
        hi = _number(row.get("upper", 1 if kind == "binary" else 1e12), f"{key}.upper")
        if kind == "binary":
            lo, hi = max(0.0, lo), min(1.0, hi)
        if lo > hi:
            raise OptimizationError(f"invalid bounds for {key}")
        ids.append(key); lower.append(lo); upper.append(hi)
        objective.append(_number(row.get("objective", 0), f"{key}.objective"))
        integrality.append(0 if kind == "continuous" else 1)

    positions = {key: index for index, key in enumerate(ids)}
    matrix, constraint_lower, constraint_upper, term_count = [], [], [], 0
    for index, row in enumerate(constraints):
        if not isinstance(row, dict) or row.get("sense") not in {"<=", "=", ">="}:
            raise OptimizationError(f"constraint {index} has an invalid sense")
        terms = row.get("terms")
        if not isinstance(terms, dict) or not terms:
            raise OptimizationError(f"constraint {index} requires terms")
        term_count += len(terms)
        if term_count > 100_000:
            raise OptimizationError("constraint term budget exceeded")
        coefficients = [0.0] * len(ids)
        for key, coefficient in terms.items():
            if key not in positions:
                raise OptimizationError(f"constraint references unknown variable: {key}")
            coefficients[positions[key]] = _number(coefficient, f"constraint {index}.{key}")
        rhs = _number(row.get("rhs"), f"constraint {index}.rhs")
        matrix.append(coefficients)
        if row["sense"] == "<=":
            constraint_lower.append(float("-inf")); constraint_upper.append(rhs)
        elif row["sense"] == ">=":
            constraint_lower.append(rhs); constraint_upper.append(float("inf"))
        else:
            constraint_lower.append(rhs); constraint_upper.append(rhs)

    try:
        import numpy as np
        import scipy
        from scipy.optimize import Bounds, LinearConstraint, milp
    except ImportError:
        return {"status": "unavailable", "objective": None, "values": [], "solver": "highs-via-scipy-milp",
                "solver_version": None, "best_bound": None, "gap": None, "message": "SciPy/HiGHS is not installed"}

    scipy_constraints = None
    if matrix:
        scipy_constraints = LinearConstraint(np.asarray(matrix), np.asarray(constraint_lower), np.asarray(constraint_upper))
    coefficients = np.asarray(objective)
    result = milp(c=-coefficients if objective_sense == "max" else coefficients,
                  integrality=np.asarray(integrality), bounds=Bounds(np.asarray(lower), np.asarray(upper)),
                  constraints=scipy_constraints, options={"time_limit": limit})
    has_solution = result.x is not None
    status = {0: "optimal", 1: "feasible" if has_solution else "timeout", 2: "infeasible", 3: "unbounded"}.get(result.status, "error")
    values = [] if not has_solution else [{"variable": key, "value": float(result.x[index])} for index, key in enumerate(ids)]
    value = None if not has_solution else float(coefficients @ result.x)
    bound = getattr(result, "mip_dual_bound", None)
    if bound is not None and objective_sense == "max":
        bound = -bound
    return {"status": status, "objective": value, "values": values, "solver": "highs-via-scipy-milp",
            "solver_version": scipy.__version__, "best_bound": float(bound) if bound is not None else None,
            "gap": float(result.mip_gap) if getattr(result, "mip_gap", None) is not None else None,
            "message": str(result.message)}
