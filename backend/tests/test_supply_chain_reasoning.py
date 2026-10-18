from app.services.v2.logic_assets import EXECUTORS
from app.services.v2.scenario_study_templates import templates


def _inputs():
    template = next(item for item in templates() if item["template_id"] == "supply_chain_reasoning")
    return {item["key"]: item["default"] for item in template["spec"]["parameters"]}


def test_supply_chain_reasoning_allocates_inventory_then_qualified_sources():
    result = EXECUTORS["supply_chain_reasoning_v1"](_inputs())

    assert result["feasible"] is True
    assert result["shortages"] == []
    assert result["late_order_count"] == 0
    assert result["total_cost"] == 300
    assert result["demand_impacts"][0]["used_sources"][0]["source"] == "inventory"
    assert {row["supplier"] for row in result["supply_plan"]} == {"Supplier-A", "Supplier-B", "Supplier-C"}


def test_supply_chain_reasoning_uses_two_qualified_alternatives_after_outage():
    inputs = _inputs()
    inputs["supply_options"] = [
        {**inputs["supply_options"][0], "outage_until": "2026-01-03T00:00:00Z"},
        inputs["supply_options"][1], inputs["supply_options"][2], inputs["supply_options"][3], inputs["supply_options"][4],
    ]
    result = EXECUTORS["supply_chain_reasoning_v1"](inputs)

    assert result["feasible"] is True
    assert result["total_cost"] == 420
    used = result["demand_impacts"][1]["used_sources"]
    assert [(row.get("supplier"), row["quantity"]) for row in used if row.get("supplier")] == [("Supplier-B", 20.0), ("Supplier-C", 10.0)]


def test_supply_chain_reasoning_reports_shortage_and_bom_cycle():
    inputs = _inputs()
    inputs["supply_options"] = [{**row, "disabled": True} for row in inputs["supply_options"]]
    result = EXECUTORS["supply_chain_reasoning_v1"](inputs)
    assert result["feasible"] is False
    assert result["shortages"]
    assert result["demand_impacts"][1]["reason_codes"] == ["material_shortage"]

    inputs["boms"] = [
        {"parent_item": "P1", "component_item": "SUB", "quantity_per": 1},
        {"parent_item": "SUB", "component_item": "P1", "quantity_per": 1},
    ]
    try:
        EXECUTORS["supply_chain_reasoning_v1"](inputs)
    except ValueError as error:
        assert "BOM cycle" in str(error)
    else:
        raise AssertionError("cyclic BOM should be rejected")
