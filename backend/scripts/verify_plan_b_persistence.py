"""Verify real JWT API writes survive a fresh application process.

Run inside the backend container with AUTH_MODE=jwt. Disposable fixtures are
removed in finally; this does not restart the shared development server.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


class HttpResponse:
    def __init__(self, status_code: int, body: bytes):
        self.status_code = status_code
        self.text = body.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.text)


class HttpClient:
    def __init__(self, token: str, base_url: str = "http://127.0.0.1:8000"):
        self.token = token
        self.base_url = base_url.rstrip("/")

    def request(self, method, path, json=None):
        body = None if json is None else __import__("json").dumps(json).encode("utf-8")
        deadline = time.monotonic() + 60
        while True:
            request = urllib.request.Request(
                self.base_url + path,
                data=body,
                headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"},
                method=method,
            )
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return HttpResponse(response.status, response.read())
            except urllib.error.HTTPError as exc:
                return HttpResponse(exc.code, exc.read())
            except urllib.error.URLError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(1)


def client():
    from fastapi.testclient import TestClient
    from app.main import app
    from app.database import SessionLocal
    from app.models.user import User
    from app.services.auth_service import create_access_token
    from app.config import settings
    assert settings.auth_mode == "jwt", "Run with AUTH_MODE=jwt"
    with SessionLocal() as db:
        user = db.query(User).filter_by(role="admin", is_active=True).first()
        assert user is not None
        token = create_access_token({"sub": user.id, "role": user.role})
    return TestClient(app, headers={"Authorization": "Bearer " + token})


def request(api, method, path, payload=None):
    response = api.request(method, path, json=payload)
    assert response.status_code in (200, 201), (path, response.status_code, response.text)
    return response.json()


def load(api, state, scenario=False):
    context = {"ontology_id": state["ontology_id"]}
    if scenario:
        context.update(scenario_id=state["scenario_id"], scenario_revision=1)
    return request(api, "POST", state["root"] + "/object-query/load", {
        "expression": state["expression"], "context": context, "read": {"page_size": 5},
    })["objects"][0]["properties"]["status"]


def prepare(path):
    from scripts.benchmark_plan_b_runtime import create_action_fixture
    from app.database import SessionLocal
    from app.models.entity import Entity
    ontology_id, object_id, action_id, _ = create_action_fixture()
    state = {"ontology_id": ontology_id, "object_id": object_id, "action_id": action_id,
             "root": f"/api/v2/ontologies/{ontology_id}",
             "expression": {"kind": "base", "type_ref": {"kind": "object", "api_name": "Order"}}}
    path.write_text(json.dumps(state), encoding="utf-8")
    with SessionLocal() as db:
        row = db.query(Entity).filter_by(ontology_id=ontology_id).one()
        row.properties = {"property_definitions": [{"id": "status", "type": "string"}]}
        db.commit()
    with client() as api:
        root = state["root"]
        resource = request(api, "POST", root + "/object-sets/resources", {
            "name": "Persistence exploration", "definition": {"expression": state["expression"]}})
        state["resource_id"] = resource["id"]
        payload = {"target_object_id": object_id, "parameters": {
            "target": {"object_type": "Order", "object_id": object_id}, "value": "reviewing"},
            "context": {"ontology_id": ontology_id}}
        request(api, "POST", root + f"/actions/{action_id}/preview", payload)
        state["live_run_id"] = request(api, "POST", root + f"/actions/{action_id}/run", payload)["run_id"]
        assert load(api, state) == "reviewing"
        scenario = request(api, "POST", root + "/scenarios:from-live", {"name": "Persistence scenario", "mode": "pinned"})
        state["scenario_id"] = scenario["id"]
        payload["parameters"]["value"] = "approved"
        payload.update(expected_etag=scenario["etag"], client_request_id="persistence-action")
        payload["context"].update(scenario_id=scenario["id"], scenario_revision=0)
        request(api, "POST", root + f"/actions/{action_id}/preview", payload)
        state["scenario_run_id"] = request(api, "POST", root + f"/actions/{action_id}/run", payload)["run_id"]
        request(api, "PUT", root + "/object-views/Order", {"tabs": [{
            "id": "status", "title": "Status", "properties": ["status"]}]})
        assert load(api, state, True) == "approved"
        assert load(api, state) == "reviewing"
    path.write_text(json.dumps(state), encoding="utf-8")


def verify_state(api, state, restart_scope):
    root = state["root"]
    resource = request(api, "GET", root + "/object-sets/resources/" + state["resource_id"])
    definition = request(api, "GET", root + f"/object-sets/resources/{resource['id']}/versions/1")
    assert definition["definition"]["expression"] == state["expression"]
    assert load(api, state) == "reviewing"
    assert load(api, state, True) == "approved"
    workspace = request(api, "GET", root + f"/scenarios/{state['scenario_id']}/workspace")
    assert workspace["objects"][0]["properties"]["status"] == "approved"
    assert workspace["base_objects"][0]["properties"]["status"] == "reviewing"
    logs = request(api, "GET", root + "/action-runs")
    assert {row["id"] for row in logs if row["status"] == "completed"} == {
        state["live_run_id"], state["scenario_run_id"]}
    view = request(api, "GET", root + "/object-views/Order")
    assert view["configured_version"] == 1 and not view["configuration_stale"]
    print(json.dumps({"restart_scope": restart_scope, "jwt": True,
                      "exploration_restored": True, "live_status": "reviewing",
                      "scenario_status": "approved", "completed_logs": 2,
                      "configured_view_version": 1}))


def verify(path):
    state = json.loads(path.read_text(encoding="utf-8"))
    with client() as api:
        verify_state(api, state, "fresh_application_process")


def verify_server(path):
    from app.database import SessionLocal
    from app.models.user import User
    from app.services.auth_service import create_access_token
    with SessionLocal() as db:
        user = db.query(User).filter_by(role="admin", is_active=True).first()
        assert user is not None
        token = create_access_token({"sub": user.id, "role": user.role})
    verify_state(HttpClient(token), json.loads(path.read_text(encoding="utf-8")), "docker_service")


def cleanup(path):
    from sqlalchemy import delete
    from app.database import Base, SessionLocal
    from app.main import app  # noqa: F401 -- register model tables
    from app.models.v2.query_view import QueryDataView
    from app.models.v2.object_set import ObjectSetResource
    from app.models.v2.scenario import ScenarioResource
    from app.services.v2.graph.falkordb_service import FalkorDBService
    state = json.loads(path.read_text(encoding="utf-8"))
    ontology_id = state["ontology_id"]
    with SessionLocal() as db:
        graphs = [row.graph_key for row in db.query(QueryDataView).filter_by(ontology_id=ontology_id)]
        resources = [row.id for row in db.query(ObjectSetResource).filter_by(ontology_id=ontology_id)]
        scenarios = [row.id for row in db.query(ScenarioResource).filter_by(ontology_id=ontology_id)]
        for table in reversed(Base.metadata.sorted_tables):
            condition = None
            if table.name == "ontology_projects":
                condition = table.c.id == ontology_id
            elif "ontology_id" in table.c:
                condition = table.c.ontology_id == ontology_id
            elif "scenario_id" in table.c:
                condition = table.c.scenario_id.in_(scenarios)
            elif "resource_id" in table.c:
                condition = table.c.resource_id.in_(resources)
            if condition is not None:
                db.execute(delete(table).where(condition))
        db.commit()
    graph = FalkorDBService()
    for key in graphs + [ontology_id]:
        assert graph.delete_graph(key), f"Fixture graph cleanup failed: {key}"


def main():
    if len(sys.argv) == 3:
        {"prepare": prepare, "verify": verify, "verify-server": verify_server,
         "cleanup": cleanup}[sys.argv[1]](Path(sys.argv[2]))
        return
    os.environ["AUTH_MODE"] = "jwt"
    with tempfile.TemporaryDirectory(prefix="plan-b-persistence-") as directory:
        path = Path(directory) / "state.json"
        try:
            for phase in ("prepare", "verify"):
                subprocess.run(
                    [sys.executable, "-m", "scripts.verify_plan_b_persistence", phase, str(path)],
                    check=True, timeout=180,
                )
        finally:
            if path.exists():
                cleanup(path)


if __name__ == "__main__":
    main()
