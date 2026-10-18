"""Rule evaluation through the real FalkorDB read path on a disposable graph."""
import sys
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pytest

from app.models.entity import Entity
from app.models.ontology import OntologyProject
from app.models.v2.query_view import QueryDataView
from app.services.v2.graph.falkordb_service import FalkorDBService
from app.services.v2.object_query.core import metadata_digest
from app.services.v2.object_query.metadata import load_sql_metadata

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_rule_runtime_steel import CSV  # noqa: E402


@pytest.fixture
def real_view(db, admin_user):
    service = FalkorDBService()
    if not service.available:
        pytest.fail("Real FalkorDB is required for rule runtime acceptance")
    ontology = OntologyProject(name="UCI steel falkor", domain="manufacturing", created_by=admin_user.id)
    db.add(ontology); db.flush()
    db.add(Entity(ontology_id=ontology.id, name_cn="生产日", name_en="EnergyDay", canonical_id="ontology:EnergyDay", type="EntityType",
                  properties={"property_definitions": [{"id": "total_kwh", "type": "double"}, {"id": "week_status", "type": "string"}]}))
    db.commit()
    df = pd.read_csv(CSV, encoding="utf-8-sig")
    df["day"] = pd.to_datetime(df["date"], format="%d/%m/%Y %H:%M").dt.date.astype(str)
    days = df.groupby("day").agg(total_kwh=("Usage_kWh", "sum"), week_status=("WeekStatus", "first"))
    key = f"rule-runtime-{uuid4().hex}"
    service.upsert_instances(key, [{"id": d, "entity_type": "EnergyDay",
                                    "properties": {"total_kwh": round(float(r.total_kwh), 2), "week_status": r.week_status}}
                                   for d, r in days.iterrows()])
    view = QueryDataView(ontology_id=ontology.id, source_manifest_digest="uci-851", graph_key=key, status="ready",
                         metadata_digest=metadata_digest(load_sql_metadata(db, ontology.id)), created_by=admin_user.id,
                         object_count=len(days))
    db.add(view); db.commit()
    try:
        yield ontology, view, days
    finally:
        assert service.delete_graph(key)


def test_rule_reads_pinned_view_from_real_falkordb(client, auth_headers, real_view):
    ontology, view, days = real_view
    expected = sorted(d for d, r in days.iterrows() if r.week_status == "Weekday" and round(r.total_kwh, 2) < 300)
    url = f"/api/v2/ontologies/{ontology.id}/rules"
    body = client.post(url, headers=auth_headers, json={"api_name": "quiet_weekday", "display_name": "Quiet weekday", "logic": {
        "input": {"kind": "base", "type_ref": {"api_name": "EnergyDay"}},
        "blocks": [{"kind": "filter", "where": {"kind": "and", "items": [
            {"kind": "comparison", "property": {"api_name": "week_status"}, "op": "eq", "value": "Weekday"},
            {"kind": "comparison", "property": {"api_name": "total_kwh"}, "op": "lt", "value": 300}]}}],
        "output": {"severity": "low", "evidence_properties": ["total_kwh"]}}}).json()
    rule_id = body["rule"]["id"]
    client.post(f"{url}/{rule_id}/proposals/{body['proposal']['id']}:review", headers=auth_headers, json={"decision": "approve"})
    r = client.post(f"{url}/{rule_id}/evaluate", headers=auth_headers,
                    json={"mode": "commit", "context": {"ontology_id": ontology.id, "data_view_id": view.id}})
    assert r.status_code == 200, r.text
    assert r.json()["output_count"] == len(expected) and len(expected) > 0
    alerts = client.get(f"/api/v2/ontologies/{ontology.id}/alerts?limit=500", headers=auth_headers).json()["alerts"]
    assert sorted(a["subject"]["object_id"] for a in alerts) == expected
