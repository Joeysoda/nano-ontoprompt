import pytest
from pydantic import ValidationError

from app.routers.v2.workshop_apps import AppDefinition


def definition():
    return {
        "schema": "workshop-app/v1",
        "data_sources": [{"id": "tasks", "kind": "object_set", "label": "Tasks", "object_type": "AnyTask"}],
        "variables": [{"id": "scenario", "label": "Scenario", "default": "scenario-1"}],
        "pages": [{"id": "schedule", "title": "Schedule", "widgets": [{
            "id": "gantt", "type": "schedule_gantt", "title": "Plan", "source": "tasks", "path": "objects",
            "props": {"id_field": "object_id", "label_field": "properties.title", "resource_field": "properties.line",
                      "start_field": "properties.starts_at", "end_field": "properties.ends_at", "scenario_variable": "scenario"},
        }], "layout": [{"i": "gantt", "x": 0, "y": 0, "w": 24, "h": 14}]}],
    }


def test_schedule_widget_contract_uses_explicit_roles_not_domain_names():
    parsed = AppDefinition.model_validate(definition())
    widget = parsed.pages[0].widgets[0]
    assert widget.type == "schedule_gantt"
    assert widget.props["resource_field"] == "properties.line"


def test_schedule_widget_requires_role_bindings_and_known_scenario_variable():
    missing = definition(); del missing["pages"][0]["widgets"][0]["props"]["start_field"]
    with pytest.raises(ValidationError, match="missing"):
        AppDefinition.model_validate(missing)
    unknown = definition(); unknown["pages"][0]["widgets"][0]["props"]["scenario_variable"] = "missing"
    with pytest.raises(ValidationError, match="unknown variables"):
        AppDefinition.model_validate(unknown)


def test_schedule_widget_rejects_invalid_generic_configuration():
    invalid = definition()
    invalid["pages"][0]["widgets"][0]["props"].update({"snap_minutes": 0, "allocation_behavior": "factory_magic"})
    with pytest.raises(ValidationError, match="snap_minutes"):
        AppDefinition.model_validate(invalid)
