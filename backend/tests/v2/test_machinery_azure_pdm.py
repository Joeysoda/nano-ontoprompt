"""Plan B item 9 acceptance: Machinery path on the full Azure PdM sample.

Real FalkorDB, all 100 machines.  One device object carries its four
Time Series Properties, its failure/maintenance/error Event Objects, rule
alerts and a maintenance Action, and the alert trace links them together.

Azure PdM is Microsoft's *simulated* predictive-maintenance sample; results
are evidence about this runtime, not about field equipment.  The dataset is
large (80 MB) and lives outside the repository: set AZURE_PDM_DIR or place it
at <workspace>/data/azure_pdm.  Checksums are pinned in
tests/data/azure_pdm_manifest.json.
"""
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.models.entity import Entity
from app.models.entity_instance import EntityInstance
from app.models.ontology import OntologyProject
from app.models.relation import Relation
from app.models.v2.action import OntologyActionType
from app.models.v2.time_series import TimeSeriesSync
from app.services.v2.azure_pdm import EVENT_LINKS, load_surface, ontology_types
from app.services.v2.graph.falkordb_service import FalkorDBService
from app.services.v2.object_query.data_views import build_live_view
from app.services.v2.object_query.metadata import load_sql_metadata
from app.services.v2.temporal_surface import install_surface

MANIFEST = json.loads((Path(__file__).resolve().parents[1] / "data" / "azure_pdm_manifest.json").read_text())
DATA = Path(os.environ.get("AZURE_PDM_DIR") or Path(__file__).resolve().parents[4] / "data" / "azure_pdm")
RULES = {"volt": ("gt", "comp1"), "rotate": ("lt", "comp2"), "pressure": ("gt", "comp3"), "vibration": ("gt", "comp4")}
WINDOW = 24  # hourly readings -> 24 h rolling mean


def oracle(telemetry: pd.DataFrame, channel: str, op: str):
    """Independent pandas episodes; threshold = pooled mean ± 3 sd (unsupervised)."""
    rolling = telemetry.groupby("machineID")[channel].transform(lambda s: s.rolling(WINDOW).mean())
    threshold = rolling.mean() + 3 * rolling.std() if op == "gt" else rolling.mean() - 3 * rolling.std()
    hit = rolling > threshold if op == "gt" else rolling < threshold
    episodes = []
    for machine, group in telemetry.assign(hit=hit).groupby("machineID"):
        flags, stamps = group["hit"].to_numpy(), group["utc"].to_numpy()
        start = None
        for i in range(len(flags) + 1):
            on = i < len(flags) and flags[i]
            if on and start is None:
                start = i
            if not on and start is not None:
                episodes.append((f"machine-{machine:03d}", stamps[start - WINDOW + 1], stamps[i - 1]))
                start = None
    return float(threshold), episodes


@pytest.fixture
def pdm(db, admin_user):
    if not DATA.exists():
        pytest.skip(f"Azure PdM acceptance data not present at {DATA}")
    for name, info in MANIFEST["files"].items():
        assert hashlib.sha256((DATA / name).read_bytes()).hexdigest() == info["sha256"], f"{name} checksum mismatch"
    graph = FalkorDBService()
    if not graph.available:
        pytest.fail("Real FalkorDB is required for Machinery acceptance")
    surface = load_surface(DATA)
    ontology = OntologyProject(name="Azure PdM", domain="manufacturing", created_by=admin_user.id)
    db.add(ontology); db.flush()
    entities = {}
    for spec in ontology_types():
        entity = Entity(ontology_id=ontology.id, name_cn=spec["name_cn"], name_en=spec["api_name"], canonical_id=f"ontology:{spec['api_name']}",
                        type="EntityType", properties={"property_definitions": spec["properties"]})
        db.add(entity); db.flush()
        entities[spec["api_name"]] = entity
    for name, link in EVENT_LINKS.items():
        db.add(Relation(ontology_id=ontology.id, source_entity=entities[name].id, target_entity=entities["Machine"].id, type=link))
    for machine in surface.machines:
        db.add(EntityInstance(id=machine["id"], entity_id=entities["Machine"].id, ontology_id=ontology.id,
                              row_identity=machine["id"], row_data=dict(machine["properties"])))
    db.commit()
    graph.upsert_instances(ontology.id, surface.machines)
    metadata = load_sql_metadata(db, ontology.id)
    digest = hashlib.sha256(json.dumps(surface.manifest["files"], sort_keys=True).encode()).hexdigest()
    view = build_live_view(db, graph, metadata, ontology.id, admin_user.id, digest, 86400)
    install_surface(db, graph, view, surface.event_nodes, surface.event_links, surface.syncs)
    try:
        yield ontology, view, surface, entities
    finally:
        assert graph.delete_graph(view.graph_key)
        graph.delete_graph(ontology.id)


def api(ontology): return f"/api/v2/ontologies/{ontology.id}"


def utc_iso(value):
    stamp = pd.Timestamp(value)
    return (stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")).isoformat()


def test_machinery_path_end_to_end_on_full_azure_pdm(client, auth_headers, db, pdm):
    ontology, view, surface, _ = pdm
    counts = {name: info["rows"] for name, info in MANIFEST["files"].items()}
    # 1. Canonical surface is complete: objects, Event Objects and TSPs reconcile with the source.
    assert surface.manifest["counts"] == {"machines": 100, "events": counts["PdM_failures.csv"] + counts["PdM_maint.csv"] + counts["PdM_errors.csv"],
                                          "series": 400, "points": counts["PdM_telemetry.csv"] * 4}
    db.refresh(view)
    assert view.status == "ready" and view.object_count == 100 + surface.manifest["counts"]["events"]
    assert db.query(TimeSeriesSync).filter_by(data_view_id=view.id).count() == 400
    series = client.get(f"{api(ontology)}/object-query/data-views/{view.id}/time-series/series:pdm:machine-001:vibration?limit=3",
                        headers=auth_headers).json()
    assert series["unit"] == "mm/s" and series["root"] == {"object_type": "Machine", "object_id": "machine-001"}
    assert series["page"]["has_more"] is True and series["points"][0]["timestamp"] == "2015-01-01T06:00:00+00:00"

    context = {"ontology_id": ontology.id, "consistency": "snapshot", "data_view_id": view.id}
    failures = pd.read_csv(DATA / "PdM_failures.csv", parse_dates=["datetime"])
    comp4 = client.post(f"{api(ontology)}/object-query/load", headers=auth_headers, json={
        "expression": {"kind": "filter", "input": {"kind": "base", "type_ref": {"api_name": "FailureEvent"}},
                       "where": {"kind": "comparison", "property": {"api_name": "code"}, "op": "eq", "value": "comp4"}},
        "context": context, "read": {"page_size": 200}}).json()
    assert comp4["completeness"] == "complete" and len(comp4["objects"]) == int((failures.failure == "comp4").sum()) == 179
    linked = client.post(f"{api(ontology)}/object-query/load", headers=auth_headers, json={
        "expression": {"kind": "traverse", "input": {"kind": "static", "type_ref": {"api_name": "FailureEvent"},
                                                     "object_ids": [comp4["objects"][0]["object_id"]]}, "link": {"api_name": "FAILURE_OF"}},
        "context": context}).json()
    assert [o["object_id"] for o in linked["objects"]] == [comp4["objects"][0]["properties"]["machine"]]
    # The ObjectPanel "关联事件" request: reverse traversal per event link, newest first.
    machine_failures = failures[failures.machineID == 1].sort_values("datetime", ascending=False)
    events = client.post(f"{api(ontology)}/object-query/load", headers=auth_headers, json={
        "expression": {"kind": "traverse", "input": {"kind": "static", "type_ref": {"api_name": "Machine"}, "object_ids": ["machine-001"]},
                       "link": {"api_name": "FAILURE_OF", "direction": "in"}},
        "context": context, "read": {"page_size": 20, "order_by": [{"property": {"api_name": "start_timestamp"}, "direction": "desc"}]}})
    assert events.status_code == 200, events.text
    assert [o["properties"]["start_timestamp"] for o in events.json()["objects"]] == [
        t.tz_localize("UTC").isoformat() for t in machine_failures.datetime]

    # 2. Maintenance Action bound to the vibration rule.
    action = OntologyActionType(ontology_id=ontology.id, name="Schedule maintenance", action_category="update", status="published", enabled=True,
                                target_entity_type="Machine", parameters=[{"name": "machine", "type": "object", "object_type": "Machine"}],
                                effects=[{"op": "set_property", "target_parameter": "machine", "property": "maintenance_status", "static_value": "scheduled"}])
    db.add(action); db.commit()

    # 3. One rule per channel; alerts must equal the independent oracle exactly.
    telemetry = pd.read_csv(DATA / "PdM_telemetry.csv", parse_dates=["datetime"]).sort_values(["machineID", "datetime"]).reset_index(drop=True)
    telemetry["utc"] = telemetry["datetime"].dt.tz_localize("UTC")
    evidence = {}
    for channel, (op, component) in RULES.items():
        threshold, expected = oracle(telemetry, channel, op)
        logic = {"input": {"kind": "base", "type_ref": {"api_name": "Machine"}},
                 "blocks": [{"kind": "series_window", "series_property": f"{channel}_series", "window_points": WINDOW,
                             "aggregate": "avg", "op": op, "threshold": threshold}],
                 "output": {"severity": "high", "properties": {"component": component}, "evidence_properties": ["model"]}}
        if channel == "vibration":
            logic["action"] = {"action_id": action.id, "object_parameter": "machine"}
        body = client.post(f"{api(ontology)}/rules", headers=auth_headers,
                           json={"api_name": f"{channel}_drift", "display_name": channel, "logic": logic}).json()
        rule_id = body["rule"]["id"]
        client.post(f"{api(ontology)}/rules/{rule_id}/proposals/{body['proposal']['id']}:review", headers=auth_headers, json={"decision": "approve"})
        result = client.post(f"{api(ontology)}/rules/{rule_id}/evaluate", headers=auth_headers,
                             json={"mode": "commit", "context": {"ontology_id": ontology.id, "data_view_id": view.id}})
        assert result.status_code == 200, result.text
        alerts, offset = [], 0
        while True:
            page = client.get(f"{api(ontology)}/alerts?rule_id={rule_id}&limit=500&offset={offset}", headers=auth_headers).json()
            alerts += page["alerts"]; offset += 500
            if offset >= page["total"]:
                break
        got = sorted((a["subject"]["object_id"], a["window_start"], a["window_end"]) for a in alerts)
        want = sorted((m, utc_iso(s), utc_iso(e)) for m, s, e in expected)
        assert got == want, f"{channel}: runtime alerts differ from the oracle"
        # Predictive value against recorded failures of the matching component (48 h lead window).
        episodes = pd.DataFrame([(a["subject"]["object_id"], pd.Timestamp(a["window_start"]), pd.Timestamp(a["window_end"])) for a in alerts],
                                columns=["machine", "start", "end"])
        target = failures[failures.failure == component].assign(machine=lambda d: d.machineID.map(lambda m: f"machine-{m:03d}"),
                                                                 failed_at=lambda d: d.datetime.dt.tz_localize("UTC"))

        def recall(times):
            return float(np.mean([((episodes.machine == m) & (episodes.end >= t - pd.Timedelta(hours=48)) & (episodes.start <= t)).any()
                                  for m, t in times]))
        rng = np.random.default_rng(0)
        lo, hi = telemetry.utc.min().value, telemetry.utc.max().value
        baseline = float(np.mean([recall([(m, pd.Timestamp(int(rng.integers(lo, hi)), tz="UTC")) for m in target.machine]) for _ in range(5)]))
        evidence[channel] = {"threshold": round(threshold, 3), "alerts": len(alerts), "failures": len(target),
                             "recall_48h": round(recall(zip(target.machine, target.failed_at)), 3), "random_baseline": round(baseline, 3)}
        assert evidence[channel]["recall_48h"] >= 3 * evidence[channel]["random_baseline"]
        if channel == "vibration":
            vibration_alerts = alerts
    print("MACHINERY_EVIDENCE", json.dumps(evidence))

    # 4. Same device: alert -> Action preview -> commit -> resolution linked to the Action run.
    alert = next(a for a in vibration_alerts if a["subject"]["object_id"] == "machine-001") if any(
        a["subject"]["object_id"] == "machine-001" for a in vibration_alerts) else vibration_alerts[0]
    machine = alert["subject"]["object_id"]
    request = {**alert["suggested_action"]["request"], "context": {"ontology_id": ontology.id, "consistency": "live"}}
    preview = client.post(f"{api(ontology)}/actions/{action.id}/preview", headers=auth_headers, json=request)
    assert preview.status_code == 200, preview.text
    assert preview.json()["edit_count"] == 1
    run = client.post(f"{api(ontology)}/actions/{action.id}/run", headers=auth_headers, json=request)
    assert run.status_code == 200, run.text
    run_id = run.json()["run_id"]
    other = next(a for a in vibration_alerts if a["subject"]["object_id"] != machine)
    mismatch = client.post(f"{api(ontology)}/alerts/{other['id']}:resolve", headers=auth_headers, json={"action_run_id": run_id})
    assert mismatch.status_code == 409 and mismatch.json()["detail"]["code"] == "action_run_mismatch"
    client.post(f"{api(ontology)}/alerts/{alert['id']}:acknowledge", headers=auth_headers, json={"note": "dispatching"})
    resolved = client.post(f"{api(ontology)}/alerts/{alert['id']}:resolve", headers=auth_headers,
                           json={"note": "maintenance scheduled", "action_run_id": run_id}).json()
    assert resolved["status"] == "resolved" and resolved["resolution_action_run_id"] == run_id
    db.expire_all()
    assert db.get(EntityInstance, machine).row_data["maintenance_status"] == "scheduled"
    trace = client.get(f"{api(ontology)}/alerts/{alert['id']}", headers=auth_headers).json()
    kinds = [e["kind"] for e in trace["events"]]
    assert kinds[0] == "triggered" and kinds[-2:] == ["acknowledged", "resolved"]
    assert trace["events"][-1]["detail"]["action_run_id"] == run_id
    assert trace["alert"]["evidence"]["series_id"] == f"series:pdm:{machine}:vibration"
