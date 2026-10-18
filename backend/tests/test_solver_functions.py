"""Solver Function contract on the pinned frePPLe manufacturing demo.

Inputs are derived from frePPLe's public demo fixture (synthetic, Apache
licensed): open demands, the operator pool and its skills.  Processing hours
come from each item's operation (duration + duration_per x quantity).  The
fixture has no horizon capacity (``maximum = 1`` means one job at a time), so
``capacity_hours`` is an explicit scenario assumption.
"""
import hashlib
import itertools
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from app.services.v2.function_contracts import SolverOutcome
from app.services.v2.logic_assets import execute
from app.services.v2.manufacturing_data import SHA256
from app.services.v2.object_query.metadata import OntologyMetadata, PropertyMetadata, TypeMetadata
from app.services.v2.solver_functions import CONTRACT

FIXTURE = Path(__file__).resolve().parents[2] / "data" / "frepple_demo" / "manufacturing_demo.json"


def frepple_problem(capacity_hours=120.0, quantity_factor=1):
    raw = FIXTURE.read_bytes()
    assert hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest() == SHA256
    rows = json.loads(raw)
    model = lambda name: [r["fields"] for r in rows if r["model"] == name]  # noqa: E731
    operations = {o["name"]: o for o in model("input.operation")}
    skill_of = {(r["operation_id"]): r.get("skill_id") for r in model("input.operationresource") if r["resource_id"] == "operators"}
    objects = {}
    for demand in model("input.demand"):
        if demand["status"] != "open":
            continue
        top = next(o for o in operations.values() if o.get("item_id") == demand["item_id"])
        steps = [o for o in operations.values() if o.get("owner_id") == top["name"]] if top["type"] == "routing" else [top]
        staffed = [s for s in steps if s["name"] in skill_of]  # steps that occupy an operator
        quantity = demand["quantity"] * quantity_factor
        hours = sum(float(s.get("duration") or 0) + float(s.get("duration_per") or 0) * quantity for s in staffed) / 3600
        skills = {skill_of[s["name"]] for s in staffed} - {None}
        objects[("Demand", demand["name"])] = {"item": demand["item_id"], "quantity": quantity, "hours": hours,
                                               "required_skill": next(iter(skills), None), "assigned_operator": None}
    skills = {}
    for row in model("input.resourceskill"):
        skills.setdefault(row["resource_id"], []).append(row["skill_id"])
    for resource in model("input.resource"):
        if resource.get("owner_id") == "operators":
            objects[("Operator", resource["name"])] = {"skills": skills.get(resource["name"], []), "capacity_hours": capacity_hours}
    return SimpleNamespace(objects=objects, edges=[])


def handle(snapshot, kind, exclude=()):
    return {"kind": "set_handle", "completeness": "complete", "consistency": "snapshot", "data_view_id": "view-pinned",
            "objects": [{"object_type": t, "object_id": i} for t, i in snapshot.objects if t == kind and i not in exclude]}


METADATA = OntologyMetadata([TypeMetadata("Demand", properties={"hours": PropertyMetadata("hours", "double")}),
                             TypeMetadata("Operator", properties={"capacity_hours": PropertyMetadata("capacity_hours", "double")})], [])


def run(snapshot, **inputs):
    return execute(CONTRACT, {"demands": handle(snapshot, "Demand"), "operators": handle(snapshot, "Operator"), **inputs},
                   metadata=METADATA, snapshot=snapshot, ontology_id="ont", through_action=True)["output"]


def brute_force_makespan(snapshot):
    demands = [(i, p) for (t, i), p in snapshot.objects.items() if t == "Demand"]
    operators = [(i, p) for (t, i), p in snapshot.objects.items() if t == "Operator"]
    choices = [[o for o, (_, op) in enumerate(operators) if not d["required_skill"] or d["required_skill"] in op["skills"]] for _, d in demands]
    fixed = [c[0] for c in choices if len(c) == 1]
    free = [(k, c) for k, c in enumerate(choices) if len(c) > 1]
    base = np.zeros(len(operators))
    for k, c in enumerate(choices):
        if len(c) == 1:
            base[c[0]] += demands[k][1]["hours"]
    best = np.inf
    for combo in itertools.product(*[c for _, c in free]):
        loads = base.copy()
        for (k, _), o in zip(free, combo):
            loads[o] += demands[k][1]["hours"]
        if all(loads[o] <= operators[o][1]["capacity_hours"] for o in range(len(operators))):
            best = min(best, loads.max())
    return best, len(fixed), len(free)


def test_frepple_fixture_shape():
    snapshot = frepple_problem()
    demands = {i: p for (t, i), p in snapshot.objects.items() if t == "Demand"}
    assert len(demands) == 16 and sum(p["hours"] for p in demands.values()) == pytest.approx(230.0)
    assert {i: p["skills"] for (t, i), p in snapshot.objects.items() if t == "Operator"} == {
        "Philippe": ["Sawing skill"], "Carl": ["Sawing skill"], "Antonio": ["Varnish skill"]}
    assert {p["required_skill"] for p in demands.values() if p["item"] == "varnished chair"} == {"Varnish skill"}


def test_solver_matches_exhaustive_optimum_and_emits_typed_edits():
    snapshot = frepple_problem()
    expected, fixed, free = brute_force_makespan(snapshot)
    assert (fixed, free) == (5, 11)  # 3^11 = 177,147 assignments enumerated
    output = run(snapshot)
    assert output["solver"]["status"] == "optimal" and output["solver"]["objective"] == pytest.approx(expected)
    assert max(output["loads"].values()) == pytest.approx(expected)
    assert len(output["edits"]) == 16 and [e["sequence"] for e in output["edits"]] == list(range(16))
    by_demand = {a["demand"]: a["operator"] for a in output["assignments"]}
    assert all(by_demand[i] == "Antonio" for (t, i), p in snapshot.objects.items() if t == "Demand" and p["required_skill"])
    assert all(e["target"]["ontology_id"] == "ont" and e["property"] == "assigned_operator" for e in output["edits"])
    assert run(snapshot)["edits"] == output["edits"]  # deterministic: preview and commit agree


def test_caller_cannot_forge_object_properties():
    snapshot = frepple_problem()
    forged = handle(snapshot, "Demand")
    for item in forged["objects"]:
        item["properties"] = {"hours": 0.0}
    output = execute(CONTRACT, {"demands": forged, "operators": handle(snapshot, "Operator")},
                     metadata=METADATA, snapshot=snapshot, ontology_id="ont", through_action=True)["output"]
    assert sum(a["hours"] for a in output["assignments"]) == pytest.approx(230.0)


@pytest.mark.parametrize("snapshot,exclude,status", [
    (frepple_problem(capacity_hours=120.0, quantity_factor=2), (), "infeasible"),   # demand doubles: 460 h > 3 x 120 h
    (frepple_problem(), ("Antonio",), "infeasible"),                               # only varnisher absent
])
def test_infeasible_plans_raise_and_emit_no_edits(snapshot, exclude, status):
    with pytest.raises(SolverOutcome) as caught:
        execute(CONTRACT, {"demands": handle(snapshot, "Demand"), "operators": handle(snapshot, "Operator", exclude)},
                metadata=METADATA, snapshot=snapshot, ontology_id="ont", through_action=True)
    assert caught.value.manifest["status"] == status


def test_doubled_demand_fits_when_capacity_allows():
    snapshot = frepple_problem(capacity_hours=200.0, quantity_factor=2)
    expected, _, _ = brute_force_makespan(snapshot)
    output = run(snapshot)
    assert output["solver"]["status"] == "optimal" and output["solver"]["objective"] == pytest.approx(expected)
