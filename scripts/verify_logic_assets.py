"""Live verifier for the local logic asset API (run inside the backend container)."""
import json
import sys
import os
from urllib.request import Request, urlopen

base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
oid = sys.argv[2] if len(sys.argv) > 2 else "frepple-d7eb98078882b234c395fd05"
root = f"{base}/api/v2/ontologies/{oid}/logic-assets"
headers = {"Content-Type": "application/json"}
if os.environ.get("API_TOKEN"):
    headers["Authorization"] = "Bearer " + os.environ["API_TOKEN"]

def get(url): return json.load(urlopen(Request(url, headers=headers), timeout=20))
def post(url, payload):
    req = Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
    return json.load(urlopen(req, timeout=20))

assets = get(root + "?seed=true")["assets"]
samples = {
    "release_gate_v1":{"tool_condition":"good","visual_inspection":"pass","machining_finalized":True,"clamp_pressure":40},
    "cycle_time_v1":{"setup_seconds":120,"unit_seconds":18,"quantity":10},
    "observation_summary_v1":{"values":[12,13,14,15,16]},
    "tool_wear_risk_v1":{"temperature":92,"vibration":3.1,"pressure":11},
    "resource_assignment_v1":{"tasks":[{"id":"op-1","duration":3,"eligible_resources":["a","b"]}],"resources":[{"id":"a","capacity":8},{"id":"b","capacity":8}]},
    "priority_plan_v1":{"tasks":[{"id":"cut","duration":2,"priority":1,"resource":"a"},{"id":"inspect","duration":1,"priority":2,"resource":"q","predecessors":["cut"]}]},
}
results=[]
for asset in assets:
    output=post(root+f"/{asset['id']}/run", {"inputs":samples[asset["asset_key"]]})
    assert output["status"] == "completed" and output["output"] is not None
    results.append({"asset_key":asset["asset_key"],"run_id":output["run_id"],"version":output["asset"]["version"]})
runs=get(root+"/runs")["runs"]
assert len(runs)>=6
print(json.dumps({"assets":len(assets),"executions":results,"saved_runs":len(runs)},ensure_ascii=False))
