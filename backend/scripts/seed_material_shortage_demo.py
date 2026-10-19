"""Seed a browsable material-shortage decision demo (synthetic L3Harris-scale data).

Creates an ontology through the HTTP API, writes the fixture's orders, BOM,
lots, reservations, commitments, supply options and fixed tasks as Ontology
objects (bulk objects have no API, so they go through the same seeder as the
real-FalkorDB acceptance test), then pins a data view and starts a baseline
run of the object-backed Study.  Open the printed URL to compare candidates,
create a Decision Package, review it as another user, apply and revert.

    python scripts/seed_material_shortage_demo.py --api http://backend:8000
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal  # noqa: E402
from app.services.v2.graph.falkordb_service import FalkorDBService  # noqa: E402
from app.services.v2.l3harris_fixture import l3harris_material_shortage_fixture  # noqa: E402
from app.services.v2.material_shortage_objects import seed_material_shortage_ontology  # noqa: E402

NAME = "缺件决策演示（合成数据）"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://backend:8000")
    args = parser.parse_args()
    graph = FalkorDBService()
    if not graph.available:
        raise SystemExit("FalkorDB is unavailable")
    with httpx.Client(base_url=args.api, timeout=120) as client:
        def ok(response):
            response.raise_for_status()
            body = response.json()
            return body.get("data", body) if isinstance(body, dict) else body

        ontology = ok(client.post("/api/v1/ontologies", json={"name": NAME, "domain": "制造",
            "description": "3 个产品、12 个订单、28 个物料的缺件与收入保护决策；数据为合成数据"}))
        with SessionLocal() as db:
            nodes = seed_material_shortage_ontology(db, graph, ontology["id"], l3harris_material_shortage_fixture())
        root = f"/api/v2/ontologies/{ontology['id']}"
        ok(client.post(f"{root}/logic-assets/seed"))
        view = ok(client.post(f"{root}/object-query/data-views", json={"source_ontology_id": ontology["id"], "source_manifest_digest": "l3-demo"}))
        template = next(t for t in ok(client.get(f"{root}/scenario-study-templates"))["items"] if t["template_id"] == "material_shortage_decision_objects")
        study = ok(client.post(f"{root}/scenario-study-definitions", json={"name": template["name"], "description": template["description"], "draft_spec": template["spec"]}))
        version = ok(client.post(f"{root}/scenario-study-definitions/{study['id']}:publish", json={"expected_etag": study["etag"], "change_note": "演示"}))
        scenario = ok(client.post(f"{root}/scenarios", json={"name": "缺件决策基线", "base_view_id": view["view_id"]}))
        case = ok(client.post(f"{root}/scenario-study-definitions/{study['id']}/cases", json={
            "study_version": version["version"], "scenario_id": scenario["id"], "name": "基线", "case_kind": "baseline"}))
        run = ok(client.post(f"{root}/scenario-study-definitions/{study['id']}/cases/{case['id']}/runs",
                             json={"expected_etag": case["etag"], "client_request_id": "seed-baseline"}))
        print(f"objects={len(nodes)} ontology={ontology['id']} study={study['id']} run={run.get('id', run)}")
        print(f"OPEN /ontologies/{ontology['id']}/scenario-studies?study={study['id']}")


if __name__ == "__main__":
    main()
