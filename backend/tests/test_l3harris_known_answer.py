"""Known-answer test for the MRPSpeed-scale fixture.

Expected values below were derived by hand from the fixture, not by running the
model.  Day N means scenario_time + N days; every order takes 1 day.

CHIP-X (2 per unit, 34 needed): stock 18 (4 reserved for ORD-1002),
PO-IN-TIME 4 @day3, PO-LATE 8 @day9 (expeditable to @day4), PO-EXPIRED ignored.
Baseline allocation in due/priority order:
  1001,1002(reserved),1003,1004,1005,1006 -> stock @0
  1007,1008 -> PO-IN-TIME @3;  1009,1010,1011 -> PO-LATE @9;  1012 -> blocked
assembly-2 is busy days 1-4, so 1004 [4,5], 1006 [5,6], 1008 [6,7] still on time.
Late: 1009 finish 10 vs due 7, 1010 finish 10 vs 8, 1011 finish 11 vs 8.
Risk = 70k + 250k + 90k + 140k = 550k.
"""
from copy import deepcopy

import pytest

from app.services.v2.l3harris_fixture import l3harris_material_shortage_fixture
from app.services.v2.material_shortage import material_shortage_decision


@pytest.fixture(scope="module")
def result():
    return material_shortage_decision(l3harris_material_shortage_fixture())


def by_id(rows, key="demand_id"):
    return {row[key]: row for row in rows}


def test_fixture_meets_plan_complexity():
    data = l3harris_material_shortage_fixture()
    components = {row["component_item"] for row in data["boms"]}
    assert len({row["item"] for row in data["demands"]}) == 3
    assert len(data["demands"]) == 12
    assert len(components) >= 20
    assert len({row["location"] for row in data["inventory"]}) >= 3
    assert len({row["resource"] for row in data["demands"]}) == 2


def test_baseline_matches_hand_calculation(result):
    impacts = by_id(result["demand_impacts"])
    assert result["revenue_at_risk"] == 550000
    assert {k: (v["status"], v["late_days"]) for k, v in impacts.items() if v["status"] != "on_time"} == {
        "ORD-1009": ("late", 3), "ORD-1010": ("late", 2), "ORD-1011": ("late", 3), "ORD-1012": ("blocked", 0)}
    assert result["shortages"] == [{"demand_id": "ORD-1012", "item": "CHIP-X", "quantity": 4.0,
                                    "path": ["COMMS-C", "RF-C", "CHIP-X"]}]
    assert result["kpis"]["root_cause_items"] == ["CHIP-X"]
    assert result["kpis"]["on_time_units"] == 11
    # Fixed rework pushes assembly-2 work past day 4.
    assert impacts["ORD-1004"]["finish_at"].startswith("2026-01-06")
    assert impacts["ORD-1006"]["finish_at"].startswith("2026-01-07")


def test_expired_commitment_never_counts_and_reservation_is_owner_only(result):
    sources = {row["source_id"] for row in result["allocations"]}
    assert "PO-EXPIRED" not in sources
    reserved = [row for row in result["demand_impacts"] if row["demand_id"] == "ORD-1002"][0]["used_sources"]
    assert [row["source"] for row in reserved if row["item"] == "CHIP-X"] == ["reserved_inventory"]
    for impact in result["demand_impacts"]:
        if impact["demand_id"] != "ORD-1002":
            assert all(row["source"] != "reserved_inventory" for row in impact["used_sources"])


def test_apparent_psu_shortage_is_covered_in_time(result):
    psu = [row for row in result["allocations"] if row["item"] == "PSU-A"]
    assert sum(row["quantity"] for row in psu) == 6
    assert sum(row["quantity"] for row in psu if row["source_id"] == "PO-PSU-A") == 4
    assert all(impact["item"] != "RADAR-A" or "PSU-A" not in impact["root_cause_items"] for impact in result["demand_impacts"])


def test_no_source_is_over_allocated(result):
    data = l3harris_material_shortage_fixture()
    capacity = {row["id"]: row["quantity"] for row in data["inventory"] + data["supply_commitments"] + data["supply_options"]}
    used = {}
    for row in result["allocations"]:
        used[row["source_id"]] = used.get(row["source_id"], 0) + row["quantity"]
    assert all(used[key] <= capacity[key] + 1e-9 for key in used)


def test_candidates_match_hand_calculation(result):
    candidates = by_id(result["candidate_actions"], "id")
    expected = {
        # Expedite PO-LATE to day 4: 1009-1011 on time, 1012 still has no chips. Premium 8 x 400.
        "expedite:PO-LATE": (140000, ["ORD-1009", "ORD-1010", "ORD-1011"], [], 3200, True),
        # Supplier-B 4 chips @day3: only 1009 recovers; 1010-1012 wait for PO-LATE. 4 x 130.
        "buy_alternate:ALT-QUALIFIED": (480000, ["ORD-1009"], [], 520, True),
        # Revenue density A > B > C, reservation released: C orders lose chips to A/B orders.
        "reallocate:CHIP-X": (420000, ["ORD-1010", "ORD-1011"], ["ORD-1003", "ORD-1006"], 0, True),
        # Supplier-C day 1 covers all chips, but 1012 then misses due by capacity; hard-constraint blocked. 16 x 55.
        "qualify_alternate:ALT-UNQUALIFIED": (140000, ["ORD-1009", "ORD-1010", "ORD-1011"], [], 880, False),
        # 16 chips by day 4 and cross-line replan: every order on time. 3200 + 520.
        "combined:expedite+alternate+replan": (0, ["ORD-1009", "ORD-1010", "ORD-1011", "ORD-1012"], [], 3720, True),
    }
    assert set(candidates) == set(expected)
    for key, (risk, protected, harmed, cost, applicable) in expected.items():
        row = candidates[key]
        assert (row["revenue_at_risk"], [p["demand_id"] for p in row["protected_orders"]],
                [h["demand_id"] for h in row["harmed_orders"]], row["incremental_cost"], row["applicable"]) == \
               (risk, protected, harmed, cost, applicable), key
    assert candidates["qualify_alternate:ALT-UNQUALIFIED"]["hard_constraints"][0]["code"] == "supplier_not_qualified"
    assert [row["id"] for row in result["candidate_actions"]][0] == "combined:expedite+alternate+replan"


def test_candidate_revenue_is_conserved(result):
    for row in result["candidate_actions"]:
        gained = sum(item["revenue"] for item in row["protected_orders"])
        lost = sum(item["revenue"] for item in row["harmed_orders"])
        assert row["revenue_protected"] == pytest.approx(gained - lost), row["id"]
        assert row["revenue_at_risk"] == pytest.approx(result["revenue_at_risk"] - gained + lost), row["id"]


def test_combined_plan_lists_concrete_reassignments(result):
    combined = by_id(result["candidate_actions"], "id")["combined:expedite+alternate+replan"]
    moves = [row for row in combined["actions"] if row["action_type"] == "reassign_work_order"]
    assert moves == [{"action_type": "reassign_work_order", "demand_id": "ORD-1004", "from_resource": "assembly-2", "to_resource": "assembly-1"}]
    purchase = [row for row in combined["actions"] if row["action_type"] == "create_purchase_order"]
    assert purchase[0]["quantity"] == 4


def test_output_is_deterministic():
    first = material_shortage_decision(l3harris_material_shortage_fixture())
    second = material_shortage_decision(deepcopy(l3harris_material_shortage_fixture()))
    assert first["impact_path"]["content_digest"] == second["impact_path"]["content_digest"]
    assert [row["content_digest"] for row in first["candidate_actions"]] == [row["content_digest"] for row in second["candidate_actions"]]


def test_template_defaults_satisfy_registered_contracts():
    from app.services.v2.logic_assets import ASSET_DEFINITIONS, EXECUTORS
    from app.services.v2.logic_contracts import validate_payload
    from app.services.v2.scenario_study_templates import templates

    asset = next(row for row in ASSET_DEFINITIONS if row["asset_key"] == "material_shortage_decision_v1")
    template = next(row for row in templates() if row["template_id"] == "material_shortage_decision")
    inputs = {row["key"]: row["default"] for row in template["spec"]["parameters"]}
    validate_payload(asset["input_schema"], inputs, "input")
    outputs = EXECUTORS["material_shortage_decision_v1"](inputs)
    validate_payload(asset["output_schema"], outputs, "output")
    node = template["spec"]["nodes"][0]
    assert set(node["outputs"]) == set(asset["output_schema"]["required"])
    assert outputs["revenue_at_risk"] == 550000
