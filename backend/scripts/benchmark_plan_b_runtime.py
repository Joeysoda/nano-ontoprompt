"""Measure Plan B runtime paths against Postgres, FalkorDB, and the HTTP API.

Read measurements use an existing ontology. Action measurements use a
disposable ontology and graph that are removed even when a request fails.
Run this inside the backend container while the API is listening::

    python -m scripts.benchmark_plan_b_runtime --runs 25 --warmup 3
"""
from __future__ import annotations

import argparse
import json
import math
import os
import platform
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from uuid import uuid4

from app.database import SessionLocal
from app.models.entity import Entity
from app.models.entity_instance import EntityInstance
from app.models.ontology import OntologyProject
from app.models.user import User
from app.models.v2.action import OntologyActionRun, OntologyActionType
from app.services.v2.graph.falkordb_service import FalkorDBService


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def summary(values: list[float], requests: int) -> dict:
    return {
        "samples": len(values),
        "requests": requests,
        "p50_ms": round(percentile(values, 0.50), 3),
        "p95_ms": round(percentile(values, 0.95), 3),
        "p99_ms": round(percentile(values, 0.99), 3),
        "max_ms": round(max(values), 3),
    }


def memory_mb() -> float | None:
    for path in ("/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory/memory.usage_in_bytes"):
        try:
            with open(path, encoding="ascii") as stream:
                return round(int(stream.read().strip()) / 1024 / 1024, 3)
        except (FileNotFoundError, PermissionError, ValueError):
            continue
    return None


class Api:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.requests = 0
        self.peak_memory_mb = memory_mb()

    def post(self, path: str, payload: dict) -> tuple[dict, float]:
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        started = time.perf_counter()
        self.requests += 1
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = json.loads(response.read())
                if response.status != 200:
                    raise RuntimeError(f"{path} returned {response.status}: {body}")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"{path} returned {exc.code}: {exc.read().decode('utf-8', 'replace')}") from exc
        elapsed = (time.perf_counter() - started) * 1000
        current = memory_mb()
        if current is not None:
            self.peak_memory_mb = max(self.peak_memory_mb or current, current)
        return body, elapsed


def load_payload(ontology_id: str, expression: dict) -> dict:
    return {
        "expression": expression,
        "context": {"ontology_id": ontology_id, "consistency": "live"},
        "read": {"page_size": 50},
    }


def measure_reads(api: Api, ontology_id: str, runs: int, warmup: int) -> dict:
    base = {"kind": "base", "type_ref": {"kind": "object", "api_name": "Demand"}}
    first, _ = api.post(f"/api/v2/ontologies/{ontology_id}/object-query/load", load_payload(ontology_id, base))
    if not first.get("objects"):
        raise RuntimeError("The read fixture has no Demand objects")
    first_id = first["objects"][0]["object_id"]
    expressions = {
        "explorer_load": base,
        "explorer_pivot": {"kind": "traverse", "input": base, "link": {"api_name": "HAS_ITEM"}},
        "explorer_compare": {
            "kind": "subtract",
            "base": base,
            "subtract": [{
                "kind": "static",
                "type_ref": {"kind": "object", "api_name": "Demand"},
                "object_ids": [first_id],
            }],
        },
    }
    output = {}
    for name, expression in expressions.items():
        path = f"/api/v2/ontologies/{ontology_id}/object-query/load"
        for _ in range(warmup):
            api.post(path, load_payload(ontology_id, expression))
        before = api.requests
        samples = [api.post(path, load_payload(ontology_id, expression))[1] for _ in range(runs)]
        output[name] = summary(samples, api.requests - before)
    return output


def create_action_fixture() -> tuple[str, str, str, FalkorDBService]:
    ontology_id = f"plan-b-benchmark-{uuid4().hex}"
    entity_id = f"type-{uuid4().hex}"
    object_id = f"order-{uuid4().hex}"
    action_id = f"action-{uuid4().hex}"
    graph = FalkorDBService()
    if not graph.available:
        raise RuntimeError("FalkorDB is unavailable")
    with SessionLocal() as db:
        admin = db.query(User).filter(User.role == "admin", User.is_active.is_(True)).order_by(User.created_at).first()
        if not admin:
            raise RuntimeError("No active local admin exists")
        db.add(OntologyProject(id=ontology_id, name="Plan B benchmark", domain="test", status="published", created_by=admin.id))
        db.flush()
        db.add(Entity(id=entity_id, ontology_id=ontology_id, name_cn="Order", name_en="Order", type="EntityType", properties={}))
        db.flush()
        db.add(EntityInstance(id=object_id, entity_id=entity_id, ontology_id=ontology_id, row_identity=object_id, row_data={"status": "pending"}))
        db.add(OntologyActionType(
            id=action_id,
            ontology_id=ontology_id,
            name="Set benchmark status",
            target_entity_type="Order",
            action_category="benchmark",
            parameters=[
                {"name": "target", "type": "object", "object_type": "Order", "required": True},
                {"name": "value", "type": "string", "required": True},
            ],
            effects=[{
                "op": "set_property",
                "target_parameter": "target",
                "property": "status",
                "value_parameter": "value",
            }],
            status="published",
            enabled=True,
            created_by=admin.id,
        ))
        db.commit()
    graph.upsert_instances(ontology_id, [{"id": object_id, "entity_type": "Order", "properties": {"status": "pending"}}])
    return ontology_id, object_id, action_id, graph


def cleanup_action_fixture(ontology_id: str, graph: FalkorDBService) -> None:
    with SessionLocal() as db:
        db.query(OntologyActionRun).filter_by(ontology_id=ontology_id).delete(synchronize_session=False)
        db.query(OntologyActionType).filter_by(ontology_id=ontology_id).delete(synchronize_session=False)
        db.query(EntityInstance).filter_by(ontology_id=ontology_id).delete(synchronize_session=False)
        db.query(Entity).filter_by(ontology_id=ontology_id).delete(synchronize_session=False)
        db.query(OntologyProject).filter_by(id=ontology_id).delete(synchronize_session=False)
        db.commit()
    if not graph.delete_graph(ontology_id):
        raise RuntimeError(f"Failed to delete disposable graph for {ontology_id}")


def measure_actions(api: Api, runs: int, warmup: int) -> dict:
    ontology_id, object_id, action_id, graph = create_action_fixture()
    path = f"/api/v2/ontologies/{ontology_id}/actions/{action_id}"
    current = "pending"

    def cycle(measure: bool) -> tuple[float, float] | None:
        nonlocal current
        value = "reviewing" if current == "pending" else "pending"
        payload = {
            "target_object_id": object_id,
            "parameters": {"target": {"object_type": "Order", "object_id": object_id}, "value": value},
            "context": {"ontology_id": ontology_id, "consistency": "live"},
        }
        preview = api.post(path + "/preview", payload)[1]
        submit = api.post(path + "/run", payload)[1]
        current = value
        return (preview, submit) if measure else None

    try:
        for _ in range(warmup):
            cycle(False)
        before = api.requests
        samples = [cycle(True) for _ in range(runs)]
        previews = [item[0] for item in samples if item]
        submits = [item[1] for item in samples if item]
        return {
            "action_preview": summary(previews, runs),
            "action_submit": summary(submits, runs),
            "combined_requests": api.requests - before,
            "fixture": "disposable Postgres + FalkorDB ontology",
        }
    finally:
        cleanup_action_fixture(ontology_id, graph)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--ontology-id", default="frepple-d7eb98078882b234c395fd05")
    parser.add_argument("--runs", type=int, default=25)
    parser.add_argument("--warmup", type=int, default=3)
    args = parser.parse_args()
    if args.runs < 2 or args.warmup < 0:
        raise SystemExit("runs must be at least 2 and warmup cannot be negative")
    api = Api(args.base_url)
    memory_before = memory_mb()
    report = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "ontology_id": args.ontology_id,
            "runs": args.runs,
            "warmup": args.warmup,
        },
        "operations": measure_reads(api, args.ontology_id, args.runs, args.warmup),
    }
    report["operations"].update(measure_actions(api, args.runs, args.warmup))
    report["resource"] = {
        "memory_before_mb": memory_before,
        "memory_after_mb": memory_mb(),
        "memory_peak_observed_mb": api.peak_memory_mb,
        "total_http_requests": api.requests,
    }
    report["browser_evidence"] = {
        "cancellation_recovery": "object-explorer-workflow: slow load recovers with one request",
        "duplicate_submit": "object-explorer-workflow: busy submit produces one run request",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
