from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.models.v2.derived_projection import DerivedProjectionRun
from app.schemas.v2.derived_projection import ProjectionSpec
from app.services.v2 import derived_projections as service
from app.services.v2.logic_assets import ASSET_DEFINITIONS
from app.services.v2.object_query.normalize import stable_hash


def spec(**changes):
    value = {
        "query_api_name": "equipment_readings", "query_version": "1.0.0", "query_params": {},
        "time_range": "all", "limit": 100, "max_rows": 100,
        "mapping": {"fields": [{"target": "value", "source": ["sensor_value"], "data_type": "number",
                                  "required": True, "nullable": False}]},
        "asset_key": "observation_summary_v1", "asset_version": "1.0.0",
        "inputs": {"values": {"kind": "column", "field": "value"},
                   "anomaly_threshold": {"kind": "literal", "value": 5.0}},
    }
    value.update(changes)
    return ProjectionSpec.model_validate(value)


def test_projection_input_bindings_are_explicit_and_strict():
    value = spec()
    assert service.assemble_inputs(value, [{"value": 1.0}, {"value": 3.0}]) == {
        "values": [1.0, 3.0], "anomaly_threshold": 5.0,
    }
    with pytest.raises(ValidationError):
        spec(inputs={"values": {"kind": "column", "field": "guessed_name"}})
    with pytest.raises(ValidationError):
        spec(inputs={"values": {"kind": "python", "expression": "row.value"}})


class FakeDb:
    def __init__(self):
        self.added = []

    def add(self, row):
        if getattr(row, "id", None) is None:
            row.id = f"generated-{len(self.added) + 1}"
        self.added.append(row)

    def flush(self): pass
    def commit(self): pass
    def refresh(self, row): pass


def asset():
    definition = next(item for item in ASSET_DEFINITIONS if item["asset_key"] == "observation_summary_v1")
    defaults = {"name": "summary", "kind": "statistics", "description": "", "interface_key": "stats.summary",
                "interface_version": "1.0.0", "executor_type": "local", "bindings": {}, "binding_spec": {},
                "config": {}, "version": "1.0.0", "status": "published", "deterministic": True, "side_effect": False}
    return SimpleNamespace(id="asset-1", **{**defaults, **definition})


def test_projection_run_records_pinned_evidence_and_derived_facts(monkeypatch):
    projection_spec = spec()
    projection = SimpleNamespace(id="projection-1", ontology_id="ontology-1", owner_id="user-1",
                                 spec=projection_spec.model_dump(mode="json"), spec_digest=stable_hash(projection_spec.model_dump(mode="json")),
                                 last_successful_run_id=None, schedule_error=None, last_run_at=None)
    version = SimpleNamespace(version="1.0.0", definition_digest="query-digest")
    monkeypatch.setattr(service, "resolve", lambda db, ontology_id, requested: (version, asset(), "objects"))
    monkeypatch.setattr(service, "run_query", lambda *args, **kwargs: {
        "data_view_id": "view-1", "outputs": {"objects": [{"id": "r1", "sensor_value": 1.0},
                                                              {"id": "r2", "sensor_value": 3.0}]}})
    db = FakeDb()
    run = service.run_projection(db, projection, SimpleNamespace(id="user-1"), SimpleNamespace())

    assert run.status == "completed"
    assert run.query_version == "1.0.0" and run.data_view_id == "view-1"
    assert run.output["count"] == 2 and run.output["mean"] == 2.0
    assert run.input_digest and run.dependency_digest
    assert projection.last_successful_run_id == run.id
    assert any(type(row).__name__ == "LogicDerivedFact" and row.subject_id == "projection-1" for row in db.added)


def test_due_only_selects_active_elapsed_schedules():
    now = datetime.now(timezone.utc)
    rows = [
        SimpleNamespace(id="due", status="active", interval_minutes=5, last_run_at=now - timedelta(minutes=6)),
        SimpleNamespace(id="later", status="active", interval_minutes=10, last_run_at=now - timedelta(minutes=2)),
        SimpleNamespace(id="paused", status="paused", interval_minutes=5, last_run_at=None),
    ]

    class Query:
        def filter(self, *args): return self
        def all(self): return [row for row in rows if row.status == "active" and row.interval_minutes is not None]
    db = SimpleNamespace(query=lambda model: Query())
    assert [row.id for row in service.due(db, now)] == ["due"]
