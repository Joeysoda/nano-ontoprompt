from unittest.mock import patch

from app.models.entity import Entity
from app.models.entity_instance import EntityInstance
from app.models.ontology import OntologyProject
from app.models.relation import Relation
from app.models.v2.security import OntologySecurityPolicy
from app.models.v2.dynamic_ontology import WhatIfScenario
from app.models.v2.schema_migration import SchemaMigrationPlan, SchemaMigrationRun
from app.models.v2.semantic_core import OntologySourceMapping
from app.models.v2.temporal_replay import DataModelSnapshot, TemporalFact, TemporalReplay
from app.routers.v2.graph import _authorize_graph_data, _canonical_ontology_data
from app.routers.v2.semantic_core import _filter_semantic_schema
from app.services.v2.authorization_service import AuthorizationContext, filter_records, redact_link_record, redact_record, resolve_projection
from app.services.v2.data_plane_service import DataPlaneError, build_context, decode_cursor, encode_cursor
from app.services.v2.revision_service import create_revision
from app.services.v2.schema_migration_service import apply_plan, dry_run, reconcile_plan, revert_plan
from app.services.v2.semantic_core_service import SemanticCoreError, apply_semantic_change, ensure_semantic_metadata, semantic_change_impact, semantic_schema


class MemoryStorage:
    def put_bytes(self, *_args, **_kwargs):
        return "memory://revision"


def _project(db, admin_user, project_id="semantic-core-test", entity_prefix=""):
    project = OntologyProject(id=project_id, name="FactoryNet security", domain="制造", data_class="temporal", created_by=admin_user.id)
    db.add(project)
    equipment_id = f"{entity_prefix}equipment" if entity_prefix else "equipment"
    observation_id = f"{entity_prefix}observation" if entity_prefix else "observation"
    db.add(Entity(id=equipment_id, ontology_id=project.id, canonical_id="Equipment", name_cn="设备", properties={"property_definitions": [{"id": "equipment_id", "name": "设备编号", "type": "string", "source_field": "equipment_id"}]}))
    db.add(Entity(id=observation_id, ontology_id=project.id, canonical_id="Observation", name_cn="观测", properties={"property_definitions": [{"id": "ordinal", "name": "Ordinal", "type": "integer", "source_field": "ordinal"}]}))
    db.add(Relation(id=f"{entity_prefix}equipment-observation", ontology_id=project.id, source_entity=equipment_id, target_entity=observation_id, type="HAS_OBSERVATION", properties={"cardinality": "one-to-many"}))
    db.commit()
    return project


def test_semantic_schema_backfills_legacy_and_is_revisioned(db, admin_user):
    project = _project(db, admin_user)
    with patch("app.services.v2.revision_service.get_storage_service", return_value=MemoryStorage()):
        revision = create_revision(db, project.id)
        info = ensure_semantic_metadata(db, project.id, revision.id, commit=True)
    schema = semantic_schema(db, project.id)
    assert info["metadata_digest"]
    assert schema["resources"]["object_type"]
    assert {item["api_name"] for item in schema["resources"]["link_type"]} == {"HAS_OBSERVATION"}
    assert db.query(Entity).filter(Entity.id == "equipment").one().semantic_resource_id


def test_owner_projection_is_unrestricted_and_policy_redacts_other_user(db, admin_user, editor_user):
    project = _project(db, admin_user)
    db.add(EntityInstance(id="obs-1", ontology_id=project.id, entity_id="observation", row_identity="1", row_data={"ordinal": 1, "secret": "hidden"}))
    db.add(OntologySecurityPolicy(ontology_id=project.id, name="editor ordinal", subject_kind="user", subject_id=editor_user.id, effect="allow", scope_kind="object_type", scope_id="observation", conditions_json=[{"field": "ordinal", "operator": ">=", "value": 1}], field_allowlist_json=["ordinal"], created_by=admin_user.id))
    db.commit()
    owner = resolve_projection(db, AuthorizationContext(project.id, admin_user.id, "admin", object_type_id="observation"), records=[{"id": "obs-1", "properties": {"ordinal": 1, "secret": "hidden"}}])
    assert owner.unrestricted
    editor = resolve_projection(db, AuthorizationContext(project.id, editor_user.id, "editor", object_type_id="observation"), records=[{"id": "obs-1", "properties": {"ordinal": 1, "secret": "hidden"}}])
    redacted = redact_record(editor, {"id": "obs-1", "properties": {"ordinal": 1, "secret": "hidden"}})
    assert redacted["properties"]["ordinal"] == 1
    assert redacted["properties"]["secret"] is None


def test_authorization_resolves_stable_resource_ids_to_legacy_row_fields(db, admin_user, editor_user):
    project = _project(db, admin_user, project_id="semantic-core-stable-policy-ids")
    with patch("app.services.v2.revision_service.get_storage_service", return_value=MemoryStorage()):
        revision = create_revision(db, project.id)
        ensure_semantic_metadata(db, project.id, revision.id, commit=True)
        schema = semantic_schema(db, project.id)
    observation_type = next(item for item in schema["resources"]["object_type"] if item["api_name"] == "Observation")
    ordinal_property = next(
        item for item in schema["resources"]["property"]
        if item["api_name"] == "ordinal" and item["parent_resource_id"] == observation_type["resource_id"]
    )
    db.add(OntologySecurityPolicy(
        ontology_id=project.id, revision_id=revision.id, name="stable observation allow",
        subject_kind="user", subject_id=editor_user.id, effect="allow",
        scope_kind="object_type", scope_id=observation_type["resource_id"],
        conditions_json=[], field_allowlist_json=[ordinal_property["resource_id"]],
        created_by=admin_user.id,
    ))
    db.add(OntologySecurityPolicy(
        ontology_id=project.id, revision_id=revision.id, name="stable ordinal condition",
        subject_kind="user", subject_id=editor_user.id, effect="deny",
        scope_kind="object_type", scope_id=observation_type["resource_id"],
        conditions_json=[{"field": ordinal_property["resource_id"], "operator": ">=", "value": 5}],
        field_allowlist_json=[ordinal_property["resource_id"]],
        created_by=admin_user.id,
    ))
    db.commit()
    records = [
        {"id": "low", "entity_id": "observation", "semantic_resource_id": observation_type["resource_id"], "properties": {"ordinal": 2, "secret": "hidden"}},
        {"id": "high", "entity_id": "observation", "semantic_resource_id": observation_type["resource_id"], "properties": {"ordinal": 5, "secret": "hidden"}},
    ]
    projection = resolve_projection(
        db,
        AuthorizationContext(project.id, editor_user.id, "editor", metadata_revision_id=revision.id),
        records=records,
    )
    visible = filter_records(projection, records)
    assert {record["id"] for record in visible} == {"low", "high"}
    assert next(record for record in visible if record["id"] == "low")["properties"]["ordinal"] == 2
    assert next(record for record in visible if record["id"] == "high")["properties"]["ordinal"] is None
    assert next(record for record in visible if record["id"] == "low")["properties"]["secret"] is None


def test_authorization_without_revision_uses_current_policy_revision(db, admin_user, editor_user):
    project = _project(db, admin_user, project_id="semantic-core-current-policy-revision")
    with patch("app.services.v2.revision_service.get_storage_service", return_value=MemoryStorage()):
        old_revision = create_revision(db, project.id)
        ensure_semantic_metadata(db, project.id, old_revision.id, commit=True)
        current_revision = create_revision(db, project.id, parent_revision_id=old_revision.id)
        ensure_semantic_metadata(db, project.id, current_revision.id, commit=True)
    db.add(OntologySecurityPolicy(
        ontology_id=project.id, revision_id=old_revision.id, name="old revision access",
        subject_kind="user", subject_id=editor_user.id, effect="allow",
        scope_kind="object_type", scope_id="observation", conditions_json=[],
        field_allowlist_json=[], created_by=admin_user.id,
    ))
    db.commit()
    projection = resolve_projection(
        db,
        AuthorizationContext(project.id, editor_user.id, "editor"),
        records=[{"id": "obs", "entity_id": "observation", "properties": {"ordinal": 1}}],
    )
    assert projection.allowed is False


def test_permission_change_produces_a_new_digest(db, admin_user, editor_user):
    project = _project(db, admin_user, project_id="semantic-core-permission-digest-change")
    with patch("app.services.v2.revision_service.get_storage_service", return_value=MemoryStorage()):
        revision = create_revision(db, project.id)
        ensure_semantic_metadata(db, project.id, revision.id, commit=True)
    policy = OntologySecurityPolicy(
        ontology_id=project.id, revision_id=revision.id, name="mutable fields",
        subject_kind="user", subject_id=editor_user.id, effect="allow",
        scope_kind="object_type", scope_id="observation", conditions_json=[],
        field_allowlist_json=["ordinal"], created_by=admin_user.id,
    )
    db.add(policy)
    db.commit()
    context = AuthorizationContext(project.id, editor_user.id, "editor", metadata_revision_id=revision.id)
    first = resolve_projection(db, context).permission_digest
    policy.field_allowlist_json = ["secret"]
    db.commit()
    second = resolve_projection(db, context).permission_digest
    assert first != second


def test_stream_projection_enforces_conditional_allow_without_per_row_queries(db, admin_user, editor_user):
    project = _project(db, admin_user, project_id="semantic-core-stream-authorization")
    db.add(OntologySecurityPolicy(
        ontology_id=project.id,
        name="only recent observations",
        subject_kind="user",
        subject_id=editor_user.id,
        effect="allow",
        scope_kind="object_type",
        scope_id="observation",
        conditions_json=[{"field": "ordinal", "operator": ">=", "value": 10}],
        field_allowlist_json=["ordinal"],
        created_by=admin_user.id,
    ))
    db.commit()
    projection = resolve_projection(
        db,
        AuthorizationContext(project.id, editor_user.id, "editor", object_type_id="observation"),
    )
    query_count = 0
    from sqlalchemy import event

    def count_policy_queries(_conn, _cursor, statement, _params, _context, _many):
        nonlocal query_count
        if "v2_ontology_security_policies" in statement:
            query_count += 1

    event.listen(db.get_bind(), "before_cursor_execute", count_policy_queries)
    try:
        assert redact_record(projection, {"id": "low", "entity_id": "observation", "properties": {"ordinal": 2}}) is None
        high = redact_record(projection, {"id": "high", "entity_id": "observation", "properties": {"ordinal": 12, "secret": "hidden"}})
        assert high["properties"]["ordinal"] == 12
        assert high["properties"]["secret"] is None
        assert query_count == 0
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", count_policy_queries)


def test_graph_authorization_loads_policies_once_for_many_nodes_and_edges(db, admin_user, editor_user):
    project = _project(db, admin_user, project_id="semantic-core-graph-policy-batch")
    db.add(OntologySecurityPolicy(
        ontology_id=project.id, name="observation access", subject_kind="user",
        subject_id=editor_user.id, effect="allow", scope_kind="object_type",
        scope_id="observation", conditions_json=[], field_allowlist_json=["ordinal"],
        created_by=admin_user.id,
    ))
    db.add(OntologySecurityPolicy(
        ontology_id=project.id, name="hide observation link", subject_kind="user",
        subject_id=editor_user.id, effect="deny", scope_kind="link",
        scope_id="HAS_OBSERVATION", conditions_json=[], field_allowlist_json=[],
        created_by=admin_user.id,
    ))
    db.commit()
    policy_query_count = 0
    from sqlalchemy import event

    def count_policy_query(_conn, _cursor, statement, _params, _context, _many):
        nonlocal policy_query_count
        if "v2_ontology_security_policies" in statement:
            policy_query_count += 1

    event.listen(db.get_bind(), "before_cursor_execute", count_policy_query)
    try:
        nodes = [
            {"id": f"node-{index}", "entity_type": "observation", "properties": {"ordinal": index, "secret": "hidden"}}
            for index in range(12)
        ]
        data = _authorize_graph_data(
            db, project.id,
            {"nodes": nodes, "edges": [{"id": "edge-1", "source": "node-0", "target": "node-1", "type": "HAS_OBSERVATION", "properties": {}}]},
            editor_user,
        )
        assert len(data["nodes"]) == 12
        assert all(node["properties"]["secret"] is None for node in data["nodes"])
        assert data["edges"] == []
        assert policy_query_count == 1
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", count_policy_query)


def test_deny_has_priority_and_conditional_field_mask_is_row_scoped(db, admin_user, editor_user):
    project = _project(db, admin_user)
    db.add(OntologySecurityPolicy(ontology_id=project.id, name="editor rows", subject_kind="user", subject_id=editor_user.id, effect="allow", scope_kind="object_type", scope_id="observation", conditions_json=[], field_allowlist_json=["ordinal", "secret"], created_by=admin_user.id))
    db.add(OntologySecurityPolicy(ontology_id=project.id, name="mask secret on high ordinal", subject_kind="user", subject_id=editor_user.id, effect="deny", scope_kind="object_type", scope_id="observation", conditions_json=[{"field": "ordinal", "operator": ">=", "value": 10}], field_allowlist_json=["secret"], created_by=admin_user.id))
    db.add(OntologySecurityPolicy(ontology_id=project.id, name="deny ordinal zero", subject_kind="user", subject_id=editor_user.id, effect="deny", scope_kind="object_type", scope_id="observation", conditions_json=[{"field": "ordinal", "operator": "=", "value": 0}], field_allowlist_json=[], created_by=admin_user.id))
    db.commit()
    records = [{"id": "low", "properties": {"ordinal": 1, "secret": "ok"}}, {"id": "high", "properties": {"ordinal": 10, "secret": "masked"}}, {"id": "zero", "properties": {"ordinal": 0, "secret": "hidden"}}]
    projection = resolve_projection(db, AuthorizationContext(project.id, editor_user.id, "editor", object_type_id="observation"), records=records)
    visible = filter_records(projection, records)
    assert {item["id"] for item in visible} == {"low", "high"}
    assert next(item for item in visible if item["id"] == "low")["properties"]["secret"] == "ok"
    assert next(item for item in visible if item["id"] == "high")["properties"]["secret"] is None


def test_property_scope_masks_only_named_field_and_cannot_grant_object_access(db, admin_user, editor_user):
    project = _project(db, admin_user)
    db.add(OntologySecurityPolicy(
        ontology_id=project.id,
        name="object rows",
        subject_kind="user",
        subject_id=editor_user.id,
        effect="allow",
        scope_kind="object_type",
        scope_id="observation",
        conditions_json=[],
        field_allowlist_json=["ordinal", "secret"],
        created_by=admin_user.id,
    ))
    db.add(OntologySecurityPolicy(
        ontology_id=project.id,
        name="mask secret property",
        subject_kind="user",
        subject_id=editor_user.id,
        effect="deny",
        scope_kind="property",
        scope_id="secret",
        conditions_json=[],
        field_allowlist_json=[],
        created_by=admin_user.id,
    ))
    db.commit()
    projection = resolve_projection(
        db,
        AuthorizationContext(project.id, editor_user.id, "editor", object_type_id="observation"),
        records=[{"id": "obs", "properties": {"ordinal": 1, "secret": "masked"}}],
    )
    assert redact_record(projection, {"id": "obs", "properties": {"ordinal": 1, "secret": "masked"}})["properties"]["secret"] is None

    no_object_policy = _project(db, admin_user, project_id="semantic-core-test-property-only", entity_prefix="property-only-")
    db.add(OntologySecurityPolicy(
        ontology_id=no_object_policy.id,
        name="property only",
        subject_kind="user",
        subject_id=editor_user.id,
        effect="allow",
        scope_kind="property",
        scope_id="ordinal",
        conditions_json=[],
        field_allowlist_json=[],
        created_by=admin_user.id,
    ))
    db.commit()
    denied = resolve_projection(
        db,
        AuthorizationContext(no_object_policy.id, editor_user.id, "editor", object_type_id="property-only-observation"),
        records=[{"id": "obs", "properties": {"ordinal": 1}}],
    )
    assert denied.allowed is False


def test_link_visibility_is_default_deny_and_field_allow_does_not_grant_edge(db, admin_user, editor_user):
    project = _project(db, admin_user, project_id="semantic-core-link-default-deny")
    db.add(OntologySecurityPolicy(
        ontology_id=project.id, name="visible observations", subject_kind="user",
        subject_id=editor_user.id, effect="allow", scope_kind="object_type",
        scope_id="observation", conditions_json=[], field_allowlist_json=["ordinal"],
        created_by=admin_user.id,
    ))
    db.add(OntologySecurityPolicy(
        ontology_id=project.id, name="only edge weight", subject_kind="user",
        subject_id=editor_user.id, effect="allow", scope_kind="link",
        scope_id="HAS_OBSERVATION", conditions_json=[], field_allowlist_json=["weight"],
        created_by=admin_user.id,
    ))
    db.commit()
    context = AuthorizationContext(project.id, editor_user.id, "editor")
    edge = {"id": "edge-1", "link_type_id": "HAS_OBSERVATION", "type": "HAS_OBSERVATION", "properties": {"weight": 0.8, "secret": "hidden"}}

    projection = resolve_projection(db, context)
    assert redact_link_record(projection, edge, link_type_id="HAS_OBSERVATION") is None

    db.add(OntologySecurityPolicy(
        ontology_id=project.id, name="allow observation relationship", subject_kind="user",
        subject_id=editor_user.id, effect="allow", scope_kind="link",
        scope_id="HAS_OBSERVATION", conditions_json=[], field_allowlist_json=[],
        created_by=admin_user.id,
    ))
    db.commit()
    projection = resolve_projection(db, context)
    visible = redact_link_record(projection, edge, link_type_id="HAS_OBSERVATION")
    assert visible["properties"] == edge["properties"]

    db.add(OntologySecurityPolicy(
        ontology_id=project.id, name="deny confidential edge value", subject_kind="user",
        subject_id=editor_user.id, effect="deny", scope_kind="link",
        scope_id="HAS_OBSERVATION", conditions_json=[], field_allowlist_json=["secret"],
        created_by=admin_user.id,
    ))
    db.commit()
    projection = resolve_projection(db, context)
    visible = redact_link_record(projection, edge, link_type_id="HAS_OBSERVATION")
    assert visible["properties"]["weight"] == 0.8
    assert visible["properties"]["secret"] is None


def test_canonical_graph_hides_ungranted_links_and_property_definitions(db, admin_user, editor_user):
    project = _project(db, admin_user, project_id="semantic-core-canonical-graph-policy")
    with patch("app.services.v2.revision_service.get_storage_service", return_value=MemoryStorage()):
        revision = create_revision(db, project.id)
        ensure_semantic_metadata(db, project.id, revision.id, commit=True)
        schema = semantic_schema(db, project.id)
    object_types = {item["api_name"]: item for item in schema["resources"]["object_type"]}
    properties = {
        item["api_name"]: item
        for item in schema["resources"]["property"]
    }
    for type_name, field_name in (("Equipment", "equipment_id"), ("Observation", "ordinal")):
        db.add(OntologySecurityPolicy(
            ontology_id=project.id, revision_id=revision.id, name=f"allow {type_name}",
            subject_kind="user", subject_id=editor_user.id, effect="allow",
            scope_kind="object_type", scope_id=object_types[type_name]["resource_id"],
            conditions_json=[], field_allowlist_json=[properties[field_name]["resource_id"]],
            created_by=admin_user.id,
        ))
    db.commit()
    data = _canonical_ontology_data(db, project.id, user=editor_user)
    observation = next(node for node in data["nodes"] if node["id"] == "observation")
    assert [item["id"] for item in observation["properties"]["property_definitions"]] == ["ordinal"]
    assert data["edges"] == []
    assert data["summary"]["relationship_count"] == 0


def test_semantic_schema_hides_ungranted_types_fields_links_and_mapping_candidates(db, admin_user, editor_user):
    project = _project(db, admin_user, project_id="semantic-core-schema-policy")
    with patch("app.services.v2.revision_service.get_storage_service", return_value=MemoryStorage()):
        revision = create_revision(db, project.id)
        ensure_semantic_metadata(db, project.id, revision.id, commit=True)
        schema = semantic_schema(db, project.id)
    observation = next(item for item in schema["resources"]["object_type"] if item["api_name"] == "Observation")
    ordinal = next(item for item in schema["resources"]["property"] if item["api_name"] == "ordinal")
    equipment_id = next(item for item in schema["resources"]["property"] if item["api_name"] == "equipment_id")
    db.add(OntologySecurityPolicy(
        ontology_id=project.id, revision_id=revision.id, name="observation schema access",
        subject_kind="user", subject_id=editor_user.id, effect="allow",
        scope_kind="object_type", scope_id=observation["resource_id"],
        conditions_json=[], field_allowlist_json=[ordinal["resource_id"]],
        created_by=admin_user.id,
    ))
    db.add(OntologySourceMapping(
        id="pending-equipment-source", ontology_id=project.id, revision_id=revision.id,
        resource_id=equipment_id["resource_id"], source_dataset_id="private-candidate",
        source_field="internal_equipment_key", mapping_status="candidate",
        evidence_json={"checksum": "private"},
    ))
    db.commit()

    filtered = _filter_semantic_schema(db, project.id, editor_user, semantic_schema(db, project.id))
    assert [item["api_name"] for item in filtered["resources"]["object_type"]] == ["Observation"]
    assert [item["api_name"] for item in filtered["resources"]["property"]] == ["ordinal"]
    assert filtered["resources"]["link_type"] == []
    assert all(item["resource_id"] == ordinal["resource_id"] for item in filtered["source_mappings"])
    assert all(item["mapping_status"] == "confirmed" for item in filtered["source_mappings"])


def test_cursor_rejects_changed_permission_context():
    context = build_context("ontology", metadata_revision_id="r1", metadata_digest="m1", permission_digest="p1", view_id="v1")
    cursor = encode_cursor({"offset": 10}, context)
    assert decode_cursor(cursor, context) == {"offset": 10}
    changed = build_context("ontology", metadata_revision_id="r1", metadata_digest="m1", permission_digest="p2", view_id="v1")
    try:
        decode_cursor(cursor, changed)
    except DataPlaneError as exc:
        assert exc.code == "CONTEXT_MISMATCH"
    else:
        raise AssertionError("permission changes must invalidate cursors")


def test_schema_migration_shadow_apply_and_revert(db, admin_user):
    project = _project(db, admin_user)
    with patch("app.services.v2.revision_service.get_storage_service", return_value=MemoryStorage()):
        base = create_revision(db, project.id)
        ensure_semantic_metadata(db, project.id, base.id, commit=True)
        schema = semantic_schema(db, project.id)
        property_row = next(item for item in schema["resources"]["property"] if item["api_name"] == "equipment_id")
        plan = dry_run(db, project.id, {"base_revision_id": base.id, "instructions": [{"kind": "rename_display_name", "resource_id": property_row["resource_id"], "display_name": "设备标识"}]}, user_id=admin_user.id)
        applied = apply_plan(db, plan["id"], user_id=admin_user.id)
        assert applied["status"] == "applied"
        assert applied["target_revision_id"]
        assert db.query(Entity).filter(Entity.id == "equipment").one().properties["property_definitions"][0]["name"] == "设备标识"
        assert reconcile_plan(db, plan["run"]["id"])["run"]["id"] == plan["run"]["id"]
        restored = revert_plan(db, plan["run"]["id"], user_id=admin_user.id)
        assert restored["status"] == "reverted"
        assert db.query(Entity).filter(Entity.id == "equipment").one().properties["property_definitions"][0]["name"] == "设备编号"


def test_reconcile_replays_persisted_running_checkpoint_after_restart(db, admin_user):
    project = _project(db, admin_user, project_id="semantic-core-reconcile-restart")
    with patch("app.services.v2.revision_service.get_storage_service", return_value=MemoryStorage()):
        base = create_revision(db, project.id)
        ensure_semantic_metadata(db, project.id, base.id, commit=True)
        prop = next(item for item in semantic_schema(db, project.id)["resources"]["property"] if item["api_name"] == "equipment_id")
        plan = dry_run(db, project.id, {
            "base_revision_id": base.id,
            "instructions": [{"kind": "rename_display_name", "resource_id": prop["resource_id"], "display_name": "设备标识"}],
        }, user_id=admin_user.id)

    # Model a process exit after the durable running checkpoint was committed,
    # then a new request/session resuming from the run ID.
    stored_plan = db.query(SchemaMigrationPlan).filter_by(id=plan["id"]).one()
    stored_run = db.query(SchemaMigrationRun).filter_by(id=plan["run"]["id"]).one()
    stored_plan.status = "running"
    stored_plan.phase = "shadow_materialization"
    stored_run.status = "running"
    stored_run.phase = "shadow_materialization"
    stored_run.progress = 10
    db.commit()
    db.expire_all()

    resumed = reconcile_plan(db, plan["run"]["id"])
    assert resumed["status"] == "applied"
    assert resumed["run"]["status"] == "completed"
    assert resumed["run"]["id"] == plan["run"]["id"]
    assert db.query(Entity).filter(Entity.id == "equipment").one().properties["property_definitions"][0]["name"] == "设备标识"


def test_canonical_semantic_change_materializes_compatibility_projection(db, admin_user):
    project = _project(db, admin_user)
    with patch("app.services.v2.revision_service.get_storage_service", return_value=MemoryStorage()):
        base = create_revision(db, project.id)
        ensure_semantic_metadata(db, project.id, base.id, commit=True)
        schema = semantic_schema(db, project.id)
        equipment_id = next(item["resource_id"] for item in schema["resources"]["object_type"] if item["api_name"] == "Equipment")
        observation_id = next(item["resource_id"] for item in schema["resources"]["object_type"] if item["api_name"] == "Observation")

        added = apply_semantic_change(db, project.id, {
            "resource_kind": "object_type",
            "operation": "add",
            "base_revision_id": base.id,
            "payload": {"api_name": "Tool", "name_cn": "刀具", "description": "FactoryNet tool"},
        }, user_id=admin_user.id)
        tool_id = added["resource"]["resource_id"]
        assert db.query(Entity).filter(Entity.semantic_resource_id == tool_id).count() == 1

        added_property = apply_semantic_change(db, project.id, {
            "resource_kind": "property",
            "operation": "add",
            "base_revision_id": added["revision"]["id"],
            "payload": {"api_name": "toolCondition", "parent_resource_id": tool_id, "base_type": "string", "source_field": "tool_condition"},
        }, user_id=admin_user.id)
        assert any(item["api_name"] == "toolCondition" for item in added_property["semantic_schema"]["resources"]["property"])

        added_link = apply_semantic_change(db, project.id, {
            "resource_kind": "link_type",
            "operation": "add",
            "base_revision_id": added_property["revision"]["id"],
            "payload": {"api_name": "USES_TOOL", "source_resource_id": equipment_id, "target_resource_id": tool_id, "cardinality": "one-to-many"},
        }, user_id=admin_user.id)
        assert db.query(Relation).filter(Relation.type == "USES_TOOL").count() == 1
        assert any(item["api_name"] == "USES_TOOL" for item in added_link["semantic_schema"]["resources"]["link_type"])

        try:
            apply_semantic_change(db, project.id, {
                "resource_kind": "object_type",
                "operation": "delete",
                "base_revision_id": added_link["revision"]["id"],
                "resource_id": tool_id,
            }, user_id=admin_user.id)
        except SemanticCoreError as exc:
            assert exc.code == "CHANGE_BLOCKED"
        else:
            raise AssertionError("an object type referenced by a link cannot be deleted")


def test_canonical_impact_previews_add_and_delete_blockers(db, admin_user):
    project = _project(db, admin_user, project_id="semantic-core-impact")
    with patch("app.services.v2.revision_service.get_storage_service", return_value=MemoryStorage()):
        base = create_revision(db, project.id)
        ensure_semantic_metadata(db, project.id, base.id, commit=True)
        schema = semantic_schema(db, project.id)
        observation_id = next(item["resource_id"] for item in schema["resources"]["object_type"] if item["api_name"] == "Observation")
        add = semantic_change_impact(db, project.id, {"resource_kind": "object_type", "operation": "add", "base_revision_id": base.id, "payload": {"api_name": "Inspection", "name_cn": "检验"}})
        assert add["can_apply"] is True
        delete = semantic_change_impact(db, project.id, {"resource_kind": "object_type", "operation": "delete", "base_revision_id": base.id, "resource_id": observation_id})
        assert delete["can_apply"] is False
        assert any(item["kind"] == "relationships" for item in delete["impact"]["blockers"])


def test_canonical_property_update_and_delete_keep_legacy_projection_consistent(db, admin_user):
    project = _project(db, admin_user, project_id="semantic-core-property-lifecycle")
    with patch("app.services.v2.revision_service.get_storage_service", return_value=MemoryStorage()):
        base = create_revision(db, project.id)
        ensure_semantic_metadata(db, project.id, base.id, commit=True)
        schema = semantic_schema(db, project.id)
        property_id = next(item["resource_id"] for item in schema["resources"]["property"] if item["api_name"] == "equipment_id")

        updated = apply_semantic_change(db, project.id, {
            "resource_kind": "property",
            "operation": "update",
            "base_revision_id": base.id,
            "resource_id": property_id,
            "payload": {"display_name": "设备标识", "base_type": "long"},
        }, user_id=admin_user.id)
        assert updated["resource"]["display_name"] == "设备标识"
        definition = db.query(Entity).filter(Entity.id == "equipment").one().properties["property_definitions"][0]
        assert definition["name"] == "设备标识"
        assert definition["type"] == "long"

        deleted = apply_semantic_change(db, project.id, {
            "resource_kind": "property",
            "operation": "delete",
            "base_revision_id": updated["revision"]["id"],
            "resource_id": property_id,
        }, user_id=admin_user.id)
        assert deleted["resource"] == {}
        definitions = db.query(Entity).filter(Entity.id == "equipment").one().properties["property_definitions"]
        assert not any(str(item.get("id")) == "equipment_id" for item in definitions)


def test_schema_drop_property_reports_instance_value_blocker(db, admin_user):
    project = _project(db, admin_user, project_id="semantic-core-drop-property-blocker")
    db.add(EntityInstance(id="equipment-1", ontology_id=project.id, entity_id="equipment", row_identity="1", row_data={"equipment_id": "EQ-1"}))
    db.commit()
    with patch("app.services.v2.revision_service.get_storage_service", return_value=MemoryStorage()):
        base = create_revision(db, project.id)
        ensure_semantic_metadata(db, project.id, base.id, commit=True)
        schema = semantic_schema(db, project.id)
        property_id = next(item["resource_id"] for item in schema["resources"]["property"] if item["api_name"] == "equipment_id")
        plan = dry_run(db, project.id, {"base_revision_id": base.id, "instructions": [{"kind": "drop_property", "resource_id": property_id}]}, user_id=admin_user.id)
        item = plan["impact"]["items"][0]
        assert item["can_apply"] is False
        assert any(blocker["kind"] == "entity_instance_property" for blocker in item["blockers"])


def test_cast_property_transforms_postgres_instances_and_revert_restores_values(db, admin_user):
    project = _project(db, admin_user, project_id="semantic-core-cast-values")
    db.add(EntityInstance(id="equipment-cast-1", ontology_id=project.id, entity_id="equipment", row_identity="1", row_data={"equipment_id": "42"}))
    db.commit()
    with patch("app.services.v2.revision_service.get_storage_service", return_value=MemoryStorage()):
        base = create_revision(db, project.id)
        ensure_semantic_metadata(db, project.id, base.id, commit=True)
        prop = next(item for item in semantic_schema(db, project.id)["resources"]["property"] if item["api_name"] == "equipment_id")
        plan = dry_run(db, project.id, {
            "base_revision_id": base.id,
            "instructions": [{"kind": "cast_property", "resource_id": prop["resource_id"], "to_type": "integer"}],
        }, user_id=admin_user.id)
        assert plan["impact"]["items"][0]["cast"]["value_count"] == 1
        applied = apply_plan(db, plan["id"], user_id=admin_user.id)
        assert db.query(EntityInstance).filter_by(id="equipment-cast-1").one().row_data["equipment_id"] == 42
        assert applied["result"]["changed_property_value_count"] == 1
        revert_plan(db, applied["run"]["id"], user_id=admin_user.id)
        assert db.query(EntityInstance).filter_by(id="equipment-cast-1").one().row_data["equipment_id"] == "42"


def test_replace_source_is_revisioned_and_move_edits_is_reversible(db, admin_user):
    project = _project(db, admin_user, project_id="semantic-core-source-and-edits")
    with patch("app.services.v2.revision_service.get_storage_service", return_value=MemoryStorage()):
        base = create_revision(db, project.id)
        ensure_semantic_metadata(db, project.id, base.id, commit=True)
        props = semantic_schema(db, project.id)["resources"]["property"]
        prop = next(item for item in props if item["api_name"] == "equipment_id")
        target = next(item for item in props if item["api_name"] == "ordinal")
        scenario = WhatIfScenario(
            ontology_id=project.id,
            name="source ref test",
            assumptions_json=[{"property_resource_id": prop["resource_id"]}],
            rule_overrides_json={prop["resource_id"]: {"value": "EQ-9"}},
        )
        db.add(scenario)
        db.commit()
        plan = dry_run(db, project.id, {
            "base_revision_id": base.id,
            "instructions": [
                {"kind": "replace_source", "resource_id": prop["resource_id"], "source_field": "asset_code_v2", "source_dataset_id": "factorynet-v2"},
                {"kind": "move_edits", "payload": {"from_resource_id": prop["resource_id"], "to_resource_id": target["resource_id"]}},
            ],
        }, user_id=admin_user.id)
        applied = apply_plan(db, plan["id"], user_id=admin_user.id)
        target_revision = applied["target_revision_id"]
        mappings = db.query(OntologySourceMapping).filter_by(
            revision_id=target_revision, resource_id=prop["resource_id"],
        ).all()
        assert len(mappings) == 1 and mappings[0].source_field == "asset_code_v2"
        db.refresh(scenario)
        assert scenario.assumptions_json[0]["property_resource_id"] == target["resource_id"]
        assert target["resource_id"] in scenario.rule_overrides_json
        revert_plan(db, applied["run"]["id"], user_id=admin_user.id)
        db.refresh(scenario)
        assert scenario.assumptions_json[0]["property_resource_id"] == prop["resource_id"]
        assert prop["resource_id"] in scenario.rule_overrides_json


def test_source_mapping_candidates_are_revisioned_confirmable_and_many_to_many(db, admin_user):
    project = _project(db, admin_user, project_id="semantic-core-source-candidates")
    with patch("app.services.v2.revision_service.get_storage_service", return_value=MemoryStorage()):
        revision = create_revision(db, project.id)
        ensure_semantic_metadata(db, project.id, revision.id, commit=True)
        schema = semantic_schema(db, project.id)
    equipment_property = next(item for item in schema["resources"]["property"] if item["api_name"] == "equipment_id")
    observation_property = next(item for item in schema["resources"]["property"] if item["api_name"] == "ordinal")

    def add_mapping(base_revision_id, api_name, resource_id, dataset_id, table, field):
        return apply_semantic_change(
            db, project.id,
            {
                "resource_kind": "source_mapping",
                "operation": "add",
                "base_revision_id": base_revision_id,
                "payload": {
                    "api_name": api_name,
                    "mapped_resource_id": resource_id,
                    "source_dataset_id": dataset_id,
                    "source_version_id": "v1",
                    "source_table": table,
                    "source_field": field,
                    "mapping_kind": "field",
                    "mapping_status": "candidate",
                    "evidence": {"checksum": f"sum-{dataset_id}"},
                },
            },
        )

    with patch("app.services.v2.semantic_core_service.get_storage_service", return_value=MemoryStorage()), patch(
        "app.services.v2.revision_service.get_storage_service", return_value=MemoryStorage()
    ):
        first = add_mapping(revision.id, "equipmentIdSourceA", equipment_property["resource_id"], "dataset-a", "equipment_a", "equip_id")
        first_revision_id = first["revision"]["id"]
        add_mapping(first_revision_id, "equipmentIdSourceB", equipment_property["resource_id"], "dataset-b", "equipment_b", "machine_code")
        second_revision_id = db.query(OntologyProject).filter(OntologyProject.id == project.id).one().current_revision_id
        add_mapping(second_revision_id, "observationOrdinalSourceA", observation_property["resource_id"], "dataset-a", "equipment_a", "sample_ordinal")
        final_revision_id = db.query(OntologyProject).filter(OntologyProject.id == project.id).one().current_revision_id
        apply_semantic_change(
            db, project.id,
            {
                "resource_kind": "source_mapping",
                "operation": "update",
                "base_revision_id": final_revision_id,
                "resource_id": first["resource"]["resource_id"],
                "payload": {"mapping_status": "confirmed"},
            },
        )

    current_revision_id = db.query(OntologyProject).filter(OntologyProject.id == project.id).one().current_revision_id
    mappings = db.query(OntologySourceMapping).filter(
        OntologySourceMapping.revision_id == current_revision_id,
        OntologySourceMapping.source_dataset_id.in_(["dataset-a", "dataset-b"]),
    ).all()
    assert len(mappings) == 3
    confirmed = next(row for row in mappings if row.source_dataset_id == "dataset-a" and row.source_field == "equip_id")
    assert confirmed.mapping_status == "confirmed"
    assert next(row for row in mappings if row.source_dataset_id == "dataset-b").mapping_status == "candidate"
    same_source_different_object = next(row for row in mappings if row.source_dataset_id == "dataset-a" and row.source_field == "sample_ordinal")
    assert same_source_different_object.resource_id == observation_property["resource_id"]
    public_mappings = semantic_schema(db, project.id)["source_mappings"]
    assert len([item for item in public_mappings if item["source_dataset_id"] in {"dataset-a", "dataset-b"}]) == 3
    assert next(item for item in public_mappings if item["source_field"] == "equip_id")["mapping_status"] == "confirmed"
    historical = db.query(OntologySourceMapping).filter(
        OntologySourceMapping.revision_id == first_revision_id,
        OntologySourceMapping.source_dataset_id == "dataset-a",
        OntologySourceMapping.source_field == "equip_id",
    ).one()
    assert historical.mapping_status == "candidate"


def test_drop_property_archives_values_and_revert_restores_them(db, admin_user):
    project = _project(db, admin_user, project_id="semantic-core-drop-archive")
    db.add(EntityInstance(id="equipment-drop-1", ontology_id=project.id, entity_id="equipment", row_identity="1", row_data={"equipment_id": "EQ-1"}))
    db.commit()
    with patch("app.services.v2.revision_service.get_storage_service", return_value=MemoryStorage()):
        base = create_revision(db, project.id)
        ensure_semantic_metadata(db, project.id, base.id, commit=True)
        prop = next(item for item in semantic_schema(db, project.id)["resources"]["property"] if item["api_name"] == "equipment_id")
        plan = dry_run(db, project.id, {
            "base_revision_id": base.id,
            "instructions": [{"kind": "drop_property", "resource_id": prop["resource_id"], "archive_values": True}],
        }, user_id=admin_user.id)
        assert plan["impact"]["items"][0]["can_apply"] is True
        applied = apply_plan(db, plan["id"], user_id=admin_user.id)
        row_data = db.query(EntityInstance).filter_by(id="equipment-drop-1").one().row_data
        assert "equipment_id" not in row_data
        assert row_data["__retired_properties"][prop["resource_id"]]["equipment_id"] == "EQ-1"
        revert_plan(db, applied["run"]["id"], user_id=admin_user.id)
        assert db.query(EntityInstance).filter_by(id="equipment-drop-1").one().row_data["equipment_id"] == "EQ-1"


def test_rebuild_projection_uses_postgres_fact_ledger_and_reconcile_checks_target(db, admin_user):
    project = _project(db, admin_user, project_id="semantic-core-projection-rebuild")
    with patch("app.services.v2.revision_service.get_storage_service", return_value=MemoryStorage()):
        base = create_revision(db, project.id)
        ensure_semantic_metadata(db, project.id, base.id, commit=True)
        replay = TemporalReplay(id="replay-projection-test", ontology_id=project.id, status="published")
        db.add(replay)
        db.flush()
        fact = TemporalFact(
            id="fact-projection-test", replay_id=replay.id,
            subject_id="FactoryNet:Episode:E1", predicate="LATEST_OBSERVATION",
            object_id="FactoryNet:Observation:E1:1", valid_from_ordinal=1,
        )
        db.add(fact)
        db.flush()
        snapshot = DataModelSnapshot(
            id="snapshot-projection-test", ontology_id=project.id, schema_revision_id=base.id,
            replay_id=replay.id, graph_namespace="source-stream", event_count=1,
            node_count=2, edge_count=1, fact_count=1, snapshot_hash="hash1", status="published",
        )
        db.add(snapshot)
        project.current_data_snapshot_id = snapshot.id
        db.commit()

        class FakeFalkor:
            available = True

            def upsert_instances(self, _ontology, nodes, graph_namespace=None):
                assert graph_namespace
                self.node_rows = nodes
                return len(nodes)

            def upsert_relations(self, _ontology, edges, graph_namespace=None):
                assert graph_namespace
                self.edge_rows = edges
                return len(edges)

            def graph_exists(self, _ontology, _namespace):
                return True

        fake = FakeFalkor()
        with patch("app.services.v2.graph.falkordb_service.FalkorDBService", return_value=fake):
            plan = dry_run(db, project.id, {"base_revision_id": base.id, "instructions": [{"kind": "rebuild_projection"}]}, user_id=admin_user.id)
            applied = apply_plan(db, plan["id"], user_id=admin_user.id)
            assert applied["result"]["projection_rebuilds"][0]["source_fact_count"] == 1
            assert len(fake.node_rows) == 2 and len(fake.edge_rows) == 1
            assert all(value is not None for value in fake.edge_rows[0]["properties"].values())
            assert reconcile_plan(db, applied["run"]["id"])["result"]["reconciled"] is True


def test_semantic_schema_and_data_plane_http_responses_match_pydantic_contracts(client, db, admin_user, auth_headers):
    project = _project(db, admin_user, project_id="semantic-core-response-contracts")
    with patch("app.services.v2.revision_service.get_storage_service", return_value=MemoryStorage()):
        revision = create_revision(db, project.id)
        ensure_semantic_metadata(db, project.id, revision.id, commit=True)

    class UnavailableFalkor:
        available = False

    with patch("app.routers.v2.data_plane.FalkorDBService", return_value=UnavailableFalkor()):
        schema_response = client.get(f"/api/v2/ontologies/{project.id}/semantic-schema", headers=auth_headers)
        capabilities_response = client.get(f"/api/v2/ontologies/{project.id}/data-plane/capabilities", headers=auth_headers)
        status_response = client.get(f"/api/v2/ontologies/{project.id}/data-plane/status", headers=auth_headers)
    assert schema_response.status_code == 200, schema_response.text
    assert capabilities_response.status_code == 200, capabilities_response.text
    assert len(capabilities_response.json()["capabilities"]) == 3
    assert status_response.status_code == 200, status_response.text
    assert status_response.json()["projections"][1]["status"] == "unavailable"
    assert status_response.json()["result_manifest"]["metadata_revision_id"] == revision.id


def test_canonical_resource_validation_rejects_nested_struct_and_unknown_rule_reference(db, admin_user):
    project = _project(db, admin_user, project_id="semantic-core-validation")
    with patch("app.services.v2.revision_service.get_storage_service", return_value=MemoryStorage()):
        base = create_revision(db, project.id)
        ensure_semantic_metadata(db, project.id, base.id, commit=True)
        schema = semantic_schema(db, project.id)
        equipment_id = next(item["resource_id"] for item in schema["resources"]["object_type"] if item["api_name"] == "Equipment")

        try:
            apply_semantic_change(db, project.id, {
                "resource_kind": "struct",
                "operation": "add",
                "base_revision_id": base.id,
                "payload": {"api_name": "MachineState", "fields": [{"name": "nested", "type": "struct"}]},
            }, user_id=admin_user.id)
        except SemanticCoreError as exc:
            assert exc.code == "INVALID_STRUCT"
        else:
            raise AssertionError("nested Struct fields must be rejected")

        try:
            apply_semantic_change(db, project.id, {
                "resource_kind": "logic_rule",
                "operation": "add",
                "base_revision_id": base.id,
                "payload": {"api_name": "UnknownRule", "linked_entities": [equipment_id, "missing-resource"]},
            }, user_id=admin_user.id)
        except SemanticCoreError as exc:
            assert exc.code == "INVALID_RESOURCE_REFERENCE"
        else:
            raise AssertionError("logic rules cannot reference missing resources")

        created_value_type = apply_semantic_change(db, project.id, {
            "resource_kind": "value_type",
            "operation": "add",
            "base_revision_id": db.query(OntologyProject).filter(OntologyProject.id == project.id).one().current_revision_id,
            "payload": {"api_name": "SerialNumber", "base_type": "string", "constraints": {"min_length": 1}},
        }, user_id=admin_user.id)
        try:
            apply_semantic_change(db, project.id, {
                "resource_kind": "value_type",
                "operation": "update",
                "base_revision_id": created_value_type["revision"]["id"],
                "resource_id": created_value_type["resource"]["resource_id"],
                "payload": {"base_type": "integer"},
            }, user_id=admin_user.id)
        except SemanticCoreError as exc:
            assert exc.code == "VALUE_TYPE_IMMUTABLE"
        else:
            raise AssertionError("a breaking Value Type change must create a new version")
        next_version = apply_semantic_change(db, project.id, {
            "resource_kind": "value_type_version",
            "operation": "add",
            "base_revision_id": created_value_type["revision"]["id"],
            "payload": {"api_name": "SerialNumberV2", "value_type_resource_id": created_value_type["resource"]["resource_id"], "base_type": "integer"},
        }, user_id=admin_user.id)
        assert next_version["resource"]["value_type_resource_id"] == created_value_type["resource"]["resource_id"]
