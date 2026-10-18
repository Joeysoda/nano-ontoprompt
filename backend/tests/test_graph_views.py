"""Saved graph resources: validation, immutable versions, optimistic save, isolation."""
import uuid

import pytest

from app.models.entity import Entity
from app.models.ontology import OntologyProject
from app.models.relation import Relation
from app.models.user import User
from app.services.auth_service import hash_password


@pytest.fixture
def ontology(db, admin_user):
    item = OntologyProject(name="Graph", domain="manufacturing", created_by=admin_user.id)
    db.add(item); db.flush()
    machine = Entity(ontology_id=item.id, name_cn="设备", name_en="Machine", canonical_id="ontology:Machine", type="EntityType",
                     properties={"property_definitions": [{"id": "model", "type": "string"}]})
    failure = Entity(ontology_id=item.id, name_cn="故障", name_en="FailureEvent", canonical_id="ontology:FailureEvent", type="EntityType",
                     properties={"property_definitions": [{"id": "start_timestamp", "type": "datetime"}, {"id": "end_timestamp", "type": "datetime"}]})
    db.add_all([machine, failure]); db.flush()
    db.add(Relation(ontology_id=item.id, source_entity=failure.id, target_entity=machine.id, type="FAILURE_OF"))
    db.commit()
    return item


def definition(**kw):
    return {"sets": [
                {"kind": "static", "type_ref": {"api_name": "Machine"}, "object_ids": ["machine-001"]},
                {"kind": "traverse", "input": {"kind": "static", "type_ref": {"api_name": "Machine"}, "object_ids": ["machine-001"]},
                 "link": {"api_name": "FAILURE_OF", "direction": "in"}}],
            "data_view_id": "view-1", "layers": {"Machine": {"color": "#2563eb"}, "FailureEvent": {"color": "#dc2626", "visible": True}},
            "positions": {'["Machine","machine-001"]': [10.0, 20.0]}, "time_cursor": "2015-06-01T00:00:00+00:00", **kw}


def url(ontology): return f"/api/v2/ontologies/{ontology.id}/graph-views"


def test_graph_view_versions_are_immutable_and_saves_are_optimistic(client, auth_headers, ontology):
    created = client.post(url(ontology), headers=auth_headers, json={"name": "machine-001 failures", "definition": definition()})
    assert created.status_code == 201, created.text
    graph = created.json()
    assert graph["head_version"] == 1 and graph["head"]["definition"]["layers"]["FailureEvent"]["color"] == "#dc2626"
    same = client.post(f"{url(ontology)}/{graph['id']}/versions", headers=auth_headers, json={"expected_version": 1, "definition": definition()})
    assert same.json()["unchanged"] is True and same.json()["head_version"] == 1  # identical definition does not mint a version
    moved = definition(positions={'["Machine","machine-001"]': [50.0, 60.0]})
    v2 = client.post(f"{url(ontology)}/{graph['id']}/versions", headers=auth_headers, json={"expected_version": 1, "definition": moved, "comment": "layout"})
    assert v2.status_code == 201 and v2.json()["head_version"] == 2
    stale = client.post(f"{url(ontology)}/{graph['id']}/versions", headers=auth_headers, json={"expected_version": 1, "definition": definition(time_cursor=None)})
    assert stale.status_code == 409 and stale.json()["detail"]["head_version"] == 2
    detail = client.get(f"{url(ontology)}/{graph['id']}", headers=auth_headers).json()
    assert [h["version"] for h in detail["history"]] == [2, 1] and detail["history"][0]["comment"] == "layout"
    v1 = client.get(f"{url(ontology)}/{graph['id']}/versions/1", headers=auth_headers).json()
    assert v1["definition"]["positions"]['["Machine","machine-001"]'] == [10.0, 20.0]
    assert client.get(url(ontology), headers=auth_headers).json()["graph_views"][0]["head_version"] == 2


@pytest.mark.parametrize("patch,status", [
    ({"sets": [{"kind": "base", "type_ref": {"api_name": "Nope"}}]}, 422),        # unknown type
    ({"sets": []}, 422),                                                          # a graph needs a set
    ({"layers": {"Machine": {"color": "blue"}}}, 422),                           # not a hex colour
])
def test_invalid_definitions_are_rejected(client, auth_headers, ontology, patch, status):
    r = client.post(url(ontology), headers=auth_headers, json={"name": "bad", "definition": definition(**patch)})
    assert r.status_code == status


def test_graph_views_are_private_to_their_owner(client, auth_headers, db, ontology):
    graph = client.post(url(ontology), headers=auth_headers, json={"name": "mine", "definition": definition()}).json()
    db.add(User(id=str(uuid.uuid4()), username="other", email="o@test.com", password_hash=hash_password("other123"), role="editor"))
    db.commit()
    token = client.post("/api/v1/auth/login", json={"username": "other", "password": "other123"}).json()["data"]["access_token"]
    other = {"Authorization": f"Bearer {token}"}
    assert client.get(f"{url(ontology)}/{graph['id']}", headers=other).status_code == 404
