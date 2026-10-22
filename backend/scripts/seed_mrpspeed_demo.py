"""Seed the MRPSpeed-style planning demo: DataCo real demand + rule-derived supply as Ontology objects.

Originally adapted from seed_material_shortage_demo.py.

Creates an ontology through the HTTP API, writes the fixture's orders, BOM,
lots, reservations, commitments, supply options and fixed tasks as Ontology
objects (bulk objects have no API, so they go through the same seeder as the
real-FalkorDB acceptance test), then pins a data view and starts a baseline
run of the object-backed Study and installs the Workshop app from
examples/mrpspeed_app.json.  Open the printed URL to compare candidates,
create a Decision Package, review it as another user, apply and revert.

    python scripts/seed_mrpspeed_demo.py --api http://backend:8000
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal  # noqa: E402
from app.services.v2.graph.falkordb_service import FalkorDBService  # noqa: E402
from app.services.v2.mrpspeed_fixture import mrpspeed_fixture  # noqa: E402
from app.services.v2.material_shortage_objects import PLANNING_INPUT_TYPES, seed_material_shortage_ontology  # noqa: E402

NAME = "MRPSpeed 规划演示（DataCo 真实需求）"


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

        body = {"name": NAME, "domain": "制造", "description": "5 条产品线的季度收入、需求风险、物料覆盖与动作；需求来自 DataCo（CC BY 4.0），BOM/供应为规则生成的示意数据"}
        created = client.post("/api/v1/ontologies", json=body)
        if created.status_code == 409:  # names are unique; each seed gets its own ontology
            created = client.post("/api/v1/ontologies", json={**body, "name": f"{NAME} · {datetime.now():%m%d-%H%M%S}"})
        ontology = ok(created)
        with SessionLocal() as db:
            nodes = seed_material_shortage_ontology(db, graph, ontology["id"], mrpspeed_fixture(),
                                                    id_prefix=f"{ontology['id'][:8]}:", input_types=PLANNING_INPUT_TYPES)
        root = f"/api/v2/ontologies/{ontology['id']}"
        ok(client.post(f"{root}/logic-assets/seed"))
        view = ok(client.post(f"{root}/object-query/data-views", json={"source_ontology_id": ontology["id"], "source_manifest_digest": "mrpspeed-demo"}))
        template = next(t for t in ok(client.get(f"{root}/scenario-study-templates"))["items"] if t["template_id"] == "mrpspeed_planning_objects")
        study = ok(client.post(f"{root}/scenario-study-definitions", json={"name": template["name"], "description": template["description"], "draft_spec": template["spec"]}))
        version = ok(client.post(f"{root}/scenario-study-definitions/{study['id']}:publish", json={"expected_etag": study["etag"], "change_note": "演示"}))
        scenario = ok(client.post(f"{root}/scenarios", json={"name": "MRPSpeed 基线", "base_view_id": view["view_id"]}))
        case = ok(client.post(f"{root}/scenario-study-definitions/{study['id']}/cases", json={
            "study_version": version["version"], "scenario_id": scenario["id"], "name": "基线", "case_kind": "baseline"}))
        run = ok(client.post(f"{root}/scenario-study-definitions/{study['id']}/cases/{case['id']}/runs",
                             json={"expected_etag": case["etag"], "client_request_id": "seed-baseline"}))
        # The MRPSpeed pages are an app definition (data), installed like a user would import one.
        definition = json.loads((Path(__file__).resolve().parents[1] / "examples" / "mrpspeed_app.json").read_text(encoding="utf-8"))
        definition["data_sources"][0]["study_id"] = study["id"]
        app = ok(client.post(f"{root}/workshop-apps", json={"name": "MRPSpeed", "description": "季度收入、需求风险、物料覆盖与建议动作", "definition": definition}))
        print(f"objects={len(nodes)} ontology={ontology['id']} study={study['id']} run={run.get('id', run)} app={app['id']}")
        print(f"OPEN /apps/workshop/{app['id']}?ontology={ontology['id']}")


if __name__ == "__main__":
    main()
