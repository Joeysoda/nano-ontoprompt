"""Multi-set induced graph (Vertex Search Around) on real FalkorDB."""
from uuid import uuid4

import pytest

from app.models.entity import Entity
from app.models.ontology import OntologyProject
from app.models.relation import Relation
from app.services.v2.graph.falkordb_service import FalkorDBService
from app.services.v2.object_query.data_views import build_live_view
from app.services.v2.object_query.metadata import load_sql_metadata


@pytest.fixture
def plant(db, admin_user):
    graph = FalkorDBService()
    if not graph.available:
        pytest.fail("Real FalkorDB is required for graph acceptance")
    ontology = OntologyProject(id=f"graph-{uuid4().hex[:10]}", name="Graph", domain="test", created_by=admin_user.id)
    db.add(ontology); db.flush()
    machine = Entity(ontology_id=ontology.id, name_cn="设备", name_en="Machine", canonical_id="ontology:Machine", type="EntityType",
                     properties={"property_definitions": [{"id": "model", "type": "string"}]})
    failure = Entity(ontology_id=ontology.id, name_cn="故障", name_en="FailureEvent", canonical_id="ontology:FailureEvent", type="EntityType",
                     properties={"property_definitions": [{"id": "start_timestamp", "type": "datetime"}, {"id": "code", "type": "string"}]})
    db.add_all([machine, failure]); db.flush()
    db.add(Relation(ontology_id=ontology.id, source_entity=failure.id, target_entity=machine.id, type="FAILURE_OF"))
    db.commit()
    graph.upsert_instances(ontology.id, [{"id": f"m{i}", "entity_type": "Machine", "properties": {"model": "model3"}} for i in range(3)] +
                           [{"id": f"f{i}", "entity_type": "FailureEvent", "properties": {"code": "comp4", "start_timestamp": f"2015-0{i + 1}-01T00:00:00+00:00"}}
                            for i in range(3)])
    graph.upsert_relations(ontology.id, [{"source": "f0", "target": "m0", "type": "FAILURE_OF"}, {"source": "f1", "target": "m0", "type": "FAILURE_OF"},
                                         {"source": "f2", "target": "m1", "type": "FAILURE_OF"}])
    view = build_live_view(db, graph, load_sql_metadata(db, ontology.id), ontology.id, admin_user.id, "graph-fixture", 3600)
    try:
        yield ontology, view
    finally:
        graph.delete_graph(view.graph_key)
        graph.delete_graph(ontology.id)


def test_search_around_adds_a_typed_set_and_induces_cross_type_edges(client, auth_headers, plant):
    ontology, view = plant
    url = f"/api/v2/ontologies/{ontology.id}/object-query/graph:multi"
    context = {"ontology_id": ontology.id, "consistency": "snapshot", "data_view_id": view.id}
    seed = {"kind": "static", "type_ref": {"api_name": "Machine"}, "object_ids": ["m0"]}
    around = {"kind": "traverse", "input": seed, "link": {"api_name": "FAILURE_OF", "direction": "in"}}
    r = client.post(url, headers=auth_headers, json={"sets": [seed, around], "context": context})
    assert r.status_code == 200, r.text
    body = r.json()
    assert sorted((o["object_type"], o["object_id"]) for o in body["objects"]) == [("FailureEvent", "f0"), ("FailureEvent", "f1"), ("Machine", "m0")]
    assert sorted((e["source"]["object_id"], e["relation_type"], e["target"]["object_id"]) for e in body["edges"]) == [
        ("f0", "FAILURE_OF", "m0"), ("f1", "FAILURE_OF", "m0")]
    assert len(body["set_execution_hashes"]) == 2
    bad = client.post(url, headers=auth_headers, json={"sets": [seed, {"kind": "base", "type_ref": {"api_name": "Nope"}}], "context": context})
    assert bad.status_code == 422 and bad.json()["detail"]["path"].startswith("sets[1]")
    unpinned = client.post(url, headers=auth_headers, json={"sets": [seed], "context": {"ontology_id": ontology.id}})
    assert unpinned.status_code == 422 and unpinned.json()["detail"]["code"] == "consistency_unavailable"
