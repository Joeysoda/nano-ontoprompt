"""HTTP and SQL boundary regressions for the branch's newly wired endpoints."""
import pytest

from app.models.entity import Entity
from app.models.relation import Relation
from app.models.v2.logic_asset import LogicAsset
from app.services.v2.object_query.metadata import load_sql_metadata


def test_mapping_row_entities_do_not_become_duplicate_types(db, ontology):
    oid = ontology["id"]
    for name in ("Demand", "Item"):
        db.add(Entity(id=name, ontology_id=oid, type="EntityType", name_en=name,
                      name_cn=name, properties={"property_definitions": [{"id": "name", "type": "string"}]}))
    for name in ("d1", "d2", "i1"):
        kind = "Demand" if name.startswith("d") else "Item"
        db.add(Entity(id=name, ontology_id=oid, type=kind, name_en=kind, name_cn=name,
                      canonical_id=f"instance:{kind}:{name}", properties={"is_instance": True, "concept_id": kind}))
    db.flush()
    for name in ("d1", "d2"):
        db.add(Relation(ontology_id=oid, source_entity=name, target_entity="i1", type="HAS_ITEM"))
    db.flush()
    metadata = load_sql_metadata(db, oid)
    assert metadata.resolve_type("object", "Demand", "test").properties["name"].data_type == "string"
    assert metadata.resolve_link("Demand", "HAS_ITEM", "out", "test")[1] == "Item"
    assert len(metadata.links) == 1


@pytest.mark.parametrize("endpoint", ["logic-assets", "manufacturing-data", "object-sets/capabilities"])
def test_new_routes_enforce_auth_and_ontology_scope(client, ontology, auth_headers, editor_user, endpoint):
    path = f'/api/v2/ontologies/{ontology["id"]}/{endpoint}'
    assert client.get(path).status_code == 403
    assert client.get(path, headers={"Authorization": "Bearer invalid"}).status_code == 401
    login = client.post('/api/v1/auth/login', json={"username": "editor", "password": "editor123"})
    token = login.json()["data"]["access_token"]
    assert client.get(path, headers={"Authorization": "Bearer " + token}).status_code == 404
    assert client.get(path, headers=auth_headers).status_code == 200
    assert client.get('/api/v2/ontologies/missing/' + endpoint, headers=auth_headers).status_code == 404


def test_logic_seed_run_status_and_negative_plan_reference(client, ontology, auth_headers, db):
    root = f'/api/v2/ontologies/{ontology["id"]}/logic-assets'
    first = client.post(root + '/seed', headers=auth_headers).json()["assets"]
    second = client.post(root + '/seed', headers=auth_headers).json()["assets"]
    assert {a["id"] for a in first} == {a["id"] for a in second}
    cycle = next(a for a in first if a["asset_key"] == 'cycle_time_v1')
    path = root + '/' + cycle["id"] + '/run'
    inputs = {"setup_seconds": 1, "unit_seconds": 2, "quantity": 3}
    good = client.post(path, headers=auth_headers, json={"inputs": inputs})
    assert good.status_code == 200 and good.json()["output"]["total_seconds"] == 7
    assert client.post(path, headers=auth_headers, json={"inputs": {}}).status_code == 422
    assert client.get(root + '/runs?limit=-1', headers=auth_headers).status_code == 422
    bad_plan = client.post(root + '/execute-plan', headers=auth_headers, json={"steps": [
        {"asset_id": cycle["id"], "inputs": inputs},
        {"asset_id": cycle["id"], "inputs": inputs, "input_bindings": {"setup_seconds": "$step.-1.output.total_seconds"}},
    ]})
    assert bad_plan.status_code == 422
    stored = db.get(LogicAsset, cycle["id"])
    stored.status = 'draft'
    db.commit()
    assert client.post(path, headers=auth_headers, json={"inputs": inputs}).status_code == 422
    stored.status = 'published'
    stored.side_effect = True
    db.commit()
    assert client.post(path, headers=auth_headers, json={"inputs": inputs}).status_code == 422
    capabilities = client.get(root + '/capabilities', headers=auth_headers).json()["capabilities"]
    assert cycle["id"] not in {item["id"] for item in capabilities}
