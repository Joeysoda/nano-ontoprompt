"""Verify branch APIs against an explicitly supplied, isolated JWT test server.

Import the pinned fixture into the test database first. This probe creates one
editor account and logic runs; never point it at a production database.
"""
import argparse
import json
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default="admin123")
    args = parser.parse_args()
    checks = []

    def call(method, path, body=None, token=None, expected=200):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        request = Request(args.base_url + path, method=method, headers=headers,
                          data=json.dumps(body).encode() if body is not None else None)
        try:
            response = urlopen(request, timeout=30)
        except HTTPError as error:
            response = error
        payload = json.load(response)
        assert response.status == expected, (method, path, response.status, payload)
        checks.append({"method": method, "path": path, "status": response.status})
        return payload

    health = call("GET", "/health")
    assert health["auth_mode"] == "jwt" and health["db"] == "ok"
    spec = call("GET", "/openapi.json")
    for suffix in ("manufacturing-data", "logic-assets", "object-sets/load"):
        assert any(path.endswith(suffix) for path in spec["paths"])
    login = call("POST", "/api/v1/auth/login", {"username": args.username, "password": args.password})
    admin = login["data"]["access_token"]
    suffix = uuid.uuid4().hex[:12]
    username = "acceptance_" + suffix
    call("POST", "/api/v1/users", {"username": username, "email": username + "@example.com",
         "password": "acceptance-test-password", "role": "editor"}, admin, expected=201)
    editor = call("POST", "/api/v1/auth/login", {"username": username,
                  "password": "acceptance-test-password"})["data"]["access_token"]
    oid = "frepple-d7eb98078882b234c395fd05"
    root = "/api/v2/ontologies/" + oid
    ontology = call("GET", "/api/v1/ontologies/" + oid, token=admin)["data"]
    assert ontology["id"] == oid and ontology["current_revision_id"]
    for endpoint in ("/manufacturing-data", "/logic-assets", "/object-sets/capabilities"):
        call("GET", root + endpoint, expected=403)
        call("GET", root + endpoint, token="invalid", expected=401)
        call("GET", root + endpoint, token=editor, expected=404)
        call("GET", root + endpoint, token=admin)
        call("GET", "/api/v2/ontologies/missing" + endpoint, token=admin, expected=404)
    owned = call("POST", "/api/v1/ontologies", {"name": "Acceptance editor ontology", "domain": "制造"}, editor, expected=201)["data"]["id"]
    for endpoint in ("/manufacturing-data", "/logic-assets", "/object-sets/capabilities"):
        call("GET", "/api/v2/ontologies/" + owned + endpoint, token=editor)
    report = call("GET", root + "/manufacturing-data", token=admin)
    assert report["import_status"] == "completed"
    assert len(report["nodes"]) == 363 and len(report["edges"]) == 903
    assert report["sha256"] == "d7eb98078882b234c395fd053c5f6fbda33810cb90add2adb4bf7d62f28637ef"
    call("GET", root + "/manufacturing-data?object_id=foreign", token=admin, expected=404)
    assets = call("POST", root + "/logic-assets/seed", {}, admin)["assets"]
    assert len(assets) == 6
    again = call("POST", root + "/logic-assets/seed", {}, admin)["assets"]
    assert {a["id"] for a in assets} == {a["id"] for a in again}
    asset = next(a for a in assets if a["asset_key"] == "cycle_time_v1")
    result = call("POST", root + "/logic-assets/" + asset["id"] + "/run",
                  {"inputs": {"setup_seconds": 120, "unit_seconds": 18, "quantity": 10}}, admin)
    assert result["output"]["total_seconds"] == 300
    call("POST", root + "/logic-assets/" + asset["id"] + "/run", {"inputs": {}}, admin, expected=422)
    runs = call("GET", root + "/logic-assets/runs", token=admin)["runs"]
    assert any(run["id"] == result["run_id"] for run in runs)
    base = {"kind": "base", "type_ref": {"kind": "object", "api_name": "Demand"}}
    query = {"expression": base, "context": {"ontology_id": oid, "consistency": "live"},
             "read": {"page_size": 2, "order_by": [{"property": {"api_name": "name"}}]}}
    path = root + "/object-sets/load"
    result = call("POST", path, query, admin)
    assert len(result["objects"]) == 2 and result["page"]["has_more"]
    assert all(item["object_id"].startswith(oid + ":") for item in result["objects"])
    for status, expected_count in (("open", 16), ("nonexistent", 0)):
        filtered = {**query, "read": {"page_size": 200}, "expression": {
            "kind": "filter", "input": base, "where": {"kind": "comparison",
            "property": {"api_name": "status"}, "op": "eq", "value": status}}}
        assert len(call("POST", path, filtered, admin)["objects"]) == expected_count
    traversed = {**query, "expression": {"kind": "traverse", "input": base,
                 "link": {"api_name": "HAS_ITEM"}}}
    assert call("POST", path, traversed, admin)["objects"]
    for invalid, code in (
        ({**query, "context": {"ontology_id": "foreign", "consistency": "live"}}, "ontology_context_mismatch"),
        ({**query, "context": {"ontology_id": oid, "consistency": "live", "revision_policy": "pinned", "revision_id": "old"}}, "revision_unavailable"),
        ({**query, "read": {"page_token": "old"}}, "pagination_unavailable"),
        ({**query, "expression": {"kind": "base", "type_ref": {"kind": "object", "api_name": "Unknown"}}}, "unknown_type"),
    ):
        assert call("POST", path, invalid, admin, expected=422)["detail"]["code"] == code
    call("POST", path, {**query, "read": {"page_size": 0}}, admin, expected=422)
    call("POST", path, query, expected=403)
    call("POST", path, query, editor, expected=404)
    print(json.dumps({"passed": True, "checks": checks, "fixture": {"ontology_id": oid,
                     "nodes": 363, "edges": 903, "sha256": report["sha256"],
                     "ontology_revision": ontology["current_revision_id"], "source_revision": report["source_revision"]}}, indent=2))


if __name__ == "__main__":
    main()
