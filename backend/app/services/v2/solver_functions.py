"""Constraint-solver Functions that return typed schedule edits (Plan B item 8).

The Function reads hydrated, pinned Object Sets, solves a bounded MILP with
HiGHS (via SciPy) and returns a result manifest plus a typed edit batch.  It
never writes: only a function-backed Action (live or Scenario) applies the
edits.  Non-success outcomes (infeasible, timeout, unavailable) carry no
edits and surface as ``solver_<status>`` errors through the Action compiler.
"""
from __future__ import annotations

from app.services.v2.object_query.normalize import stable_hash

SUCCESS = ("optimal", "feasible")


def _manifest(status, *, objective=None, bound=None, gap=None, version=None, time_limit=None, message=""):
    return {"status": status, "solver": "highs-via-scipy-milp", "solver_version": version, "objective": objective,
            "best_bound": bound, "gap": gap, "time_limit_seconds": time_limit, "message": message}


def operator_assignment(inputs: dict) -> dict:
    """Assign each demand to one eligible operator, minimising the largest load.

    Demand properties: ``hours`` (processing time) and optional
    ``required_skill``.  Operator properties: ``capacity_hours`` and
    ``skills`` (list).  Edits set ``Demand.assigned_operator``.
    """
    demands, operators = inputs["demands"], inputs["operators"]
    limit = float(inputs.get("time_limit_seconds", 5))
    ontology_id = demands["ontology_id"]
    rows = sorted(demands["objects"], key=lambda o: o["object_id"])
    pool = sorted(operators["objects"], key=lambda o: o["object_id"])
    digest = stable_hash({"demands": rows, "operators": pool, "time_limit_seconds": limit})
    base = {"input_digest": digest, "assignments": [], "loads": {}}
    eligible = {(d, o) for d, row in enumerate(rows) for o, op in enumerate(pool)
                if not row["properties"].get("required_skill") or row["properties"]["required_skill"] in (op["properties"].get("skills") or [])}
    stranded = [rows[d]["object_id"] for d in range(len(rows)) if not any((d, o) in eligible for o in range(len(pool)))]
    if stranded:
        return {**base, "solver": _manifest("infeasible", time_limit=limit, message=f"No eligible operator for {', '.join(stranded)}"), "edits": []}
    try:
        import numpy as np
        import scipy
        from scipy.optimize import Bounds, LinearConstraint, milp
    except ImportError:
        return {**base, "solver": _manifest("unavailable", time_limit=limit, message="SciPy/HiGHS is not installed"), "edits": []}
    pairs = sorted(eligible)
    n = len(pairs) + 1  # last variable is the makespan
    hours = [float(rows[d]["properties"]["hours"]) for d, _ in pairs]
    # Tiny deterministic tie-break so preview and commit pick the same optimum;
    # it is far below the 0.5 h granularity of the makespan.
    cost = np.array([1e-6 * (i + 1) for i in range(len(pairs))] + [1.0])
    a, lower, upper = [], [], []
    for d in range(len(rows)):
        a.append([1.0 if i < len(pairs) and pairs[i][0] == d else 0.0 for i in range(n)]); lower.append(1); upper.append(1)
    for o, op in enumerate(pool):
        load = [hours[i] if pairs[i][1] == o else 0.0 for i in range(len(pairs))]
        a.append(load + [0.0]); lower.append(0); upper.append(float(op["properties"]["capacity_hours"]))
        a.append(load + [-1.0]); lower.append(-np.inf); upper.append(0)
    integrality = np.array([1] * len(pairs) + [0])
    result = milp(c=cost, integrality=integrality, bounds=Bounds(np.zeros(n), np.array([1.0] * len(pairs) + [np.inf])),
                  constraints=LinearConstraint(np.array(a), lower, upper), options={"time_limit": limit, "disp": False})
    has_plan = result.x is not None
    status = {0: "optimal", 1: "feasible" if has_plan else "timeout", 2: "infeasible"}.get(result.status, "error")
    manifest = _manifest(status, objective=round(float(result.x[-1]), 6) if has_plan else None,
                         bound=getattr(result, "mip_dual_bound", None), gap=getattr(result, "mip_gap", None),
                         version=scipy.__version__, time_limit=limit, message=str(result.message))
    if status not in SUCCESS:
        return {**base, "solver": manifest, "edits": []}
    assignments, loads, edits = [], {op["object_id"]: 0.0 for op in pool}, []
    for i, (d, o) in enumerate(pairs):
        if result.x[i] > 0.5:
            demand, operator = rows[d], pool[o]
            assignments.append({"demand": demand["object_id"], "operator": operator["object_id"], "hours": hours[i]})
            loads[operator["object_id"]] += hours[i]
            edits.append({"sequence": len(edits), "op": "set_property",
                          "target": {"ontology_id": ontology_id, "concrete_type": demand["object_type"], "object_id": demand["object_id"]},
                          "property": "assigned_operator", "value": operator["object_id"],
                          "expected_old_value": demand["properties"].get("assigned_operator")})
    return {**base, "solver": manifest, "assignments": assignments, "loads": loads, "edits": edits}


OBJECT_SET = {"type": "object"}
CONTRACT = {
    "asset_key": "operator_assignment", "implementation": "operator_assignment_v1", "version": "1.0.0", "kind": "edit",
    "input_schema": {"type": "object", "required": ["demands", "operators"], "properties": {
        "demands": {**OBJECT_SET, "x-ontology": {"kind": "object_set", "api_name": "Demand"}},
        "operators": {**OBJECT_SET, "x-ontology": {"kind": "object_set", "api_name": "Operator"}},
        "time_limit_seconds": {"type": "number", "exclusiveMinimum": 0, "maximum": 30}}},
    "output_schema": {"type": "object", "required": ["solver", "edits", "input_digest"]},
    "config": {"execution_class": "edit", "edit_provenance": {"Demand": ["set_property"]}},
}
