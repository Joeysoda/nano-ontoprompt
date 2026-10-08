from uuid import uuid4

from app.models.entity import Entity


def test_object_query_validate_returns_hash_type_and_capabilities(client, ontology, auth_headers, db):
    db.add(Entity(
        id=str(uuid4()), ontology_id=ontology["id"], name_en="Demand", name_cn="Demand",
        type="EntityType", properties={"property_definitions": [{"id": "status", "type": "string"}]},
    ))
    db.commit()
    response = client.post(
        f"/api/v2/ontologies/{ontology['id']}/object-query/validate",
        headers=auth_headers,
        json={
            "expression": {"kind": "base", "type_ref": {"kind": "object", "api_name": "Demand"}},
            "context": {"ontology_id": ontology["id"]},
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["valid"] is True
    assert len(payload["definition_hash"]) == 64
    assert payload["result_type"]["api_name"] == "Demand"
    assert payload["capabilities"]["terminals"]["load"]["enabled"] is True


def test_object_query_aggregate_requires_snapshot_view(client, ontology, auth_headers):
    response = client.post(
        f"/api/v2/ontologies/{ontology['id']}/object-query/aggregate",
        headers=auth_headers,
        json={
            "expression": {"kind": "base", "type_ref": {"kind": "object", "api_name": "Demand"}},
            "aggregations": [{"op": "count"}],
            "context": {"ontology_id": ontology["id"]},
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "consistency_unavailable"
