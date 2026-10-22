"""User-built applications: definition validation, immutable versions and data source resolution."""
from copy import deepcopy

import pytest

from app.models.v2.scenario import ScenarioRun
from app.models.v2.scenario_study import StudyDefinition


@pytest.fixture
def study(db, ontology, admin_user):
    row = StudyDefinition(ontology_id=ontology["id"], owner_id=admin_user.id, name="plan", draft_spec={})
    db.add(row)
    db.commit()
    return row


def definition(study_id):
    return {"schema": "workshop-app/v1",
            "data_sources": [{"id": "plan", "kind": "study_run", "study_id": study_id, "node": "plan"}],
            "variables": [{"id": "line"}],
            "pages": [{"id": "cockpit", "title": "Cockpit", "widgets": [
                {"id": "kpis", "type": "kpi_strip", "source": "plan", "props": {"items": [{"label": "Revenue", "path": "totals.projected_revenue", "format": "money"}]}},
                {"id": "lines", "type": "table", "source": "plan", "path": "portfolio", "props": {"columns": [{"label": "Line", "field": "name"}]},
                 "on_select": {"set_variable": "line", "field": "id", "navigate": "demand"}}],
                "layout": [{"i": "kpis", "x": 0, "y": 0, "w": 24, "h": 3}, {"i": "lines", "x": 0, "y": 3, "w": 24, "h": 10}]},
                {"id": "demand", "title": "Demand", "widgets": []}]}


def test_create_version_and_conflict(client, auth_headers, ontology, study):
    api = f"/api/v2/ontologies/{ontology['id']}/workshop-apps"
    created = client.post(api, headers=auth_headers, json={"name": "MRPSpeed", "definition": definition(study.id)})
    assert created.status_code == 201, created.text
    app = created.json()
    assert app["head_version"] == 1 and app["head"]["definition"]["pages"][0]["widgets"][1]["on_select"]["navigate"] == "demand"
    same = client.post(f"{api}/{app['id']}/versions", headers=auth_headers, json={"expected_version": 1, "definition": definition(study.id)})
    assert same.status_code == 201 and same.json()["unchanged"] is True
    changed = definition(study.id)
    changed["pages"][0]["title"] = "MRPSpeed Cockpit"
    saved = client.post(f"{api}/{app['id']}/versions", headers=auth_headers, json={"expected_version": 1, "definition": changed, "comment": "rename"})
    assert saved.json()["head_version"] == 2
    stale = client.post(f"{api}/{app['id']}/versions", headers=auth_headers, json={"expected_version": 1, "definition": definition(study.id)})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "version_conflict"
    old = client.get(f"{api}/{app['id']}", headers=auth_headers, params={"version": 1}).json()
    assert old["head"]["definition"]["pages"][0]["title"] == "Cockpit"   # versions are immutable
    assert [row["name"] for row in client.get(api, headers=auth_headers).json()["apps"]] == ["MRPSpeed"]


@pytest.mark.parametrize("mutate,message", [
    (lambda d: d["pages"][0]["widgets"][0].update(type="rocket"), "unknown widget type"),
    (lambda d: d["pages"][0]["widgets"][0]["props"].pop("items"), "missing"),
    (lambda d: d["pages"][0]["widgets"][1].update(source="nope"), "unknown data source"),
    (lambda d: d["pages"][0]["widgets"][1]["on_select"].update(set_variable="ghost"), "unknown variables"),
    (lambda d: d["pages"][0]["widgets"][1]["on_select"].update(navigate="nowhere"), "unknown page"),
    (lambda d: d["pages"][0]["layout"].append({"i": "ghost", "x": 0, "y": 0, "w": 1, "h": 1}), "unknown widget"),
    (lambda d: d["pages"].append(deepcopy(d["pages"][1])), "unique"),
])
def test_invalid_definitions_are_rejected(client, auth_headers, ontology, study, mutate, message):
    body = definition(study.id)
    mutate(body)
    response = client.post(f"/api/v2/ontologies/{ontology['id']}/workshop-apps", headers=auth_headers, json={"name": "x", "definition": body})
    assert response.status_code == 422 and message in response.text


def test_source_resolves_latest_completed_run_or_pinned_run(client, auth_headers, ontology, study, db, admin_user):
    from datetime import datetime, timedelta, timezone
    from app.models.v2.query_view import QueryDataView
    from app.models.v2.scenario import ScenarioResource
    view = QueryDataView(id="view-apps", ontology_id=ontology["id"], source_manifest_digest="s", metadata_digest="m",
                         graph_key="g-apps", status="ready", created_by=admin_user.id)
    scenario = ScenarioResource(ontology_id=ontology["id"], owner_id=admin_user.id, name="s", base_view_id=view.id)
    db.add_all([view, scenario]); db.flush()
    now = datetime.now(timezone.utc)

    def run(status, revenue, minutes):
        row = ScenarioRun(scenario_id=scenario.id, revision=0, case_key="c", logic_asset_key="study", logic_version="v1", parameters={},
                          status=status, progress=100, current_stage="done", input_digest="d", study_id=study.id, created_by=admin_user.id,
                          completed_at=now + timedelta(minutes=minutes),
                          result_manifest={"nodes": {"plan": {"outputs": {"totals": {"projected_revenue": revenue}}}}})
        db.add(row); db.flush()
        return row
    older, newer, failed = run("completed", 1, 0), run("completed", 2, 5), run("failed", 3, 9)
    db.commit()
    api = f"/api/v2/ontologies/{ontology['id']}/workshop-apps"
    app = client.post(api, headers=auth_headers, json={"name": "a", "definition": definition(study.id)}).json()
    latest = client.get(f"{api}/{app['id']}/sources/plan", headers=auth_headers).json()
    assert latest["run_id"] == newer.id and latest["outputs"]["totals"]["projected_revenue"] == 2   # failed runs are skipped
    pinned = client.get(f"{api}/{app['id']}/sources/plan", headers=auth_headers, params={"run": older.id}).json()
    assert pinned["outputs"]["totals"]["projected_revenue"] == 1
    assert failed.id not in {latest["run_id"], pinned["run_id"]}
    foreign = client.get(f"{api}/{app['id']}/sources/plan", headers=auth_headers, params={"run": "not-a-run"})
    assert foreign.status_code == 404
