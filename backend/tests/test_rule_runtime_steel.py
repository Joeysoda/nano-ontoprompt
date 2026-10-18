"""Real-data acceptance for the rule runtime on UCI Steel Industry Energy.

Known answers come from an independent pandas implementation (rolling mean +
run-length grouping), not from the runtime's own helpers.

Timestamp interpretation: each day's "dd/mm/yyyy 00:00" row (NSM=0) is the
reading that closes that day, i.e. next-day 00:00.  The data supports this:
its median |ΔkWh| to the file neighbours is 0.07 versus 0.39 to the literal
same-day 00:15 neighbour.  The plant is in South Korea (UTC+9, no DST).
"""
import hashlib
from pathlib import Path

import pandas as pd
import pytest

from app.models.entity import Entity
from app.models.ontology import OntologyProject
from app.models.v2.query_view import QueryDataView
from app.models.v2.time_series import TimeSeriesSync
from app.routers.v2.rule_runtime import get_graph_reader
from app.services.v2.object_query.core import GraphData, metadata_digest
from app.services.v2.object_query.metadata import load_sql_metadata
from app.services.v2.uci_steel_energy import DATASET_SHA256

CSV = Path(__file__).parent / "data" / "Steel_industry_data.csv"
WINDOW = 4  # four 15-minute readings = one hour
GRAPHS: dict[str, GraphData] = {}


class Adapter:
    def __init__(self, data): self.data = data
    def read(self, ontology_id): return self.data


@pytest.fixture(scope="module")
def frame():
    raw = CSV.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == DATASET_SHA256
    df = pd.read_csv(CSV, encoding="utf-8-sig")
    assert len(df) == 35_040
    ts = pd.to_datetime(df["date"], format="%d/%m/%Y %H:%M")
    ts = ts.where(df["NSM"] != 0, ts + pd.Timedelta(days=1))
    df["ts"] = ts.dt.tz_localize("Asia/Seoul")
    df["utc"] = df["ts"].dt.tz_convert("UTC")  # the runtime stores instants in UTC
    df["day"] = pd.to_datetime(df["date"], format="%d/%m/%Y %H:%M").dt.date
    assert df["ts"].is_monotonic_increasing and not df["ts"].duplicated().any()
    return df


def oracle_episodes(df: pd.DataFrame, threshold: float) -> list[tuple[str, str, float]]:
    rolling = df["Usage_kWh"].rolling(WINDOW).mean()
    hit = rolling > threshold
    run_id = (hit != hit.shift()).cumsum()
    out = []
    for _, run in df[hit].groupby(run_id[hit]):
        first, last = run.index[0], run.index[-1]
        out.append((df["utc"].iloc[first - WINDOW + 1].isoformat(), df["utc"].iloc[last].isoformat(),
                    round(float(rolling.loc[first:last].max()), 6)))
    return out


@pytest.fixture
def setup(client, db, admin_user):
    from app.main import app
    GRAPHS.clear()
    app.dependency_overrides[get_graph_reader] = lambda: (lambda view: Adapter(GRAPHS[view.graph_key]))
    ontology = OntologyProject(name="UCI steel", domain="manufacturing", created_by=admin_user.id)
    db.add(ontology); db.flush()
    db.add(Entity(ontology_id=ontology.id, name_cn="工厂", name_en="Plant", canonical_id="ontology:Plant", type="EntityType",
                  properties={"property_definitions": [{"id": "usage_series", "type": "string"}, {"id": "name", "type": "string"}]}))
    db.add(Entity(ontology_id=ontology.id, name_cn="生产日", name_en="EnergyDay", canonical_id="ontology:EnergyDay", type="EntityType",
                  properties={"property_definitions": [{"id": "total_kwh", "type": "double"}, {"id": "week_status", "type": "string"}]}))
    db.commit()
    yield ontology
    app.dependency_overrides.pop(get_graph_reader, None)


def view_with(db, ontology, user, objects, points=None):
    view = QueryDataView(ontology_id=ontology.id, source_manifest_digest=DATASET_SHA256,
                         metadata_digest=metadata_digest(load_sql_metadata(db, ontology.id)), content_digest=f"rows:{len(points or [])}",
                         graph_key=f"steel_{len(GRAPHS)}", status="ready", created_by=user.id)
    db.add(view); db.flush()
    GRAPHS[view.graph_key] = GraphData(objects, [])
    if points is not None:
        db.add(TimeSeriesSync(ontology_id=ontology.id, data_view_id=view.id, series_id="usage", root_type="Plant", root_id="plant",
                              sensor_type="Meter", sensor_id="main", property_api_name="usage_series", unit="kWh",
                              interpolation="none", points=points, point_count=len(points)))
    db.commit()
    return view


def approve(client, headers, ontology, api_name, logic):
    url = f"/api/v2/ontologies/{ontology.id}/rules"
    body = client.post(url, headers=headers, json={"api_name": api_name, "display_name": api_name, "logic": logic}).json()
    client.post(f"{url}/{body['rule']['id']}/proposals/{body['proposal']['id']}:review", headers=headers, json={"decision": "approve"})
    return body["rule"]["id"]


def evaluate(client, headers, ontology, rule_id, view):
    r = client.post(f"/api/v2/ontologies/{ontology.id}/rules/{rule_id}/evaluate", headers=headers,
                    json={"mode": "commit", "context": {"ontology_id": ontology.id, "data_view_id": view.id}})
    assert r.status_code == 200, r.text
    return r.json()


def all_alerts(client, headers, ontology, rule_id):
    out, offset = [], 0
    while True:
        page = client.get(f"/api/v2/ontologies/{ontology.id}/alerts?rule_id={rule_id}&limit=500&offset={offset}", headers=headers).json()
        out += page["alerts"]
        offset += 500
        if offset >= page["total"]:
            return out


def test_hourly_usage_rule_matches_independent_oracle_and_is_incremental(client, auth_headers, db, admin_user, setup, frame):
    ontology = setup
    threshold = float(frame["Usage_kWh"].quantile(0.95))
    expected = oracle_episodes(frame, threshold)
    assert 50 < len(expected) < 2000  # sanity: a P95 hourly threshold yields a few hundred episodes
    points = [{"timestamp": t.isoformat(), "value": float(v)} for t, v in zip(frame["ts"], frame["Usage_kWh"])]
    plant = {("Plant", "plant"): {"name": "UCI steel plant", "usage_series": "usage"}}
    rule_id = approve(client, auth_headers, ontology, "hourly_peak", {
        "input": {"kind": "base", "type_ref": {"api_name": "Plant"}},
        "blocks": [{"kind": "series_window", "series_property": "usage_series", "window_points": WINDOW,
                    "aggregate": "avg", "op": "gt", "threshold": threshold}],
        "output": {"severity": "high", "properties": {"assignee": "energy-desk"}, "evidence_properties": ["name"]}})

    # Split inside a breaching run so one real episode straddles the two views.
    hit = (frame["Usage_kWh"].rolling(WINDOW).mean() > threshold).tolist()
    half = next(i for i in range(len(points) // 2, len(points) - 1) if hit[i] and hit[i + 1]) + 1
    first = evaluate(client, auth_headers, ontology, rule_id, view_with(db, ontology, admin_user, plant, points[:half]))
    expected_half = oracle_episodes(frame.iloc[:half].reset_index(drop=True), threshold)
    assert first["triggered_count"] == len(expected_half)

    full_view = view_with(db, ontology, admin_user, plant, points)
    second = evaluate(client, auth_headers, ontology, rule_id, full_view)
    alerts = all_alerts(client, auth_headers, ontology, rule_id)
    got = sorted((a["window_start"], a["window_end"], a["evidence"]["peak"]) for a in alerts)
    assert got == sorted(expected), "alerts must equal the independent pandas episodes exactly"
    assert len({(a["subject"]["object_id"], a["window_start"]) for a in alerts}) == len(alerts)
    # An episode open at the half-way watermark is extended, never duplicated.
    watermark = frame["utc"].iloc[half - 1].isoformat()
    straddling = [a for a in alerts if a["occurrence_count"] == 2 and a["window_end"] > watermark]
    assert second["triggered_count"] == len(expected) - len(expected_half)
    assert first["output_count"] == len(expected_half)
    assert expected_half[-1][1] == watermark  # the first view ends mid-episode
    assert len(straddling) == 1 and second["recovered_count"] == 1
    assert evaluate(client, auth_headers, ontology, rule_id, full_view)["replayed"] is True
    assert second["quality"]["series"]["usage"]["irregular_intervals"] == 0


def test_daily_filter_rule_matches_pandas_on_real_days(client, auth_headers, db, admin_user, setup, frame):
    ontology = setup
    days = frame.groupby("day").agg(total_kwh=("Usage_kWh", "sum"), week_status=("WeekStatus", "first"))
    assert len(days) == 365
    weekend_cut = float(days.loc[days.week_status == "Weekend", "total_kwh"].median())
    expected = sorted(str(d) for d, row in days.iterrows() if row.week_status == "Weekend" and round(row.total_kwh, 2) > round(weekend_cut, 2))
    objects = {("EnergyDay", str(d)): {"total_kwh": round(float(row.total_kwh), 2), "week_status": row.week_status}
               for d, row in days.iterrows()}
    rule_id = approve(client, auth_headers, ontology, "busy_weekend", {
        "input": {"kind": "base", "type_ref": {"api_name": "EnergyDay"}},
        "blocks": [{"kind": "filter", "where": {"kind": "and", "items": [
            {"kind": "comparison", "property": {"api_name": "week_status"}, "op": "eq", "value": "Weekend"},
            {"kind": "comparison", "property": {"api_name": "total_kwh"}, "op": "gt", "value": round(weekend_cut, 2)}]}}],
        "output": {"severity": "medium", "evidence_properties": ["total_kwh"]}})
    result = evaluate(client, auth_headers, ontology, rule_id, view_with(db, ontology, admin_user, objects))
    assert result["input_count"] == len(expected)
    got = sorted(a["subject"]["object_id"] for a in all_alerts(client, auth_headers, ontology, rule_id))
    assert got == expected
