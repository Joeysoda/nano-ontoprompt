"""End-to-end: L3Harris-scale Study run -> Decision Package -> governed review.

Uses the real Study compiler, the real executor with contract validation, the
real run-summary API and the real Decision Package router.
"""
import os
import threading
import uuid
from time import monotonic

import pytest

from app.services.auth_service import hash_password
from app.models.user import User
from app.models.v2.logic_asset import LogicAsset
from app.models.v2.query_view import QueryDataView
from app.models.v2.scenario import ScenarioResource, ScenarioRun, ScenarioRunStage
from app.models.v2.scenario_study import RunPlan
from app.services.v2.logic_assets import ASSET_DEFINITIONS
from app.services.v2.scenario_study_templates import templates
from app.tasks.v2.scenario import _execute_study_run


def _completed_run(client, auth_headers, ontology_id, db, admin_user):
    definition = next(row for row in ASSET_DEFINITIONS if row["asset_key"] == "material_shortage_decision_v1")
    view = QueryDataView(id=f"view-{uuid.uuid4().hex[:8]}", ontology_id=ontology_id, source_manifest_digest="source-v1",
                         metadata_digest="metadata-v1", graph_key="graph-material", status="ready", created_by=admin_user.id)
    scenario = ScenarioResource(ontology_id=ontology_id, owner_id=admin_user.id, name="Shortage scenario", base_view_id=view.id)
    db.add_all([LogicAsset(ontology_id=ontology_id, asset_key=definition["asset_key"], name=definition["name"], kind=definition["kind"],
                           implementation=definition["implementation"], version="1.0.0", input_schema=definition["input_schema"],
                           output_schema=definition["output_schema"], bindings=definition["bindings"], status="published"), view, scenario])
    db.commit()
    db.refresh(scenario)
    spec = next(row for row in templates() if row["template_id"] == "material_shortage_decision")["spec"]
    base = f"/api/v2/ontologies/{ontology_id}/scenario-study-definitions"
    study = client.post(base, headers=auth_headers, json={"name": "L3 shortage", "draft_spec": spec}).json()
    published = client.post(f"{base}/{study['id']}:publish", headers=auth_headers, json={"expected_etag": study["etag"]})
    assert published.status_code == 201, published.text
    case = client.post(f"{base}/{study['id']}/cases", headers=auth_headers,
                       json={"study_version": 1, "scenario_id": scenario.id, "name": "Baseline", "case_kind": "baseline"}).json()
    compiled = client.post(f"{base}/{study['id']}/cases/{case['id']}:compile", headers=auth_headers)
    assert compiled.status_code == 201, compiled.text
    plan = db.get(RunPlan, compiled.json()["id"])
    run = ScenarioRun(scenario_id=scenario.id, revision=0, case_key=case["name"], logic_asset_key=f"study:{study['id']}",
                      logic_version="scenario-study/v1", parameters={"run_plan_id": plan.id}, status="queued", progress=0,
                      current_stage="queued", input_digest=plan.plan_hash, study_id=study["id"], case_id=case["id"],
                      run_plan_id=plan.id, created_by=admin_user.id)
    db.add(run)
    db.flush()
    db.add(ScenarioRunStage(run_id=run.id, sequence=1, stage_key="node_000"))
    db.commit()
    db.refresh(run)
    _execute_study_run(db, run, monotonic())
    db.refresh(run)
    assert run.status == "completed", run.error_json
    return scenario, run


def test_l3_run_feeds_governed_decision_package(client, auth_headers, ontology, db, admin_user):
    ontology_id = ontology["id"]
    scenario, run = _completed_run(client, auth_headers, ontology_id, db, admin_user)
    summary = client.get(f"/api/v2/ontologies/{ontology_id}/scenario-study-runs/{run.id}", headers=auth_headers).json()
    outputs = summary["result_manifest"]["nodes"]["decision"]["outputs"]
    assert outputs["revenue_at_risk"] == 550000
    assert summary["result_manifest"]["metrics"][0]["value"] == 550000
    assert summary["result_manifest"]["constraints"][0]["satisfied"] is False
    artifact = next(row for row in summary["artifacts"] if row["artifact_type"] == "node:decision")
    assert summary["scenario_revision"] == 0

    combined = next(row for row in outputs["candidate_actions"] if row["kind"] == "combined")
    packages = f"/api/v2/ontologies/{ontology_id}/decision-packages"
    body = {"scenario_id": scenario.id, "scenario_revision": 0, "run_id": run.id, "title": combined["label"],
            "rationale": "Protects all 550k at 3,720 incremental cost; no order is harmed.",
            "actions": [{"action_type_id": row["action_type"], "invocation": row, "expected_edits": [row]} for row in combined["actions"]],
            "evidence": [{"evidence_type": "artifact", "reference": {"artifact_id": artifact["id"], "candidate_id": combined["id"]},
                          "content_digest": artifact["content_digest"]}]}
    tampered = {**body, "evidence": [{**body["evidence"][0], "content_digest": "0" * 64}]}
    assert client.post(packages, headers=auth_headers, json=tampered).status_code == 422
    created = client.post(packages, headers=auth_headers, json=body)
    assert created.status_code == 200, created.text
    package = created.json()
    assert package["status"] == "draft" and len(package["actions"]) == len(combined["actions"])

    def move(item, event, headers, request_id=None, etag=None, reason=""):
        return client.post(f"{packages}/{item['id']}/transition", headers=headers,
                           json={"event": event, "expected_etag": etag or item["etag"], "request_id": request_id or uuid.uuid4().hex, "reason": reason})

    submitted = move(package, "submit", auth_headers, request_id="submit-1")
    assert submitted.json()["status"] == "in_review"
    replay = move(package, "submit", auth_headers, request_id="submit-1")  # idempotent retry with the stale etag
    assert replay.status_code == 200 and replay.json()["etag"] == submitted.json()["etag"]
    package = submitted.json()

    own = move(package, "approve", auth_headers)
    assert own.status_code == 403 and own.json()["detail"]["code"] == "self_review_forbidden"

    reviewer = User(id=str(uuid.uuid4()), username="reviewer", email="reviewer@test.com", password_hash=hash_password("review123"), role="admin")
    db.add(reviewer)
    db.commit()
    token = client.post("/api/v1/auth/login", json={"username": "reviewer", "password": "review123"}).json()["data"]["access_token"]
    reviewer_headers = {"Authorization": f"Bearer {token}"}
    assert move(package, "approve", reviewer_headers, etag=package["etag"] + 5).json()["detail"]["code"] == "stale_etag"
    approved = move(package, "approve", reviewer_headers)
    assert approved.status_code == 200 and approved.json()["status"] == "approved"
    assert approved.json()["reviewed_by"] == reviewer.id
    package = approved.json()

    db.refresh(scenario)
    scenario.head_revision = 1
    db.commit()
    # Apply cannot be faked through a plain transition; it must go through :apply.
    assert move(package, "begin_apply", reviewer_headers).status_code == 422
    stale = client.post(f"{packages}/{package['id']}:apply", headers=reviewer_headers,
                        json={"expected_etag": package["etag"], "request_id": "apply-1"})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "stale_scenario_revision"
    events = client.get(packages, headers=auth_headers).json()["packages"][0]["events"]
    assert [row["event_type"] for row in events] == ["created", "submit", "approve"]


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL", "").startswith("postgresql"), reason="row locks need real PostgreSQL")
def test_concurrent_reviews_have_exactly_one_winner(client, auth_headers, ontology, db, admin_user):
    from fastapi import HTTPException
    from sqlalchemy.orm import sessionmaker
    from app.routers.v2 import decisions
    from app.routers.v2.decisions import PackageTransitionInput, transition_package

    ontology_id = ontology["id"]
    scenario, run = _completed_run(client, auth_headers, ontology_id, db, admin_user)
    summary = client.get(f"/api/v2/ontologies/{ontology_id}/scenario-study-runs/{run.id}", headers=auth_headers).json()
    artifact = next(row for row in summary["artifacts"] if row["artifact_type"] == "node:decision")
    candidate = summary["result_manifest"]["nodes"]["decision"]["outputs"]["candidate_actions"][0]
    packages = f"/api/v2/ontologies/{ontology_id}/decision-packages"
    package = client.post(packages, headers=auth_headers, json={
        "scenario_id": scenario.id, "scenario_revision": 0, "run_id": run.id, "title": "t", "rationale": "r",
        "actions": [{"action_type_id": row["action_type"], "invocation": row} for row in candidate["actions"]],
        "evidence": [{"evidence_type": "artifact", "reference": {"artifact_id": artifact["id"]}, "content_digest": artifact["content_digest"]}]}).json()
    package = client.post(f"{packages}/{package['id']}/transition", headers=auth_headers,
                          json={"event": "submit", "expected_etag": package["etag"], "request_id": "submit"}).json()
    reviewers = [User(id=str(uuid.uuid4()), username=f"r{i}", email=f"r{i}@t.com", password_hash="x", role="admin") for i in range(2)]
    db.add_all(reviewers)
    db.commit()

    TestSession = sessionmaker(bind=db.get_bind())
    barrier, results = threading.Barrier(2), []

    def review(user):
        session = TestSession()
        try:
            barrier.wait()
            transition_package(ontology_id, package["id"], PackageTransitionInput(
                event="approve", expected_etag=package["etag"], request_id=f"approve-{user.username}"), db=session, user=session.get(User, user.id))
            results.append("ok")
        except HTTPException as exc:
            results.append(exc.detail["code"])
        except Exception as exc:  # e.g. a unique-constraint race without the lock
            results.append(type(exc).__name__)
        finally:
            session.close()

    # Hold each request just after it has read the package, so both would see the
    # same ETag unless the row lock makes the second wait for the first commit.
    overlap, real_transition = threading.Barrier(2), decisions.transition

    def held_transition(*args, **kwargs):
        try:
            overlap.wait(timeout=2)
        except threading.BrokenBarrierError:
            pass
        return real_transition(*args, **kwargs)

    decisions.transition = held_transition
    threads = [threading.Thread(target=review, args=(user,)) for user in reviewers]
    [thread.start() for thread in threads]
    [thread.join(30) for thread in threads]
    decisions.transition = real_transition
    assert sorted(results) == ["ok", "stale_etag"]
    final = client.get(packages, headers=auth_headers).json()["packages"][0]
    assert final["status"] == "approved" and [row["event_type"] for row in final["events"]].count("approve") == 1
