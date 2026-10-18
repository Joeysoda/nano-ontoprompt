"""Rule runtime contract tests: governance, deterministic evaluation, alerts."""
import random
import uuid
from copy import deepcopy

import pytest

from app.models.entity import Entity
from app.models.ontology import OntologyProject
from app.models.user import User
from app.models.v2.query_view import QueryDataView
from app.models.v2.time_series import TimeSeriesSync
from app.routers.v2.rule_runtime import get_graph_reader
from app.schemas.v2.rule_runtime import SeriesWindowBlock
from app.services.auth_service import hash_password
from app.services.v2.object_query.core import GraphData
from app.services.v2.object_query.errors import ObjectQueryError
from app.services.v2.rule_runtime import _rolling, window_episodes

GRAPHS: dict[str, GraphData] = {}


class Adapter:
    def __init__(self, data): self.data = data
    def read(self, ontology_id): return deepcopy(self.data)


@pytest.fixture(autouse=True)
def graph_reader(client):
    from app.main import app
    GRAPHS.clear()
    app.dependency_overrides[get_graph_reader] = lambda: (lambda view: Adapter(GRAPHS[view.graph_key]))
    yield
    app.dependency_overrides.pop(get_graph_reader, None)


def stamp(i):
    return f"2026-01-01T{i // 4:02d}:{(i % 4) * 15:02d}:00+00:00"


def block(**kw):
    return SeriesWindowBlock(kind="series_window", **{"series_property": "s", "window_points": 2, "aggregate": "avg",
                                                     "op": "gt", "threshold": 3, **kw})


# ── pure window semantics ────────────────────────────────────────

def test_window_episode_known_answer():
    points = [{"timestamp": stamp(i), "value": v} for i, v in enumerate([1, 1, 5, 5, 5, 1, 5])]
    episodes, quality = window_episodes(points, block())
    # avg windows ending at 1..6: 1,3,5,5,3,3 -> breaching windows ending at 3 and 4
    assert episodes == [{"window_start": stamp(2), "window_end": stamp(4), "breaching_windows": 2, "peak": 5.0, "ongoing": False}]
    assert quality["points"] == 7 and quality["irregular_intervals"] == 0 and quality["watermark"] == stamp(6)


def test_trailing_episode_is_ongoing_and_min_consecutive_filters():
    points = [{"timestamp": stamp(i), "value": v} for i, v in enumerate([5, 5, 1, 1, 9, 9])]
    episodes, _ = window_episodes(points, block())
    assert [(e["window_start"], e["ongoing"]) for e in episodes] == [(stamp(0), False), (stamp(3), True)]
    assert window_episodes(points, block(min_consecutive_windows=3))[0] == []


@pytest.mark.parametrize("aggregate", ["avg", "sum", "min", "max"])
def test_rolling_matches_brute_force(aggregate):
    rng = random.Random(7)
    values = [rng.uniform(-50, 50) for _ in range(500)]
    fn = {"avg": lambda w: sum(w) / len(w), "sum": sum, "min": min, "max": max}[aggregate]
    for size in (1, 3, 17):
        expected = [fn(values[i - size + 1:i + 1]) for i in range(size - 1, len(values))]
        assert _rolling(values, size, aggregate) == pytest.approx(expected)


def test_point_order_and_exact_duplicates_do_not_change_result():
    points = [{"timestamp": stamp(i), "value": v} for i, v in enumerate([1, 4, 6, 2, 8, 8, 1])]
    shuffled = points[::-1] + [dict(points[2])]
    first, _ = window_episodes(points, block())
    second, quality = window_episodes(shuffled, block())
    assert first == second and quality["duplicate_points"] == 1


@pytest.mark.parametrize("bad,code", [
    ([{"timestamp": stamp(0), "value": 1}, {"timestamp": stamp(0), "value": 2}], "conflicting_points"),
    ([{"timestamp": stamp(0), "value": float("nan")}], "invalid_series"),
    ([{"timestamp": stamp(0), "value": True}], "invalid_series"),
])
def test_invalid_points_fail_explicitly(bad, code):
    with pytest.raises(ObjectQueryError) as caught:
        window_episodes(bad, block())
    assert caught.value.code == code


def test_irregular_sampling_is_reported():
    times = ["2026-01-01T00:00:00+00:00", "2026-01-01T00:15:00+00:00", "2026-01-01T00:30:00+00:00", "2026-01-01T02:00:00+00:00"]
    _, quality = window_episodes([{"timestamp": t, "value": 1} for t in times], block())
    assert quality["irregular_intervals"] == 1


# ── API fixtures ─────────────────────────────────────────────────

@pytest.fixture
def ontology(db, admin_user):
    item = OntologyProject(name="Rules", domain="manufacturing", created_by=admin_user.id)
    db.add(item); db.flush()
    db.add(Entity(ontology_id=item.id, name_cn="设备", name_en="Machine", canonical_id="ontology:Machine", type="EntityType",
                  properties={"property_definitions": [{"id": "status", "type": "string"}, {"id": "line", "type": "string"},
                                                       {"id": "temp_series", "type": "string"}, {"id": "load", "type": "integer"}]}))
    db.commit()
    return item


def make_view(db, ontology, user, objects, series=None):
    from app.services.v2.object_query.core import metadata_digest
    from app.services.v2.object_query.metadata import load_sql_metadata
    view = QueryDataView(ontology_id=ontology.id, source_manifest_digest="fixture",
                         metadata_digest=metadata_digest(load_sql_metadata(db, ontology.id)),
                         content_digest=str(uuid.uuid4()), graph_key=f"view_{uuid.uuid4().hex}", status="ready", created_by=user.id)
    db.add(view); db.flush()
    GRAPHS[view.graph_key] = GraphData({("Machine", oid): props for oid, props in objects.items()}, [])
    for series_id, values in (series or {}).items():
        points = [{"timestamp": stamp(i), "value": v} for i, v in enumerate(values)]
        db.add(TimeSeriesSync(ontology_id=ontology.id, data_view_id=view.id, series_id=series_id, root_type="Machine",
                              root_id=series_id, sensor_type="Sensor", sensor_id=series_id, property_api_name="temp_series",
                              unit="C", interpolation="linear", points=points, point_count=len(points)))
    db.commit()
    return view


def series_logic(**kw):
    return {"input": {"kind": "base", "type_ref": {"api_name": "Machine"}},
            "blocks": [{"kind": "series_window", "series_property": "temp_series", "window_points": 2, "aggregate": "avg",
                        "op": "gt", "threshold": 3}],
            "output": {"severity": "high", "properties": {"assignee": "maintenance"}, "evidence_properties": ["line"]}, **kw}


def filter_logic():
    return {"input": {"kind": "base", "type_ref": {"api_name": "Machine"}},
            "blocks": [{"kind": "filter", "where": {"kind": "comparison", "property": {"api_name": "status"}, "op": "eq", "value": "down"}}],
            "output": {"severity": "medium", "evidence_properties": ["status"]}}


def base_url(ontology): return f"/api/v2/ontologies/{ontology.id}"


def create_and_approve(client, headers, ontology, logic, api_name="hot_machine"):
    r = client.post(f"{base_url(ontology)}/rules", headers=headers,
                    json={"api_name": api_name, "display_name": api_name, "logic": logic})
    assert r.status_code == 201, r.text
    rule, proposal = r.json()["rule"], r.json()["proposal"]
    r = client.post(f"{base_url(ontology)}/rules/{rule['id']}/proposals/{proposal['id']}:review", headers=headers, json={"decision": "approve"})
    assert r.status_code == 200, r.text
    return rule["id"]


def evaluate(client, headers, ontology, rule_id, view, mode="commit", **context):
    return client.post(f"{base_url(ontology)}/rules/{rule_id}/evaluate", headers=headers,
                       json={"mode": mode, "context": {"ontology_id": ontology.id, "data_view_id": view.id, **context}})


@pytest.fixture
def editor_headers(client, db):
    db.add(User(id=str(uuid.uuid4()), username="editor2", email="e2@test.com", password_hash=hash_password("editor123"), role="editor"))
    db.commit()
    token = client.post("/api/v1/auth/login", json={"username": "editor2", "password": "editor123"}).json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


# ── governance ───────────────────────────────────────────────────

def test_rule_requires_approved_proposal_before_evaluation(client, auth_headers, db, admin_user, ontology):
    r = client.post(f"{base_url(ontology)}/rules", headers=auth_headers,
                    json={"api_name": "hot", "display_name": "Hot", "logic": series_logic()})
    rule = r.json()["rule"]
    assert rule["current_version"] == 0 and r.json()["proposal"]["status"] == "open"
    view = make_view(db, ontology, admin_user, {"m1": {"temp_series": "s1"}}, {"s1": [1, 5, 5]})
    assert evaluate(client, auth_headers, ontology, rule["id"], view).status_code == 404
    r = client.post(f"{base_url(ontology)}/rules/{rule['id']}/proposals/{r.json()['proposal']['id']}:review",
                    headers=auth_headers, json={"decision": "approve"})
    assert r.json()["status"] == "approved" and r.json()["self_approved"] is True


def test_unknown_property_and_action_rejected_at_proposal_time(client, auth_headers, ontology):
    logic = series_logic(); logic["output"]["evidence_properties"] = ["nope"]
    r = client.post(f"{base_url(ontology)}/rules", headers=auth_headers, json={"api_name": "x", "display_name": "x", "logic": logic})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "unknown_property"
    logic = series_logic(action={"action_id": "missing"})
    r = client.post(f"{base_url(ontology)}/rules", headers=auth_headers, json={"api_name": "y", "display_name": "y", "logic": logic})
    assert r.json()["detail"]["code"] == "unknown_action"
    logic = series_logic(); logic["blocks"][0]["series_property"] = "load"
    r = client.post(f"{base_url(ontology)}/rules", headers=auth_headers, json={"api_name": "z", "display_name": "z", "logic": logic})
    assert r.json()["detail"]["code"] == "metadata_mismatch"


def test_series_window_must_be_final_block(client, auth_headers, ontology):
    logic = series_logic(); logic["blocks"].append(filter_logic()["blocks"][0])
    r = client.post(f"{base_url(ontology)}/rules", headers=auth_headers, json={"api_name": "x", "display_name": "x", "logic": logic})
    assert r.status_code == 422


def test_stale_proposal_is_rejected(client, auth_headers, ontology):
    rule_id = create_and_approve(client, auth_headers, ontology, series_logic())
    first = client.post(f"{base_url(ontology)}/rules/{rule_id}/proposals", headers=auth_headers,
                        json={"base_version": 1, "logic": series_logic()}).json()
    second = client.post(f"{base_url(ontology)}/rules/{rule_id}/proposals", headers=auth_headers,
                         json={"base_version": 1, "logic": filter_logic()}).json()
    assert first["old_logic"]["output"]["severity"] == "high" and second["new_logic"]["output"]["severity"] == "medium"
    ok = client.post(f"{base_url(ontology)}/rules/{rule_id}/proposals/{first['id']}:review", headers=auth_headers, json={"decision": "approve"})
    assert ok.status_code == 200
    stale = client.post(f"{base_url(ontology)}/rules/{rule_id}/proposals/{second['id']}:review", headers=auth_headers, json={"decision": "approve"})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "stale_proposal"
    late = client.post(f"{base_url(ontology)}/rules/{rule_id}/proposals", headers=auth_headers, json={"base_version": 1, "logic": filter_logic()})
    assert late.status_code == 409


def test_editor_self_approval_forbidden(client, editor_headers, db, ontology):
    ontology.created_by = db.query(User).filter_by(username="editor2").one().id
    db.commit()
    r = client.post(f"{base_url(ontology)}/rules", headers=editor_headers, json={"api_name": "e", "display_name": "e", "logic": series_logic()})
    assert r.status_code == 201, r.text
    body = r.json()
    r = client.post(f"{base_url(ontology)}/rules/{body['rule']['id']}/proposals/{body['proposal']['id']}:review",
                    headers=editor_headers, json={"decision": "approve"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "self_review_forbidden"


def test_other_users_cannot_see_rules(client, auth_headers, editor_headers, ontology):
    create_and_approve(client, auth_headers, ontology, series_logic())
    assert client.get(f"{base_url(ontology)}/rules", headers=editor_headers).status_code == 404
    assert client.get(f"{base_url(ontology)}/alerts", headers=editor_headers).status_code == 404


# ── evaluation and alert lifecycle ───────────────────────────────

def test_commit_is_idempotent_and_growing_episode_updates_one_alert(client, auth_headers, db, admin_user, ontology):
    rule_id = create_and_approve(client, auth_headers, ontology, series_logic())
    v1 = make_view(db, ontology, admin_user, {"m1": {"temp_series": "s1", "line": "L1"}, "m2": {"temp_series": "s2", "line": "L2"}},
                   {"s1": [1, 5, 5], "s2": [1, 1, 1]})
    first = evaluate(client, auth_headers, ontology, rule_id, v1).json()
    assert (first["triggered_count"], first["output_count"], first["replayed"]) == (1, 1, False)
    again = evaluate(client, auth_headers, ontology, rule_id, v1).json()
    assert again["replayed"] is True and again["id"] == first["id"]
    alerts = client.get(f"{base_url(ontology)}/alerts", headers=auth_headers).json()
    assert alerts["total"] == 1
    alert = alerts["alerts"][0]
    assert alert["subject"] == {"object_type": "Machine", "object_id": "m1"} and alert["evidence"]["evidence"] == {"line": "L1"}
    assert alert["properties"] == {"assignee": "maintenance"} and alert["recovered_at"] is None
    v2 = make_view(db, ontology, admin_user, {"m1": {"temp_series": "s1", "line": "L1"}}, {"s1": [1, 5, 5, 6, 1, 1]})
    second = evaluate(client, auth_headers, ontology, rule_id, v2).json()
    assert second["triggered_count"] == 0 and second["recurring_count"] == 1 and second["recovered_count"] == 1
    detail = client.get(f"{base_url(ontology)}/alerts/{alert['id']}", headers=auth_headers).json()
    assert detail["alert"]["occurrence_count"] == 2 and detail["alert"]["window_end"] == stamp(4)
    assert detail["alert"]["recovered_at"] is not None
    assert [e["kind"] for e in detail["events"]] == ["triggered", "recurred", "recovered"]


def test_object_leaving_filter_set_recovers_alert(client, auth_headers, db, admin_user, ontology):
    rule_id = create_and_approve(client, auth_headers, ontology, filter_logic(), "down_machine")
    v1 = make_view(db, ontology, admin_user, {"m1": {"status": "down"}, "m2": {"status": "up"}})
    assert evaluate(client, auth_headers, ontology, rule_id, v1).json()["triggered_count"] == 1
    v2 = make_view(db, ontology, admin_user, {"m1": {"status": "up"}, "m2": {"status": "down"}})
    result = evaluate(client, auth_headers, ontology, rule_id, v2).json()
    assert (result["triggered_count"], result["recovered_count"]) == (1, 1)
    alerts = {a["subject"]["object_id"]: a for a in client.get(f"{base_url(ontology)}/alerts", headers=auth_headers).json()["alerts"]}
    assert alerts["m1"]["recovered_at"] is not None and alerts["m2"]["recovered_at"] is None


def test_preview_matches_commit_and_writes_nothing(client, auth_headers, db, admin_user, ontology):
    rule_id = create_and_approve(client, auth_headers, ontology, series_logic())
    view = make_view(db, ontology, admin_user, {"m1": {"temp_series": "s1"}}, {"s1": [9, 9, 1, 1, 9, 9]})
    preview = evaluate(client, auth_headers, ontology, rule_id, view, mode="preview").json()
    assert client.get(f"{base_url(ontology)}/alerts", headers=auth_headers).json()["total"] == 0
    commit = evaluate(client, auth_headers, ontology, rule_id, view).json()
    assert preview["result_digest"] == commit["result_digest"] and preview["output_count"] == commit["output_count"] == 2


def test_scenario_context_is_preview_only(client, auth_headers, db, admin_user, ontology):
    from app.models.v2.scenario import ScenarioResource
    rule_id = create_and_approve(client, auth_headers, ontology, filter_logic(), "down")
    view = make_view(db, ontology, admin_user, {"m1": {"status": "down"}})
    scenario = ScenarioResource(ontology_id=ontology.id, owner_id=admin_user.id, name="what-if", base_view_id=view.id)
    db.add(scenario); db.commit()
    preview = evaluate(client, auth_headers, ontology, rule_id, view, mode="preview", scenario_id=scenario.id, scenario_revision=0)
    assert preview.status_code == 200, preview.text
    assert preview.json()["output_count"] == 1
    commit = evaluate(client, auth_headers, ontology, rule_id, view, scenario_id=scenario.id, scenario_revision=0)
    assert commit.status_code == 422 and commit.json()["detail"]["code"] == "scenario_commit_forbidden"


def test_muted_rule_records_alerts_without_effects_and_lifecycle(client, auth_headers, db, admin_user, ontology):
    from app.models.v2.action import OntologyActionType
    action = OntologyActionType(ontology_id=ontology.id, name="Inspect", action_category="update", status="published")
    db.add(action); db.commit()
    rule_id = create_and_approve(client, auth_headers, ontology, {**filter_logic(), "action": {"action_id": action.id, "parameters": {"reason": "rule"}}}, "down")
    v1 = make_view(db, ontology, admin_user, {"m1": {"status": "down"}})
    evaluate(client, auth_headers, ontology, rule_id, v1)
    alert = client.get(f"{base_url(ontology)}/alerts", headers=auth_headers).json()["alerts"][0]
    assert alert["suggested_action"] == {"action_id": action.id, "request": {"target_object_id": "m1", "parameters": {"reason": "rule"}}}
    assert client.post(f"{base_url(ontology)}/rules/{rule_id}/status", headers=auth_headers, json={"status": "muted"}).json()["status"] == "muted"
    v2 = make_view(db, ontology, admin_user, {"m1": {"status": "down"}, "m2": {"status": "down"}})
    assert evaluate(client, auth_headers, ontology, rule_id, v2).json()["triggered_count"] == 1
    muted = client.get(f"{base_url(ontology)}/alerts?subject_id=m2", headers=auth_headers).json()["alerts"][0]
    assert muted["suggested_action"] is None
    url = f"{base_url(ontology)}/alerts/{alert['id']}"
    assert client.post(f"{url}:resolve", headers=auth_headers, json={"note": "fixed"}).json()["status"] == "resolved"
    again = client.post(f"{url}:acknowledge", headers=auth_headers, json={})
    assert again.status_code == 409 and again.json()["detail"]["code"] == "invalid_transition"
    assert client.get(f"{base_url(ontology)}/alerts?status=resolved", headers=auth_headers).json()["total"] == 1


def test_resolved_alert_is_not_reopened_by_same_episode(client, auth_headers, db, admin_user, ontology):
    rule_id = create_and_approve(client, auth_headers, ontology, filter_logic(), "down")
    evaluate(client, auth_headers, ontology, rule_id, make_view(db, ontology, admin_user, {"m1": {"status": "down"}}))
    alert = client.get(f"{base_url(ontology)}/alerts", headers=auth_headers).json()["alerts"][0]
    client.post(f"{base_url(ontology)}/alerts/{alert['id']}:acknowledge", headers=auth_headers, json={})
    client.post(f"{base_url(ontology)}/alerts/{alert['id']}:resolve", headers=auth_headers, json={"note": "done"})
    result = evaluate(client, auth_headers, ontology, rule_id, make_view(db, ontology, admin_user, {"m1": {"status": "down"}})).json()
    assert result["triggered_count"] == 0
    body = client.get(f"{base_url(ontology)}/alerts", headers=auth_headers).json()
    assert body["total"] == 1 and body["alerts"][0]["status"] == "resolved" and body["alerts"][0]["occurrence_count"] == 2


def test_failed_evaluation_is_recorded_and_retryable(client, auth_headers, db, admin_user, ontology):
    rule_id = create_and_approve(client, auth_headers, ontology, series_logic())
    view = make_view(db, ontology, admin_user, {"m1": {"temp_series": "s1"}}, {"s1": [1, 2]})
    sync = db.query(TimeSeriesSync).filter_by(data_view_id=view.id).one()
    sync.points = [{"timestamp": stamp(0), "value": 1}, {"timestamp": stamp(0), "value": 2}]
    db.commit()
    bad = evaluate(client, auth_headers, ontology, rule_id, view)
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "conflicting_points"
    history = client.get(f"{base_url(ontology)}/rules/{rule_id}/evaluations", headers=auth_headers).json()["evaluations"]
    assert [h["status"] for h in history] == ["failed"]
    sync.points = [{"timestamp": stamp(0), "value": 9}, {"timestamp": stamp(1), "value": 9}]
    db.commit()
    assert evaluate(client, auth_headers, ontology, rule_id, view).json()["triggered_count"] == 1


def test_resolution_links_only_a_completed_action_run_on_the_same_subject(client, auth_headers, db, admin_user, ontology):
    from app.models.v2.action import OntologyActionRun
    rule_id = create_and_approve(client, auth_headers, ontology, filter_logic(), "down")
    evaluate(client, auth_headers, ontology, rule_id, make_view(db, ontology, admin_user, {"m1": {"status": "down"}}))
    alert = client.get(f"{base_url(ontology)}/alerts", headers=auth_headers).json()["alerts"][0]
    url = f"{base_url(ontology)}/alerts/{alert['id']}"
    runs = {name: OntologyActionRun(ontology_id=ontology.id, target_object_id=target, status=status)
            for name, (target, status) in {"other": ("m2", "completed"), "failed": ("m1", "failed"), "ok": ("m1", "completed")}.items()}
    db.add_all(runs.values()); db.commit()
    assert client.post(f"{url}:resolve", headers=auth_headers, json={"action_run_id": "missing"}).status_code == 404
    for name in ("other", "failed"):
        r = client.post(f"{url}:resolve", headers=auth_headers, json={"action_run_id": runs[name].id})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "action_run_mismatch"
    r = client.post(f"{url}:acknowledge", headers=auth_headers, json={"action_run_id": runs["ok"].id})
    assert r.status_code == 409
    r = client.post(f"{url}:resolve", headers=auth_headers, json={"action_run_id": runs["ok"].id, "note": "fixed"})
    assert r.json()["resolution_action_run_id"] == runs["ok"].id
    events = client.get(url, headers=auth_headers).json()["events"]
    assert events[-1]["kind"] == "resolved" and events[-1]["detail"]["action_run_id"] == runs["ok"].id
