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
import json
import secrets
import sys
import time
from datetime import datetime
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
    parser.add_argument("--two-user-demo", action="store_true", help="Create separate JWT author/reviewer accounts; emit private JSON on stdout")
    parser.add_argument("--wait-seconds", type=int, default=240)
    parser.add_argument("--public-url", default="http://127.0.0.1:15173")
    args = parser.parse_args()
    credentials = None
    if args.two_user_demo:
        from app.config import settings
        from app.models.user import User
        from app.services.auth_service import hash_password
        if settings.auth_mode != "jwt":
            raise SystemExit("The two-user demo requires AUTH_MODE=jwt")
        suffix = secrets.token_hex(4)
        credentials = {}
        with SessionLocal() as db:
            for role in ("author", "reviewer"):
                username = f"decision-{role}-{suffix}"
                password = secrets.token_urlsafe(24)
                db.add(User(username=username, email=f"{username}@demo.ontoact.invalid",
                            password_hash=hash_password(password), role="admin"))
                credentials[role] = {"username": username, "password": password}
            db.commit()
    graph = FalkorDBService()
    if not graph.available:
        raise SystemExit("FalkorDB is unavailable")
    with httpx.Client(base_url=args.api, timeout=120) as client:
        def ok(response):
            response.raise_for_status()
            body = response.json()
            return body.get("data", body) if isinstance(body, dict) else body

        if credentials:
            token = ok(client.post("/api/v1/auth/login", json=credentials["author"]))["access_token"]
            client.headers["Authorization"] = f"Bearer {token}"

        body = {"name": NAME, "domain": "制造", "description": "3 个产品、12 个订单、28 个物料的缺件与收入保护决策；数据为合成数据"}
        created = client.post("/api/v1/ontologies", json=body)
        if created.status_code == 409:  # names are unique; each seed gets its own ontology
            created = client.post("/api/v1/ontologies", json={**body, "name": f"{NAME} · {datetime.now():%m%d-%H%M%S}"})
        ontology = ok(created)
        with SessionLocal() as db:
            nodes = seed_material_shortage_ontology(db, graph, ontology["id"], l3harris_material_shortage_fixture(),
                                                    id_prefix=f"{ontology['id'][:8]}:")
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
        deadline = time.monotonic() + args.wait_seconds
        while True:
            result = ok(client.get(f"{root}/scenario-study-runs/{run['id']}"))
            if result["status"] == "completed":
                break
            if result["status"] in ("failed", "cancelled"):
                raise SystemExit(f"Baseline did not complete: {result.get('error')}")
            if time.monotonic() >= deadline:
                raise SystemExit("Timed out waiting for baseline; check celery_worker logs")
            time.sleep(1)
        outputs = result["result_manifest"]["nodes"]["decision"]["outputs"]
        combined = next(row for row in outputs["candidate_actions"] if row["kind"] == "combined")
        if outputs["revenue_at_risk"] != 550000 or combined["revenue_at_risk"] != 0 or combined["incremental_cost"] != 3720:
            raise SystemExit("Seeded baseline does not match the known-answer fixture")
        url = f"{args.public_url.rstrip('/')}/ontologies/{ontology['id']}/scenario-studies?study={study['id']}&run={run['id']}"
        if credentials:
            print(json.dumps({"url": url, "ontology_id": ontology["id"], "study_id": study["id"], "run_id": run["id"],
                              "accounts": credentials, "baseline_revenue_at_risk": 550000,
                              "combined_incremental_cost": 3720}, indent=2))
        else:
            print(f"objects={len(nodes)} ontology={ontology['id']} study={study['id']} run={run['id']}")
            print(f"OPEN {url}")


if __name__ == "__main__":
    main()
