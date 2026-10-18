from dataclasses import dataclass

from app.models.entity import Entity
from app.models.ontology import OntologyProject
from app.models.v2.security import OntologySecurityPolicy
from app.schemas.v2.object_query import BaseObjectSet, ExecutionContext, LoadObjectSetRequest, PropertyRef, ReadOptions, TypeRef
from app.services.v2.object_query.authorization import build_query_policy
from app.services.v2.object_query.core import GraphData, QueryCore
from app.services.v2.object_query.metadata import load_sql_metadata


@dataclass
class MemoryAdapter:
    data: GraphData

    def read(self, _ontology_id):
        return self.data


def test_query_core_uses_one_compiled_projection_and_masks_fields(db, admin_user, editor_user):
    project = OntologyProject(
        id="plan-a-query-auth",
        name="Plan A query auth",
        domain="manufacturing",
        created_by=admin_user.id,
    )
    db.add(project)
    db.add(Entity(
        id="observation",
        ontology_id=project.id,
        canonical_id="Observation",
        name_cn="观测",
        name_en="Observation",
        properties={"property_definitions": [
            {"id": "ordinal", "type": "integer"},
            {"id": "secret", "type": "string"},
        ]},
    ))
    db.add(OntologySecurityPolicy(
        ontology_id=project.id,
        name="editor observation rows",
        subject_kind="user",
        subject_id=editor_user.id,
        effect="allow",
        scope_kind="object_type",
        scope_id="Observation",
        conditions_json=[],
        field_allowlist_json=["ordinal"],
        created_by=admin_user.id,
    ))
    db.commit()

    data = GraphData({
        ("Observation", "1"): {"_type": "Observation", "_instance_id": "1", "ordinal": 1, "secret": "hidden-1"},
        ("Observation", "2"): {"_type": "Observation", "_instance_id": "2", "ordinal": 2, "secret": "hidden-2"},
    }, [])
    metadata = load_sql_metadata(db, project.id)
    request = LoadObjectSetRequest(
        expression=BaseObjectSet(kind="base", type_ref=TypeRef(api_name="Observation")),
        read=ReadOptions(select=[PropertyRef(api_name="ordinal"), PropertyRef(api_name="secret")]),
        context=ExecutionContext(ontology_id=project.id),
    )
    policy = build_query_policy(db, project.id, editor_user.id, editor_user.role)
    result = QueryCore(metadata, MemoryAdapter(data), policy).load(request)

    assert [item.object_id for item in result.objects] == ["1", "2"]
    assert all(item.properties["ordinal"] in {1, 2} for item in result.objects)
    assert all(item.properties["secret"] is None for item in result.objects)
    assert result.execution_context["permission_digest"] == policy.digest()


def test_query_core_returns_authorized_empty_instead_of_forbidden(db, admin_user, editor_user):
    project = OntologyProject(
        id="plan-a-query-empty",
        name="Plan A empty query",
        domain="manufacturing",
        created_by=admin_user.id,
    )
    db.add(project)
    db.add(Entity(
        id="observation-empty",
        ontology_id=project.id,
        canonical_id="Observation",
        name_cn="观测",
        name_en="Observation",
        properties={"property_definitions": [{"id": "ordinal", "type": "integer"}]},
    ))
    db.add(OntologySecurityPolicy(
        ontology_id=project.id,
        name="conditional editor observation rows",
        subject_kind="user",
        subject_id=editor_user.id,
        effect="allow",
        scope_kind="object_type",
        scope_id="Observation",
        conditions_json=[{"field": "ordinal", "operator": ">", "value": 100}],
        field_allowlist_json=["ordinal"],
        created_by=admin_user.id,
    ))
    db.commit()
    metadata = load_sql_metadata(db, project.id)
    request = LoadObjectSetRequest(
        expression=BaseObjectSet(kind="base", type_ref=TypeRef(api_name="Observation")),
        read=ReadOptions(select=[PropertyRef(api_name="ordinal")]),
        context=ExecutionContext(ontology_id=project.id),
    )
    data = GraphData({
        ("Observation", "1"): {"_type": "Observation", "_instance_id": "1", "ordinal": 1},
    }, [])

    result = QueryCore(
        metadata,
        MemoryAdapter(data),
        build_query_policy(db, project.id, editor_user.id, editor_user.role),
    ).load(request)

    assert result.objects == []
    assert result.status == "empty"
