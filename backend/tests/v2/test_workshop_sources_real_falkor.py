"""Workshop object-set and alert sources on real FalkorDB with the MRPSpeed dataset (DataCo real demand).

Every count and sum is checked against the committed fixture, so a source that
silently stops at one 200-row page, drops a filter or mixes snapshots fails.
"""
import uuid
from collections import Counter

import pytest

from app.models.ontology import OntologyProject
from app.models.v2.query_view import QueryDataView
from app.services.v2.graph.falkordb_service import FalkorDBService
from app.services.v2.material_shortage_objects import PLANNING_INPUT_TYPES, seed_material_shortage_ontology
from app.services.v2.mrpspeed_fixture import mrpspeed_fixture


@pytest.fixture
def plant(db, admin_user):
    graph = FalkorDBService()
    if not graph.available:
        pytest.fail("Real FalkorDB is required for Workshop object-set sources")
    ontology = OntologyProject(id=f"ws-{uuid.uuid4().hex[:12]}", name="Workshop sources", domain="manufacturing", created_by=admin_user.id)
    db.add(ontology)
    db.commit()
    fixture = mrpspeed_fixture()
    seed_material_shortage_ontology(db, graph, ontology.id, fixture, id_prefix="", input_types=PLANNING_INPUT_TYPES)
    try:
        yield {"ontology": ontology, "fixture": fixture, "api": f"/api/v2/ontologies/{ontology.id}/workshop-apps"}
    finally:
        for view in db.query(QueryDataView).filter_by(ontology_id=ontology.id).all():
            graph.delete_graph(view.graph_key)
        graph.delete_graph(ontology.id)


def app_definition(sources):
    return {"schema": "workshop-app/v1", "data_sources": sources, "variables": [],
            "pages": [{"id": "home", "title": "Home", "widgets": [
                {"id": "orders", "type": "table", "source": sources[0]["id"], "path": "objects", "props": {"columns": [{"label": "Item", "field": "item"}]}}]}]}


def test_object_set_sources_read_the_whole_type_from_one_shared_snapshot(client, auth_headers, plant):
    demands, commitments = plant["fixture"]["demands"], plant["fixture"]["supply_commitments"]
    assert len(commitments) > 200   # more than one Object Query page
    sources = [{"id": "orders", "kind": "object_set", "object_type": "CustomerOrder"},
               {"id": "bike", "kind": "object_set", "object_type": "CustomerOrder", "select": ["quantity", "product_line"],
                "filters": [{"field": "product_line", "op": "eq", "value": "BIKE"}]},
               {"id": "pos", "kind": "object_set", "object_type": "SupplyCommitment", "select": ["item", "quantity"]}]
    created = client.post(plant["api"], headers=auth_headers, json={"name": "Orders", "definition": app_definition(sources)})
    assert created.status_code == 201, created.text
    base = f"{plant['api']}/{created.json()['id']}/sources"

    orders = client.get(f"{base}/orders", headers=auth_headers)
    assert orders.status_code == 200, orders.text
    orders = orders.json()
    assert orders["kind"] == "object_set" and orders["outputs"]["count"] == len(demands) and not orders["outputs"]["truncated"]
    assert sorted(row["id"] for row in orders["outputs"]["objects"]) == sorted(row["id"] for row in demands)
    assert sum(row["quantity"] for row in orders["outputs"]["objects"]) == pytest.approx(sum(row["quantity"] for row in demands))
    assert Counter(row["product_line"] for row in orders["outputs"]["objects"]) == Counter(row["product_line"] for row in demands)

    bike = client.get(f"{base}/bike", headers=auth_headers).json()
    expected = [row for row in demands if row["product_line"] == "BIKE"]
    assert bike["outputs"]["count"] == len(expected) > 0
    assert set(bike["outputs"]["objects"][0]) == {"id", "quantity", "product_line"}   # only the selected properties

    pos = client.get(f"{base}/pos", headers=auth_headers).json()
    assert pos["outputs"]["count"] == len(commitments)
    assert sum(row["quantity"] for row in pos["outputs"]["objects"]) == pytest.approx(sum(row["quantity"] for row in commitments))
    assert orders["data_view_id"] == bike["data_view_id"] == pos["data_view_id"]   # one snapshot per app

    fresh = client.post(f"{plant['api']}/{created.json()['id']}/snapshot", headers=auth_headers)
    assert fresh.status_code == 201, fresh.text
    again = client.get(f"{base}/orders", headers=auth_headers).json()
    assert again["data_view_id"] == fresh.json()["data_view_id"] != orders["data_view_id"]


@pytest.mark.parametrize("source,code", [
    ({"id": "x", "kind": "object_set", "object_type": "Spaceship"}, "unknown_type"),
    ({"id": "x", "kind": "object_set", "object_type": "CustomerOrder", "select": ["warp_speed"]}, "unknown_property"),
    ({"id": "x", "kind": "object_set", "object_type": "CustomerOrder", "filters": [{"field": "warp_speed", "value": 9}]}, "unknown_property"),
])
def test_object_set_sources_are_checked_against_the_ontology(client, auth_headers, plant, source, code):
    response = client.post(plant["api"], headers=auth_headers, json={"name": "bad", "definition": app_definition([source])})
    assert response.status_code == 422 and response.json()["detail"]["code"] == code


def test_alert_source_lists_visible_alerts(client, auth_headers, plant):
    created = client.post(plant["api"], headers=auth_headers, json={"name": "Alerts", "definition": app_definition([{"id": "alerts", "kind": "alerts"}])})
    assert created.status_code == 201, created.text
    alerts = client.get(f"{plant['api']}/{created.json()['id']}/sources/alerts", headers=auth_headers).json()
    assert alerts["kind"] == "alerts" and alerts["outputs"] == {"alerts": [], "count": 0, "total": 0}
