from copy import deepcopy

import pytest

from app.services.v2.material_shortage import material_shortage_decision


@pytest.fixture
def fixture():
    return {
        "scenario_time": "2026-01-01T00:00:00Z",
        "demands": [
            {"id": "O-HIGH", "item": "P1", "quantity": 2, "due_at": 3, "priority": 10, "unit_revenue": 1000, "processing_days": 1, "resource": "line-1"},
            {"id": "O-LOW", "item": "P2", "quantity": 2, "due_at": 3, "priority": 1, "unit_revenue": 400, "processing_days": 1, "resource": "line-1"},
        ],
        "boms": [
            {"parent_item": "P1", "component_item": "SUB", "quantity_per": 1},
            {"parent_item": "P2", "component_item": "SUB", "quantity_per": 1},
            {"parent_item": "SUB", "component_item": "CRITICAL", "quantity_per": 2},
        ],
        "inventory": [{"id": "LOT-1", "item": "CRITICAL", "location": "MAIN", "quantity": 5}],
        "reservations": [{"item": "CRITICAL", "demand_id": "O-LOW", "quantity": 1, "status": "active"}],
        "supply_commitments": [
            {"id": "PO-VALID", "item": "CRITICAL", "quantity": 1, "available_at": "2026-01-02T00:00:00Z", "expires_at": "2026-01-10T00:00:00Z"},
            {"id": "PO-EXPIRED", "item": "CRITICAL", "quantity": 99, "available_at": "2026-01-01T00:00:00Z", "expires_at": "2025-12-31T00:00:00Z"},
        ],
        "supply_options": [{"id": "ALT-BAD", "supplier": "S-B", "item": "CRITICAL", "quantity": 100, "lead_time": 1, "qualified": False}],
    }


def test_shared_material_is_not_double_allocated_and_revenue_is_reproducible(fixture):
    result = material_shortage_decision(deepcopy(fixture))

    # O-HIGH takes the 4 free units. O-LOW could only gather 2 of 4 (its reservation + PO-VALID),
    # so it is blocked and that tentative allocation is rolled back instead of stranding material.
    assert sum(row["quantity"] for row in result["allocations"]) == 4
    assert result["shortages"] == [{"demand_id": "O-LOW", "item": "CRITICAL", "quantity": 2.0, "path": ["P2", "SUB", "CRITICAL"]}]
    assert result["revenue_at_risk"] == 800
    assert result["stranded_inventory"] == [{"item": "CRITICAL", "demand_id": "O-LOW", "source_id": "LOT-1", "quantity": 1.0}]
    assert result["impact_path"]["schema"] == "ImpactPathArtifact/v1"
    assert result["impact_path"]["completeness"] == "exact"
    assert len(result["impact_path"]["content_digest"]) == 64
    assert {row["kind"] for row in result["candidate_actions"]} == {"reallocate", "qualify_alternate"}
    qualify = [row for row in result["candidate_actions"] if row["kind"] == "qualify_alternate"][0]
    assert qualify["applicable"] is False and qualify["revenue_at_risk"] == 0


def test_blocked_order_does_not_strand_scarce_material(fixture):
    # Before the all-or-nothing fix, O-HIGH (needs 4) took 4 partial units and blocked O-LOW.
    fixture["inventory"][0]["quantity"] = 3
    fixture["reservations"] = []
    fixture["supply_commitments"] = []
    fixture["demands"][1]["quantity"] = 1  # O-LOW now needs 2 CRITICAL
    result = material_shortage_decision(fixture)

    statuses = {row["demand_id"]: row["status"] for row in result["demand_impacts"]}
    assert statuses == {"O-HIGH": "blocked", "O-LOW": "on_time"}
    assert result["revenue_at_risk"] == 2000


def test_in_time_commitment_covers_apparent_shortage(fixture):
    fixture["supply_commitments"][0]["quantity"] = 3
    result = material_shortage_decision(fixture)

    assert result["shortages"] == []
    assert result["feasible"] is True
    assert result["revenue_at_risk"] == 0


def test_over_reserved_inventory_and_bom_cycle_are_rejected(fixture):
    fixture["reservations"][0]["quantity"] = 6
    with pytest.raises(ValueError, match="reservations exceed inventory"):
        material_shortage_decision(fixture)

    fixture["reservations"] = []
    fixture["boms"].append({"parent_item": "CRITICAL", "component_item": "P1", "quantity_per": 1})
    with pytest.raises(ValueError, match="BOM cycle"):
        material_shortage_decision(fixture)
