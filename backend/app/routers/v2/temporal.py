"""First-class temporal ontology construction API."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.deps import get_current_user
from app.models.ontology import OntologyProject
from app.models.v2.construction import ConstructionRun
from app.services.v2.construction_service import create_run, serialize_run
from app.services.v2.graph.falkordb_service import FalkorDBService

router = APIRouter(prefix="/temporal", dependencies=[Depends(get_current_user)])
# Contract-friendly aliases mounted below /api/v2/ontologies.  Keeping the
# short /api/v2/temporal/... catalog routes and the ontology-scoped routes
# means clients can discover adapters without an ontology while all result
# queries remain naturally scoped to one ontology.
ontology_router = APIRouter(prefix="/{ontology_id}/temporal", dependencies=[Depends(get_current_user)])


def get_falkordb() -> FalkorDBService:
    return FalkorDBService()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class TemporalRunCreate(BaseModel):
    source: str = "bts_site_b"
    dataset_id: str | None = None
    adapter: str = "bts"
    model_name: str | None = None
    time_kind: str = Field(default="instant", pattern="^(ordinal|instant|interval)$")
    event_time_column: str | None = "event_time"
    sequence_column: str | None = "event_seq"
    valid_from_column: str | None = None
    valid_to_column: str | None = None
    entity_id_column: str = "stream_id"
    entity_type: str = "Equipment"
    observation_type: str = "Observation"
    sample_limit: int = Field(default=300, ge=10, le=1000)


@router.get("/adapters")
def adapters():
    return [
        {"name": "bts", "time_kind": "instant", "description": "BTS Site B timestamps mapped to Brick classes."},
        {"name": "cmapss", "time_kind": "ordinal", "description": "C-MAPSS cycle as sequence time; no date is invented."},
        {"name": "scania", "time_kind": "instant", "description": "SCANIA source timestamps normalized to UTC."},
        {"name": "generic", "time_kind": "instant", "description": "User-selected event_time, event_seq or valid interval columns."},
    ]


@router.get("/catalog")
def catalog():
    # ``backend/data`` is mounted into the API container alongside ``app``.
    path = Path(__file__).resolve().parents[3] / "data" / "bts_demo" / "manifest.json"
    manifest = __import__("json").loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return [{"id": "bts_site_b", "name": "BTS Site B（Building TimeSeries）", "kind": "temporal", "time_kind": "instant", "source_url": "https://github.com/cruiseresearchgroup/DIEF_BTS", "manifest": manifest}]


@router.get("/catalog/{dataset_key}/preview")
def catalog_preview(dataset_key: str, limit: int = Query(20, ge=1, le=100)):
    if dataset_key != "bts_site_b":
        raise HTTPException(404, "temporal demo dataset not found")
    path = Path(__file__).resolve().parents[3] / "data" / "bts_demo" / "observations.csv"
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    rows = rows[:limit]
    with path.open(encoding="utf-8") as stream:
        total_rows = max(0, sum(1 for _ in stream) - 1)
    return {"dataset_id": dataset_key, "rows": rows, "columns": list(rows[0].keys()) if rows else [], "total_rows": total_rows}


@router.post("/ontologies/{ontology_id}/runs", status_code=202)
def create_temporal_run(ontology_id: str, body: TemporalRunCreate, background: BackgroundTasks, db: Session = Depends(get_db)):
    if not db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first():
        raise HTTPException(404, "Ontology not found")
    config = body.model_dump(exclude={"dataset_id", "model_name", "source"})
    config["source"] = body.source
    config["time"] = {"time_kind": body.time_kind, "event_time_column": body.event_time_column, "sequence_column": body.sequence_column, "valid_from_column": body.valid_from_column, "valid_to_column": body.valid_to_column}
    run = create_run(db, ontology_id=ontology_id, dataset_id=body.dataset_id, model_name=body.model_name, mode="temporal", config=config)
    try:
        from app.tasks.v2.temporal_construction import run_temporal_construction_task, run_temporal_construction
        if run_temporal_construction_task is not None:
            run_temporal_construction_task.delay(run.id)
        else:
            background.add_task(run_temporal_construction, run.id)
    except Exception as exc:
        background.add_task(run_temporal_construction, run.id)
        run.error = f"Celery unavailable; background fallback: {exc}"
        db.commit()
    return serialize_run(run)


@router.get("/ontologies/{ontology_id}/snapshot")
def snapshot(ontology_id: str, at: str | None = None, limit: int = Query(300, ge=10, le=1000)):
    return get_falkordb().temporal_snapshot(ontology_id, at=at, limit=limit)


@router.get("/ontologies/{ontology_id}/timeline")
def timeline(ontology_id: str, entity_id: str | None = None, limit: int = Query(200, ge=1, le=1000)):
    return get_falkordb().temporal_timeline(ontology_id, entity_id=entity_id, limit=limit)


@router.get("/ontologies/{ontology_id}/diff")
def diff(ontology_id: str, from_at: str, to_at: str, limit: int = Query(300, ge=10, le=1000)):
    return get_falkordb().temporal_diff(ontology_id, from_at=from_at, to_at=to_at, limit=limit)


@router.get("/ontologies/{ontology_id}/growth")
def growth(ontology_id: str, limit: int = Query(200, ge=1, le=1000)):
    return get_falkordb().temporal_growth(ontology_id, limit=limit)


@ontology_router.post("/runs", status_code=202)
def create_temporal_run_alias(ontology_id: str, body: TemporalRunCreate, background: BackgroundTasks, db: Session = Depends(get_db)):
    return create_temporal_run(ontology_id, body, background, db)


@ontology_router.get("/snapshot")
def snapshot_alias(ontology_id: str, at: str | None = None, limit: int = Query(300, ge=10, le=1000)):
    return snapshot(ontology_id, at=at, limit=limit)


@ontology_router.get("/timeline")
def timeline_alias(ontology_id: str, entity_id: str | None = None, limit: int = Query(200, ge=1, le=1000)):
    return timeline(ontology_id, entity_id=entity_id, limit=limit)


@ontology_router.get("/diff")
def diff_alias(ontology_id: str, from_at: str, to_at: str, limit: int = Query(300, ge=10, le=1000)):
    return diff(ontology_id, from_at=from_at, to_at=to_at, limit=limit)


@ontology_router.get("/growth")
def growth_alias(ontology_id: str, limit: int = Query(200, ge=1, le=1000)):
    return growth(ontology_id, limit=limit)
