"""Workshop object-set and alert sources on real FalkorDB with the MRPSpeed dataset (DataCo real demand).

Every count and sum is checked against the committed fixture, so a source that
silently stops at one 200-row page, drops a filter or mixes snapshots fails.
"""
import uuid
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone

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
    assert alerts["kind"] == "alerts" and alerts["outputs"]["alerts"] == [] and alerts["outputs"]["count"] == 0 and not alerts["outputs"]["truncated"]


# --- aggregate sources and the server-side time range ------------------------------------------------------------

EVENT_COUNT = 10_050   # more than one 10,000-row graph page
SITES = {"North": 52.0, "South": -33.0, "East": 35.0}
BASE = datetime(2020, 1, 1, tzinfo=timezone.utc)


def event(i):
    # Ids mix upper and lower case: FalkorDB orders strings byte-wise but compares them case-insensitively,
    # which once made the paged graph scan re-read rows ("Duplicate object identity").
    site = list(SITES)[i % 3]
    return {"id": f"{'Ev' if i % 2 else 'ev'}-{i:05d}", "entity_type": "Event",
            "properties": {"at": (BASE + timedelta(hours=i)).isoformat(), "site_id": f"site-{site}", "amount": float(i % 7), "late": float(i % 5 == 0)}}


@pytest.fixture
def events(db, admin_user):
    from app.models.entity import Entity
    from app.models.entity_instance import EntityInstance
    graph = FalkorDBService()
    if not graph.available:
        pytest.fail("Real FalkorDB is required for aggregate sources")
    ontology = OntologyProject(id=f"ev-{uuid.uuid4().hex[:12]}", name="Events", domain="manufacturing", created_by=admin_user.id)
    db.add(ontology)
    nodes = {"Site": [{"id": f"site-{name}", "entity_type": "Site", "properties": {"name": name, "lat": lat}} for name, lat in SITES.items()],
             "Event": [event(i) for i in range(EVENT_COUNT)]}
    types = {"Site": {"name": "string", "lat": "double"}, "Event": {"at": "timestamp", "site_id": "string", "amount": "double", "late": "double"}}
    for name, props in types.items():
        entity = Entity(ontology_id=ontology.id, name_cn=name, name_en=name, canonical_id=f"ontology:{name}", type="EntityType",
                        properties={"property_definitions": [{"id": key, "type": kind} for key, kind in props.items()]})
        db.add(entity); db.flush()
        db.bulk_save_objects([EntityInstance(id=f"{ontology.id}:{row['id']}", entity_id=entity.id, ontology_id=ontology.id, row_identity=row["id"],
                                             row_data=row["properties"]) for row in nodes[name]])
    db.commit()
    for name in types:
        for start in range(0, len(nodes[name]), 5000):
            graph.upsert_instances(ontology.id, nodes[name][start:start + 5000])
    try:
        yield {"ontology": ontology, "api": f"/api/v2/ontologies/{ontology.id}/workshop-apps"}
    finally:
        for view in db.query(QueryDataView).filter_by(ontology_id=ontology.id).all():
            graph.delete_graph(view.graph_key)
        graph.delete_graph(ontology.id)


PER_SITE = {"id": "perSite", "kind": "aggregate", "object_type": "Event", "group_by": ["site_id"], "time_field": "at",
            "metrics": [{"key": "events", "op": "count"}, {"key": "amount", "op": "sum", "property": "amount"}, {"key": "late_rate", "op": "avg", "property": "late"}],
            "join": {"object_type": "Site", "on": "site_id", "select": ["name", "lat"]},
            "bands": [{"field": "late_rate", "into": "level", "cuts": [[0.21, "high"]], "otherwise": "normal"}]}


def expected(days):
    rows = [event(i)["properties"] for i in range(EVENT_COUNT)]
    if days:
        anchor = BASE + timedelta(hours=EVENT_COUNT - 1)
        rows = [row for row in rows if datetime.fromisoformat(row["at"]) > anchor - timedelta(days=days)]
    out = {}
    for row in rows:
        group = out.setdefault(row["site_id"], {"events": 0, "amount": 0.0, "late": 0.0})
        group["events"] += 1; group["amount"] += row["amount"]; group["late"] += row["late"]
    return {site: {"events": g["events"], "amount": g["amount"], "late_rate": g["late"] / g["events"]} for site, g in out.items()}


def test_aggregate_source_groups_on_the_server_for_each_time_range(client, auth_headers, events):
    created = client.post(events["api"], headers=auth_headers, json={"name": "Events", "definition": app_definition([PER_SITE])})
    assert created.status_code == 201, created.text
    url = f"{events['api']}/{created.json()['id']}/sources/perSite"
    for time_range, days in (("all", None), ("7d", 7), ("30d", 30)):
        response = client.get(url, headers=auth_headers, params={"range": time_range})
        assert response.status_code == 200, response.text
        groups = {row["site_id"]: row for row in response.json()["outputs"]["groups"]}
        truth = expected(days)
        assert set(groups) == set(truth)
        for site, want in truth.items():
            got = groups[site]
            assert got["events"] == want["events"] and got["amount"] == pytest.approx(want["amount"]) and got["late_rate"] == pytest.approx(want["late_rate"])
            assert got["name"] == site.removeprefix("site-") and got["lat"] == SITES[got["name"]]   # joined from the Site object
            assert got["level"] == ("high" if want["late_rate"] >= 0.21 else "normal")
        if days:
            assert sum(row["events"] for row in groups.values()) == days * 24   # one event an hour, counted back from the latest
            assert response.json()["outputs"]["time"]["anchor"].startswith((BASE + timedelta(hours=EVENT_COUNT - 1)).isoformat()[:19])


def test_object_set_time_range_is_applied_on_the_server(client, auth_headers, events):
    source = {"id": "recent", "kind": "object_set", "object_type": "Event", "select": ["at", "site_id"], "time_field": "at"}
    created = client.post(events["api"], headers=auth_headers, json={"name": "Recent", "definition": app_definition([source])})
    assert created.status_code == 201, created.text
    week = client.get(f"{events['api']}/{created.json()['id']}/sources/recent", headers=auth_headers, params={"range": "7d"}).json()["outputs"]
    assert week["count"] == 7 * 24 and not week["truncated"] and week["time"]["range"] == "7d"


@pytest.mark.parametrize("mutate,code", [
    (lambda s: s["join"].update(on="amount"), "join.on must be one of group_by"),
    (lambda s: s["metrics"].append({"key": "x", "op": "sum", "property": "weight"}), "unknown_property"),
    (lambda s: s["metrics"].append({"key": "y", "op": "count", "property": "amount"}), "count takes no property"),
    (lambda s: s["join"].update(select=["altitude"]), "unknown_property"),
])
def test_invalid_aggregate_sources_are_rejected(client, auth_headers, events, mutate, code):
    source = deepcopy(PER_SITE)
    mutate(source)
    response = client.post(events["api"], headers=auth_headers, json={"name": "bad", "definition": app_definition([source])})
    assert response.status_code == 422 and code in response.text
