"""Seed a browsable Machinery demo from the Azure PdM sample (simulated data).

Creates one ontology with 100 Machines, failure/maintenance/error Event
Objects, four telemetry Time Series Properties per machine, a pinned data
view, a maintenance Action and four channel rules (approved, scheduled and
evaluated) so the Object Explorer, alert center, rules page and graph all
have real content.

Bulk data has no API, so it is written through the same adapter and
install_surface path as the acceptance test; rules, reviews, schedules and
evaluations go through the running HTTP API.

    python scripts/seed_machinery_demo.py --pdm /pdm --api http://backend:8000
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import httpx
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal  # noqa: E402
from app.models.entity import Entity  # noqa: E402
from app.models.entity_instance import EntityInstance  # noqa: E402
from app.models.ontology import OntologyProject  # noqa: E402
from app.models.relation import Relation  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.v2.action import OntologyActionType  # noqa: E402
from app.services.v2.azure_pdm import EVENT_LINKS, load_surface, ontology_types  # noqa: E402
from app.services.v2.graph.falkordb_service import FalkorDBService  # noqa: E402
from app.services.v2.object_query.data_views import build_live_view  # noqa: E402
from app.services.v2.object_query.metadata import load_sql_metadata  # noqa: E402
from app.services.v2.temporal_surface import install_surface  # noqa: E402

NAME = "设备运维演示（Azure PdM）"
RULES = {"vibration": ("gt", "comp4", "振动漂移"), "volt": ("gt", "comp1", "电压偏高"),
         "rotate": ("lt", "comp2", "转速偏低"), "pressure": ("gt", "comp3", "压力偏高")}


def thresholds(directory: Path) -> dict[str, float]:
    telemetry = pd.read_csv(directory / "PdM_telemetry.csv").sort_values(["machineID", "datetime"])
    out = {}
    for channel, (op, _, _) in RULES.items():
        rolling = telemetry.groupby("machineID")[channel].transform(lambda s: s.rolling(24).mean())
        out[channel] = float(rolling.mean() + 3 * rolling.std()) if op == "gt" else float(rolling.mean() - 3 * rolling.std())
    return out


def seed_data(directory: Path) -> tuple[str, str, str]:
    graph = FalkorDBService()
    if not graph.available:
        raise SystemExit("FalkorDB is unavailable")
    surface = load_surface(directory)
    with SessionLocal() as db:
        admin = db.query(User).filter_by(role="admin", is_active=True).order_by(User.created_at).first()
        if admin is None:
            raise SystemExit("No admin user; start the backend once so it creates FIRST_ADMIN_USER")
        old = db.query(OntologyProject).filter_by(name=NAME).first()
        if old is not None:
            raise SystemExit(f"Demo already seeded: ontology {old.id}")
        ontology = OntologyProject(name=NAME, domain="manufacturing", description="Azure PdM 模拟数据：100 台设备、事件与遥测", created_by=admin.id)
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
        action = OntologyActionType(ontology_id=ontology.id, name="安排维护", description="把设备维护状态改为已排期", action_category="update",
                                    status="published", enabled=True, target_entity_type="Machine",
                                    parameters=[{"name": "machine", "type": "object", "object_type": "Machine"}],
                                    effects=[{"op": "set_property", "target_parameter": "machine", "property": "maintenance_status", "static_value": "scheduled"}])
        db.add(action)
        db.commit()
        graph.upsert_instances(ontology.id, surface.machines)
        digest = hashlib.sha256(json.dumps(surface.manifest["files"], sort_keys=True).encode()).hexdigest()
        view = build_live_view(db, graph, load_sql_metadata(db, ontology.id), ontology.id, admin.id, digest, 30 * 86400)
        install_surface(db, graph, view, surface.event_nodes, surface.event_links, surface.syncs)
        print(f"data: ontology={ontology.id} view={view.id} counts={surface.manifest['counts']}")
        return ontology.id, view.id, action.id


def seed_rules(api: str, ontology_id: str, action_id: str, limits: dict[str, float]) -> None:
    with httpx.Client(base_url=f"{api}/api/v2/ontologies/{ontology_id}", timeout=600) as client:
        for channel, (op, component, label) in RULES.items():
            logic = {"input": {"kind": "base", "type_ref": {"api_name": "Machine"}},
                     "blocks": [{"kind": "series_window", "series_property": f"{channel}_series", "window_points": 24,
                                 "aggregate": "avg", "op": op, "threshold": round(limits[channel], 3)}],
                     "output": {"severity": "high" if channel == "vibration" else "medium", "properties": {"component": component},
                                "evidence_properties": ["model"]}}
            if channel == "vibration":
                logic["action"] = {"action_id": action_id, "object_parameter": "machine"}
            created = client.post("/rules", json={"api_name": f"{channel}_drift", "display_name": label,
                                                  "description": f"24 小时均值越过 ±3σ，预警 {component}", "logic": logic})
            created.raise_for_status()
            rule = created.json()["rule"]
            client.post(f"/rules/{rule['id']}/proposals/{created.json()['proposal']['id']}:review", json={"decision": "approve"}).raise_for_status()
            client.post(f"/rules/{rule['id']}/schedule", json={"interval_minutes": 60, "source": "latest_view"}).raise_for_status()
            result = client.post(f"/rules/{rule['id']}/evaluate:latest")
            result.raise_for_status()
            print(f"rule {label}: {result.json()['output_count']} episodes, {result.json()['triggered_count']} alerts")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdm", type=Path, required=True)
    parser.add_argument("--api", default="http://backend:8000")
    args = parser.parse_args()
    ontology_id, view_id, action_id = seed_data(args.pdm)
    seed_rules(args.api, ontology_id, action_id, thresholds(args.pdm))
    print(f"OPEN /ontologies/{ontology_id}?tab=objects&dataView={view_id}")


if __name__ == "__main__":
    main()
