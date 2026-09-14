"""Run 100 reproducible Agent decision cases against real Joey graph data.

The cases use the deterministic Agent mode so failures identify integration
bugs rather than random language-model wording. Every proposal is confirmed
through the same API used by the UI and is tagged as synthetic validation.
"""
from __future__ import annotations

import json
import os
import urllib.request

BASE = os.environ.get("AGENT_TEST_URL", "http://127.0.0.1:18080/api/v2/ontologies")
OID = os.environ.get("AGENT_TEST_ONTOLOGY", "e69354c8-6db7-4da1-b551-33b41f4c0315")


def call(path: str, body=None):
    req = urllib.request.Request(BASE + "/" + OID + path,
        data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
        headers={"Content-Type": "application/json"}, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=90) as response:
        return json.load(response)


def main():
    graph = call("/reasoning/graph")
    nodes = [n for n in graph.get("nodes", []) if n.get("id")]
    runs = call("/reasoning/runs")
    if not nodes or not runs:
        raise SystemExit("需要先导入 Joey 数据并完成至少一次规则推理")
    run = call("/reasoning/runs/" + runs[0]["run_id"])
    conclusions = run.get("inferred_facts") or []
    if not conclusions:
        raise SystemExit("选定推理运行没有派生结论")
    conclusion = conclusions[0]["conclusion"]
    checks = []
    for i in range(100):
        node = nodes[i % len(nodes)]
        body = {
            "task": f"Agent 批量验证 {i + 1:03d}：请根据当前结论提出设备复核建议；这是合成测试，不代表历史决策。",
            "object_id": node["id"],
            "reasoning_run_id": run["run_id"],
            "conclusion": conclusion,
            "mode": "deterministic",
        }
        try:
            proposal = call("/agent/runs", body)
            p = proposal.get("proposal", {})
            ok = (proposal.get("status") == "proposed"
                  and proposal.get("run_id")
                  and p.get("status") == "proposed"
                  and p.get("basis", [{}])[0].get("run_id") == run["run_id"]
                  and p.get("evidence"))
            confirmed = call("/agent/runs/" + proposal["run_id"] + "/confirm", {})
            ok = ok and confirmed.get("status") == "confirmed" and confirmed.get("decision_id")
            checks.append({"case": i + 1, "object_id": node["id"], "passed": bool(ok), "agent_run_id": proposal.get("run_id"), "decision_id": confirmed.get("decision_id")})
        except Exception as exc:
            checks.append({"case": i + 1, "object_id": node["id"], "passed": False, "error": str(exc)})
    result = {"ontology_id": OID, "cases": len(checks), "passed": sum(1 for c in checks if c["passed"]), "failed": sum(1 for c in checks if not c["passed"]), "checks": checks}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
