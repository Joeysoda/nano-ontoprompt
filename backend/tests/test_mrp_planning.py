"""Portfolio MRP planning: hand-checked netting plus invariants on the real-demand dataset."""
from copy import deepcopy

import pytest

from app.services.v2.mrp_planning import mrpspeed_planning


def tiny(po_quantity=10):
    """One line P = 1 A + 2 B.  Hand answers in each test (week = 7 days)."""
    return {
        "scenario_time": "2026-01-05T00:00:00Z", "horizon_weeks": 4, "quarter_start": 0, "quarter_end": 28,
        "product_lines": [{"id": "P", "name": "P line", "policy": "base", "unit_price": 100, "weekly_rate": 14, "conversion_cost": 10, "resource": "L"}],
        "policy_definitions": [{"id": "base", "capacity_factor": 1, "pull_in_days": 0, "conversion_factor": 1, "exclude_low_confidence": False},
                               {"id": "fast", "capacity_factor": 2, "pull_in_days": 0, "conversion_factor": 1.5, "exclude_low_confidence": False}],
        "items": [{"id": "A", "kind": "part", "product_line": "P", "unit_cost": 5, "lead_time_days": 14},
                  {"id": "B", "kind": "part", "product_line": "P", "unit_cost": 1, "lead_time_days": 7}],
        "boms": [{"parent_item": "P", "component_item": "A", "quantity_per": 1}, {"parent_item": "P", "component_item": "B", "quantity_per": 2}],
        "demands": [{"id": "D1", "item": "P", "product_line": "P", "quantity": 4, "due_at": 10, "unit_revenue": 100, "processing_days": 2, "resource": "L"},
                    {"id": "D2", "item": "P", "product_line": "P", "quantity": 6, "due_at": 20, "unit_revenue": 100, "processing_days": 3, "resource": "L"}],
        "inventory": [{"id": "LA", "item": "A", "quantity": 5}, {"id": "LB", "item": "B", "quantity": 20}],
        "reservations": [], "supply_options": [], "fixed_tasks": [],
        "supply_commitments": [{"id": "PO1", "item": "A", "quantity": po_quantity, "available_at": 14, "unit_cost": 5}],
    }


def rows(result):
    return {row["item"]: row for row in result["coverage"]["rows"]}


def test_netting_matches_hand_calculation():
    # Requirements are netted on due - build time: D1 day 10-2=8 (week 1), D2 day 20-3=17 (week 2).
    # A: 5 | 5-4=1 | 1+10-6=5 | 5     B: 20 | 20-8=12 | 12-12=0 | 0
    result = mrpspeed_planning(tiny())
    assert rows(result)["A"]["projected"] == [5, 1, 5, 5]
    assert rows(result)["B"]["projected"] == [20, 12, 0, 0]
    assert result["totals"]["planned_orders"] == 0
    assert result["totals"]["projected_revenue"] == 1000 == result["quarter"]["revenue_target"]
    # Cost = qty x (5 + 2x1 material + 10 conversion) = 10 x 17.
    assert result["totals"]["cost"] == 170


def test_short_part_creates_planned_order_with_lead_time_offset():
    # PO1 only 3: A week 2 = 1 + 3 - 6 = -2 -> lot-for-lot order of 2, released 2 weeks earlier.
    result = mrpspeed_planning(tiny(po_quantity=3))
    a = rows(result)["A"]
    assert a["projected"][2] == -2 and a["first_shortage_week"] == 2
    assert a["planned_orders"] == [{"week": 2, "release_week": 0, "quantity": 2}]
    events = {row["id"]: row for row in result["demand_events"]}
    assert events["D2"]["status"] == "blocked" and events["D2"]["root_cause_items"] == ["A"]
    assert result["totals"]["projected_revenue"] == 400


@pytest.fixture(scope="module")
def real():
    from app.services.v2.mrpspeed_fixture import mrpspeed_fixture
    data = mrpspeed_fixture()
    return data, mrpspeed_planning(data)


def test_real_dataset_provenance_and_scale(real):
    data, _ = real
    assert "DataCo" in data["provenance"]["real"]
    assert len(data["demands"]) >= 100 and len(data["product_lines"]) == 5
    assert sum(row["source_orders"] for row in data["demands"]) > 10000   # real order lines behind the events


def test_candidates_conserve_and_defer_never_harms(real):
    _, result = real
    by_status = {row["id"]: row["status"] for row in result["demand_events"]}
    for row in result["candidate_actions"]:
        assert all(by_status[o["demand_id"]] != "on_time" for o in row["protected_orders"]), row["id"]
        assert all(by_status[o["demand_id"]] == "on_time" for o in row["harmed_orders"]), row["id"]
        if row["kind"] == "defer_po":
            assert row["applicable"] and row["capital_deferred"] > 0 and not row["harmed_orders"]
            assert row["quarter_revenue_delta"] >= 0
        if row["kind"] == "set_policy":
            assert row["quarter_revenue_delta"] > 0


def test_one_late_part_is_not_fixed_by_expediting_another(real):
    """Bike needs frame AND derailleur: expediting only the frame cannot protect orders; fixing both does."""
    _, result = real
    candidates = {row["id"]: row for row in result["candidate_actions"]}
    frame_only = next(row for key, row in candidates.items() if key.startswith("expedite:") and "Aluminum Frame" in key)
    combined = candidates["combined:BIKE"]
    assert len(combined["protected_orders"]) > len(frame_only["protected_orders"])
    assert combined["quarter_revenue_delta"] > frame_only["quarter_revenue_delta"]


def test_object_query_records_give_identical_results(real):
    from app.services.v2.material_shortage_objects import PLANNING_INPUT_TYPES, fixture_objects
    data, result = real
    nodes = fixture_objects(data, id_prefix="abcd1234:", input_types=PLANNING_INPUT_TYPES)
    records = {key: [{"object_id": n["id"], "object_type": kind, "properties": n["properties"]} for n in nodes if n["entity_type"] == kind]
               for key, kind in PLANNING_INPUT_TYPES.items()}
    params = {key: data[key] for key in ("scenario_time", "horizon_weeks", "quarter_start", "quarter_end")}
    assert mrpspeed_planning({**records, **params})["totals"] == result["totals"]


def test_deterministic(real):
    data, result = real
    again = mrpspeed_planning(deepcopy(data))
    assert [row["content_digest"] for row in again["candidate_actions"]] == [row["content_digest"] for row in result["candidate_actions"]]


def test_property_named_kind_does_not_break_canonical_hashing():
    """A type with a property called "kind" made data-view hashing crash (unhashable dict)."""
    from app.services.v2.object_query.normalize import stable_hash
    assert stable_hash({"properties": {"kind": {"data_type": "string"}, "code": {"data_type": "string"}}})
    assert stable_hash({"kind": "and", "items": [{"kind": "b"}, {"kind": "a"}]}) == stable_hash({"kind": "and", "items": [{"kind": "a"}, {"kind": "b"}]})
