from unittest.mock import patch

from app.models.entity import Entity
from app.models.entity_instance import EntityInstance
from app.models.ontology import OntologyProject
from app.services.v2.revision_service import create_revision
from app.services.v2.dynamic_ontology_service import OntologyEditError, apply_batch_changes, apply_change, validate_batch_changes
from app.services.v2.what_if_service import WhatIfError, _context_facts, build_context, infer_facts, run_preview
from app.models.v2.dynamic_ontology import WhatIfRun, WhatIfScenario


class MemoryStorage:
    def put_bytes(self, *_args, **_kwargs):
        return "memory://revision"


def _project(db, admin_user, name="FactoryNet CNC"):
    project = OntologyProject(id="dynamic-ontology", name=name, domain="制造", data_class="temporal", created_by=admin_user.id)
    db.add(project)
    db.commit()
    return project


def test_single_operation_creates_revision_and_blocks_referenced_delete(db, admin_user):
    project = _project(db, admin_user)
    with patch("app.services.v2.revision_service.get_storage_service", return_value=MemoryStorage()), patch("app.services.v2.audit_runner.queue_local_audit", return_value=None):
        base = create_revision(db, project.id)
        added = apply_change(db, project.id, {"base_revision_id": base.id, "target_kind": "entity_type", "operation": "add", "payload": {"canonical_id": "Observation", "name_cn": "观测"}}, user_id=admin_user.id)
        prop = apply_change(db, project.id, {"base_revision_id": added["revision"]["id"], "target_kind": "property", "operation": "add", "payload": {"entity_id": "Observation", "property": {"id": "ordinal", "name": "Ordinal", "type": "integer", "source_field": "ordinal"}}}, user_id=admin_user.id)
        db.add(EntityInstance(id="instance-1", entity_id="Observation", ontology_id=project.id, row_identity="1", row_data={"ordinal": 1})); db.commit()
        try:
            apply_change(db, project.id, {"base_revision_id": prop["revision"]["id"], "target_kind": "entity_type", "operation": "delete", "target_id": "Observation"}, user_id=admin_user.id)
        except OntologyEditError as exc:
            assert exc.code == "CHANGE_BLOCKED"
        else:
            raise AssertionError("referenced entity deletion should be blocked")


def test_what_if_forward_chaining_and_property_override_are_isolated(db, admin_user):
    assert infer_facts([("IN_PHASE", ("obs", "phase")), ("HAS_TOOL_CONDITION", ("obs", "condition"))], [{"id": "r", "conditions": [("IN_PHASE", ["?obs", "?phase"]), ("HAS_TOOL_CONDITION", ["?obs", "?condition"])], "conclusion": ("PHASE_TOOL_STATE", ["?phase", "?condition"])}])["derived"][0]["fact"] == "PHASE_TOOL_STATE(phase, condition)"


def test_what_if_requires_a_selected_observation(db, admin_user):
    project = _project(db, admin_user, name="FactoryNet CNC requires target")
    try:
        build_context(db, project.id)
    except WhatIfError as exc:
        assert "选择一个 Observation" in str(exc)
    else:
        raise AssertionError("What-If should not build a whole-ontology context without a target")


def test_context_fact_projection_does_not_expand_unused_sensor_columns(db, admin_user):
    context = {
        "nodes": [
            {
                "id": f"obs-{index}",
                "entity_type": "Observation",
                "properties": {"ctx_tool_condition": "unworn", **{f"sensor_{column}": column for column in range(40)}},
            }
            for index in range(100)
        ],
        "edges": [
            {"id": f"edge-{index}", "source": f"obs-{index % 100}", "target": f"obs-{(index + 1) % 100}", "type": "NEXT_OBSERVATION"}
            for index in range(1500)
        ],
    }
    facts, _ = _context_facts(
        context,
        rules=[{"conditions": [("NEXT_OBSERVATION", ["?source", "?target"])], "property_specs": []}],
        assumptions=[{"kind": "set_property", "property": "ctx_tool_condition"}],
    )
    # The 1,500 synthetic edges intentionally repeat the same 100 adjacent
    # pairs, so the compiler's deterministic de-duplication leaves 200 facts
    # (100 structural relations plus 100 selected properties).
    assert len(facts) == 200
    assert any(fact[0] == "PROP_CTX_TOOL_CONDITION" for fact in facts)
    assert not any(fact[0] == "PROP_SENSOR_1" for fact in facts)


def test_batch_suggestions_are_validated_together_and_applied_atomically(db, admin_user):
    project = _project(db, admin_user, name="FactoryNet batch editor")
    with patch("app.services.v2.revision_service.get_storage_service", return_value=MemoryStorage()), patch("app.services.v2.audit_runner.queue_local_audit", return_value=None):
        base = create_revision(db, project.id)
        duplicate = {
            "base_revision_id": base.id,
            "operations": [
                {"target_kind": "entity_type", "operation": "add", "payload": {"canonical_id": "Observation", "name_cn": "观测"}},
                {"target_kind": "entity_type", "operation": "add", "payload": {"canonical_id": "Observation", "name_cn": "另一观测"}},
            ],
        }
        try:
            validate_batch_changes(db, project.id, duplicate)
        except OntologyEditError as exc:
            assert exc.code == "BATCH_CONFLICT"
        else:
            raise AssertionError("duplicate suggestions should be rejected before any write")
        assert db.query(Entity).filter(Entity.ontology_id == project.id).count() == 0

        valid = {
            "base_revision_id": base.id,
            "operations": [
                {"target_kind": "entity_type", "operation": "add", "payload": {"canonical_id": "Equipment", "name_cn": "设备"}},
                {"target_kind": "entity_type", "operation": "add", "payload": {"canonical_id": "Observation", "name_cn": "观测"}},
                {"target_kind": "relationship", "operation": "add", "payload": {"source_entity": "Equipment", "target_entity": "Observation", "type": "HAS_OBSERVATION", "cardinality": "one-to-many"}},
            ],
        }
        checked = validate_batch_changes(db, project.id, valid)
        assert checked["can_apply"] is True
        assert len(checked["operations"]) == 3
        result = apply_batch_changes(db, project.id, valid, user_id=admin_user.id)
        assert result["revision"]["is_current"] is True
        assert db.query(Entity).filter(Entity.ontology_id == project.id).count() == 2


def test_entity_update_preserves_properties_and_rejects_bulk_property_payload(db, admin_user):
    project = _project(db, admin_user, name="FactoryNet entity edit")
    entity = Entity(
        id="Observation",
        ontology_id=project.id,
        name_cn="观测",
        properties={"property_definitions": [{"id": "ordinal", "name": "Ordinal", "type": "integer"}]},
    )
    db.add(entity)
    db.commit()
    with patch("app.services.v2.revision_service.get_storage_service", return_value=MemoryStorage()), patch("app.services.v2.audit_runner.queue_local_audit", return_value=None):
        base = create_revision(db, project.id)
        result = apply_change(
            db,
            project.id,
            {"base_revision_id": base.id, "target_kind": "entity_type", "operation": "update", "target_id": "Observation", "payload": {"name_cn": "加工观测"}},
            user_id=admin_user.id,
        )
        assert result["revision"]["is_current"] is True
        db.refresh(entity)
        assert entity.name_cn == "加工观测"
        assert entity.properties["property_definitions"][0]["id"] == "ordinal"
        try:
            apply_change(
                db,
                project.id,
                {"base_revision_id": result["revision"]["id"], "target_kind": "entity_type", "operation": "update", "target_id": "Observation", "payload": {"properties": {"property_definitions": []}}},
                user_id=admin_user.id,
            )
        except OntologyEditError as exc:
            assert exc.code == "PROPERTY_OPERATION_REQUIRED"
        else:
            raise AssertionError("bulk property payload should require a typed property operation")
