"""Live smoke test for capability discovery, chained execution and derived facts."""
import json
import os
from urllib.request import Request, urlopen

base = os.environ.get("API_BASE_URL", "http://127.0.0.1:8000")
oid = os.environ.get("ONTOLOGY_ID", "frepple-d7eb98078882b234c395fd05")
root = f"{base}/api/v2/ontologies/{oid}/logic-assets"
headers = {"Content-Type": "application/json"}
if os.environ.get("API_TOKEN"):
    headers["Authorization"] = "Bearer " + os.environ["API_TOKEN"]
def call(path, payload=None):
    if payload is None:
        return json.load(urlopen(Request(root + path, headers=headers), timeout=20))
    req = Request(root + path, data=json.dumps(payload).encode(),
                  headers=headers, method="POST")
    return json.load(urlopen(req, timeout=20))

assets = call("?seed=true")["assets"]
caps = call("/capabilities?interface_key=manufacturing.cycle_time")
assert caps["count"] >= 1
by_key = {a["asset_key"]: a for a in assets}
chain = call("/execute-plan", {
    "subject_id": "public-fixture-machine-001",
    "steps": [
        {"asset_id": by_key["cycle_time_v1"]["id"],
         "inputs": {"setup_seconds": 30, "unit_seconds": 12, "quantity": 5}},
        {"asset_id": by_key["observation_summary_v1"]["id"],
         "inputs": {"values": [1, 2, 3, 4]}}
    ]
})
assert chain["status"] == "completed" and len(chain["steps"]) == 2
facts = call("/runs")
assert any(r["id"] == chain["steps"][0]["run_id"] for r in facts["runs"])
print(json.dumps({"capabilities": caps["count"], "steps": len(chain["steps"]), "status": chain["status"]}))
