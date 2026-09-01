"""Temporal dataset and construction APIs.

ICEWS is the product-facing temporal source in this release.  The old BTS,
C-MAPSS and SCANIA adapters remain available for regression callers, but no
longer appear in the default temporal catalog.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.deps import get_current_user, require_admin
from app.models.ontology import OntologyProject
from app.models.v2.construction import ConstructionRun
from app.models.v2.dataset import Dataset, DatasetVersion
from app.services.v2.construction_service import create_run, serialize_run
from app.services.v2.datasets.icews_adapter import (
    ICEWS_DATASET_NAME,
    ICEWS_DOI,
    ICEWS_DOWNLOAD_URL,
    ICEWS_SOURCE_ID,
    ICEWS_FILE_ID,
    ICEWS_SHA256,
    ICEWS_COLUMNS,
    icews_summary,
    normalize_icews_rows,
    parse_icews_tsv,
)
from app.services.v2.datasets.icews_installer import find_icews_dataset, install_icews_dataset
from app.services.v2.graph.falkordb_service import FalkorDBService
from app.services.storage_service import get_storage_service

router = APIRouter(prefix="/temporal", dependencies=[Depends(get_current_user)])
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
    # ``source`` is retained for callers of the first BTS version; the new UI
    # always sends ``source_id=icews_2023_demo`` explicitly.  Keeping the old
    # defaults here means a legacy BTS client does not silently switch data
    # sources just because the product catalog changed.
    source: str = "bts_site_b"
    source_id: str | None = None
    dataset_id: str | None = None
    adapter: str = "bts"
    model_name: str | None = None
    time_kind: str = Field(default="instant", pattern="^(ordinal|instant|interval)$")
    time_precision: str = "day"
    event_time_column: str | None = "event_time"
    sequence_column: str | None = "event_seq"
    valid_from_column: str | None = None
    valid_to_column: str | None = None
    entity_id_column: str = "stream_id"
    entity_type: str = "Equipment"
    observation_type: str = "Observation"
    sample_limit: int = Field(default=300, ge=1, le=100000)
    filters: dict[str, Any] = Field(default_factory=dict)
    field_mapping: dict[str, Any] = Field(default_factory=dict)


def _source_id(body: TemporalRunCreate) -> str:
    return body.source_id or body.source or ICEWS_SOURCE_ID


def _source_item(db: Session, source_id: str) -> dict[str, Any]:
    if source_id == ICEWS_SOURCE_ID:
        dataset = find_icews_dataset(db)
        manifest = dict(dataset.schema_json or {}) if dataset else {}
        summary = manifest.get("summary") or {}
        return {
            "id": ICEWS_SOURCE_ID,
            "name": ICEWS_DATASET_NAME,
            "kind": "temporal",
            "time_kind": "instant",
            "time_precision": "day",
            "source_url": f"https://doi.org/{ICEWS_DOI}",
            "download_url": ICEWS_DOWNLOAD_URL,
            "doi": ICEWS_DOI,
            "file_id": ICEWS_FILE_ID,
            "sha256": ICEWS_SHA256,
            "installed": bool(dataset),
            "dataset_id": dataset.id if dataset else None,
            "records": summary.get("rows") if dataset else None,
            "date_from": summary.get("time_from") if dataset else None,
            "date_to": summary.get("time_to") if dataset else None,
            "participants": summary.get("participants") if dataset else None,
            "categories": summary.get("categories") if dataset else None,
            "columns": list(ICEWS_COLUMNS),
            "supports": ["instant"],
            "manifest": manifest,
        }
    if source_id.startswith("dataset:"):
        dataset_id = source_id.split(":", 1)[1]
        dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
        if dataset is not None:
            version = db.query(DatasetVersion).filter(DatasetVersion.id == dataset.latest_version_id).first() if dataset.latest_version_id else None
            if version is None:
                version = db.query(DatasetVersion).filter(DatasetVersion.dataset_id == dataset.id).order_by(DatasetVersion.version_no.desc()).first()
            columns: list[str] = []
            if version and version.storage_uri:
                try:
                    rows = _parse_dataset_rows(get_storage_service().get_object(version.storage_uri))
                    columns = [key for key in rows[0].keys() if not key.startswith("_")] if rows else []
                except Exception:
                    columns = []
            return {
                "id": source_id, "name": dataset.name, "kind": "existing_dataset",
                "installed": bool(version), "dataset_id": dataset.id,
                "records": version.rowcount if version else None,
                "columns": columns, "supports": ["instant", "ordinal", "interval"],
                "source": "existing_dataset",
            }
    return {"id": source_id, "name": source_id, "kind": "temporal", "installed": False, "supports": []}


def _parse_dataset_rows(raw: bytes) -> list[dict[str, Any]]:
    """Read a DatasetVersion for the generic temporal entry point.

    Dataset uploads are stored as ``data.bin`` without their extension in the
    object key, so the parser identifies JSON, Excel and delimited text from
    the bytes instead of trusting the storage filename.
    """
    if raw[:2] == b"PK":
        try:
            import openpyxl
            workbook = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
            sheet = workbook.active
            values = list(sheet.iter_rows(values_only=True))
            workbook.close()
            if not values:
                return []
            headers = [str(value) if value is not None else f"column_{index}" for index, value in enumerate(values[0])]
            return [
                {**{headers[index]: value if value is not None else "" for index, value in enumerate(row) if index < len(headers)}, "_source_row_index": row_index}
                for row_index, row in enumerate(values[1:])
                if any(value not in (None, "") for value in row)
            ]
        except Exception as exc:
            raise ValueError(f"unable to parse XLSX dataset: {exc}") from exc
    text = raw.decode("utf-8-sig", errors="replace")
    stripped = text.lstrip()
    if stripped.startswith("[") or stripped.startswith("{"):
        data = json.loads(stripped)
        items = data if isinstance(data, list) else [data]
        return [{**dict(item), "_source_row_index": index} for index, item in enumerate(items) if isinstance(item, dict)]
    first_line = text.splitlines()[0] if text.splitlines() else ""
    delimiter = "\t" if "\t" in first_line and "," not in first_line else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if not reader.fieldnames:
        return []
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(reader):
        if any(str(value or "").strip() for value in row.values()):
            rows.append({**dict(row), "_source_row_index": index})
    return rows


def _existing_dataset_sources(db: Session) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for dataset in db.query(Dataset).filter(Dataset.kind.in_(["structured", "semi"])).order_by(Dataset.created_at.desc()).all():
        manifest = dataset.schema_json or {}
        if manifest.get("source_id") == ICEWS_SOURCE_ID:
            continue
        item = _source_item(db, f"dataset:{dataset.id}")
        if item.get("installed"):
            items.append(item)
    return items


@router.get("/adapters")
def adapters():
    from app.services.v2.datasets.temporal_adapters import list_adapters
    return list_adapters()


@router.get("/sources")
def sources(db: Session = Depends(get_db)):
    """List ICEWS, existing tabular datasets and their temporal entry state."""
    return [_source_item(db, ICEWS_SOURCE_ID), *_existing_dataset_sources(db)]


@router.post("/sources/icews_2023_demo/install", status_code=201)
def install_icews(_admin=Depends(require_admin), db: Session = Depends(get_db)):
    try:
        return install_icews_dataset(db)
    except (ValueError, OSError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/sources/{source_id}/preview")
def source_preview(
    source_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    date_from: str | None = None,
    date_to: str | None = None,
    country: str | None = None,
    event_type: str | None = None,
    cameo_code: str | None = None,
    intensity_min: float | None = None,
    intensity_max: float | None = None,
    scenario: str | None = None,
    db: Session = Depends(get_db),
):
    if source_id != ICEWS_SOURCE_ID and not source_id.startswith("dataset:"):
        raise HTTPException(404, "temporal source not found")
    if source_id.startswith("dataset:"):
        dataset_id = source_id.split(":", 1)[1]
        dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
        version = db.query(DatasetVersion).filter(DatasetVersion.id == dataset.latest_version_id).first() if dataset and dataset.latest_version_id else None
        if version is None and dataset is not None:
            version = db.query(DatasetVersion).filter(DatasetVersion.dataset_id == dataset.id).order_by(DatasetVersion.version_no.desc()).first()
        if not dataset or not version or not version.storage_uri:
            raise HTTPException(404, "dataset source not found")
        rows = _parse_dataset_rows(get_storage_service().get_object(version.storage_uri))
        page = rows[offset:offset + limit]
        return {
            "source_id": source_id, "dataset_id": dataset.id, "offset": offset,
            "limit": limit, "rows": page,
            "columns": list(rows[0].keys()) if rows else [],
            "total_rows": len(rows), "total_source_rows": len(rows),
            "summary": {"rows": len(rows), "time_kind": "instant", "time_precision": "source-defined"},
        }
    dataset = find_icews_dataset(db)
    if not dataset or not dataset.latest_version_id:
        raise HTTPException(status_code=409, detail={"error": "SOURCE_NOT_INSTALLED", "message": "请先安装 ICEWS 官方样例"})
    version = db.query(DatasetVersion).filter(DatasetVersion.id == dataset.latest_version_id).first()
    if version is None:
        version = db.query(DatasetVersion).filter(DatasetVersion.dataset_id == dataset.id).order_by(DatasetVersion.version_no.desc()).first()
    if not version or not version.storage_uri:
        raise HTTPException(422, "ICEWS dataset version has no storage object")
    rows = parse_icews_tsv(get_storage_service().get_object(version.storage_uri))
    filters = {
        "date_from": date_from, "date_to": date_to, "country": country,
        "event_type": event_type, "cameo_code": cameo_code,
        "intensity_min": intensity_min, "intensity_max": intensity_max,
        "scenario": scenario,
    }
    from app.tasks.v2.temporal_construction import _filter_icews_rows
    selected = _filter_icews_rows(rows, filters)
    page = selected[offset:offset + limit]
    return {
        "source_id": source_id, "dataset_id": dataset.id, "offset": offset,
        "limit": limit, "rows": page, "columns": list(rows[0].keys()) if rows else [],
        "total_rows": len(selected), "total_source_rows": len(rows),
        "summary": icews_summary(normalize_icews_rows(selected)[0]),
    }


@router.get("/catalog")
def catalog(db: Session = Depends(get_db)):
    """Compatibility alias; product catalog now contains ICEWS only."""
    return [_source_item(db, ICEWS_SOURCE_ID), *_existing_dataset_sources(db)]


@router.get("/catalog/{dataset_key}/preview")
def catalog_preview(dataset_key: str, limit: int = Query(25, ge=1, le=100)):
    if dataset_key != ICEWS_SOURCE_ID and not dataset_key.startswith("dataset:"):
        raise HTTPException(404, "temporal source not found")
    with SessionLocal() as db:
        return source_preview(dataset_key, offset=0, limit=limit, db=db)


def _canonical_config(body: TemporalRunCreate, source_id: str, dataset_id: str | None) -> tuple[dict[str, Any], str]:
    config = body.model_dump(exclude={"model_name", "source", "source_id", "dataset_id"})
    config.update({"source": source_id, "source_id": source_id, "dataset_id": dataset_id})
    config["time"] = {
        "time_kind": body.time_kind, "time_precision": body.time_precision,
        "event_time_column": body.event_time_column, "sequence_column": body.sequence_column,
        "valid_from_column": body.valid_from_column, "valid_to_column": body.valid_to_column,
    }
    payload = json.dumps(config, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return config, hashlib.sha256(payload).hexdigest()


def _find_completed_run(db: Session, ontology_id: str, config_hash: str) -> ConstructionRun | None:
    runs = db.query(ConstructionRun).filter(
        ConstructionRun.ontology_id == ontology_id,
        ConstructionRun.mode == "temporal", ConstructionRun.status == "completed",
    ).order_by(ConstructionRun.completed_at.desc()).all()
    return next((run for run in runs if (run.config or {}).get("config_hash") == config_hash), None)


def create_temporal_run(ontology_id: str, body: TemporalRunCreate, background: BackgroundTasks, db: Session):
    if not db.query(OntologyProject.id).filter(OntologyProject.id == ontology_id).first():
        raise HTTPException(404, "Ontology not found")
    source_id = _source_id(body)
    dataset_id = body.dataset_id
    if source_id == ICEWS_SOURCE_ID:
        dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first() if dataset_id else find_icews_dataset(db)
        if not dataset:
            raise HTTPException(status_code=409, detail={"error": "SOURCE_NOT_INSTALLED", "message": "请先安装 ICEWS 官方样例"})
        dataset_id = dataset.id
        if body.time_kind != "instant":
            raise HTTPException(422, "ICEWS 2023 demo only supports Instant day precision")
    elif source_id.startswith("dataset:"):
        dataset_id = source_id.split(":", 1)[1]
        if not db.query(Dataset.id).filter(Dataset.id == dataset_id).first():
            raise HTTPException(404, "Dataset source not found")
    config, config_hash = _canonical_config(body, source_id, dataset_id)
    config["config_hash"] = config_hash
    existing = _find_completed_run(db, ontology_id, config_hash)
    if existing:
        return {**serialize_run(existing), "reused": True}
    run = create_run(db, ontology_id=ontology_id, dataset_id=dataset_id, model_name=body.model_name, mode="temporal", config=config)
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


@router.post("/ontologies/{ontology_id}/runs", status_code=202)
def create_temporal_run_legacy(ontology_id: str, body: TemporalRunCreate, background: BackgroundTasks, db: Session = Depends(get_db)):
    return create_temporal_run(ontology_id, body, background, db)


@router.get("/runs")
def list_temporal_runs(ontology_id: str | None = None, limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    query = db.query(ConstructionRun).filter(ConstructionRun.mode == "temporal")
    if ontology_id:
        query = query.filter(ConstructionRun.ontology_id == ontology_id)
    return [serialize_run(run) for run in query.order_by(ConstructionRun.created_at.desc()).limit(limit).all()]


@ontology_router.post("/runs", status_code=202)
def create_temporal_run_alias(ontology_id: str, body: TemporalRunCreate, background: BackgroundTasks, db: Session = Depends(get_db)):
    return create_temporal_run(ontology_id, body, background, db)


@ontology_router.get("/runs")
def list_temporal_runs_alias(ontology_id: str, limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    """Ontology-scoped history route used by integrations and the wizard."""
    return list_temporal_runs(ontology_id=ontology_id, limit=limit, db=db)


@ontology_router.get("/snapshot")
def snapshot(
    ontology_id: str, at: str | None = None,
    mode: str = Query("cumulative", pattern="^(window|cumulative)$"),
    date_from: str | None = None, date_to: str | None = None,
    country: str | None = None, event_type: str | None = None,
    category: str | None = None,
    intensity_min: float | None = None, intensity_max: float | None = None,
    participant: str | None = None, limit: int = Query(200, ge=1, le=1000),
):
    return get_falkordb().temporal_snapshot(
        ontology_id, at=at, mode=mode, date_from=date_from, date_to=date_to,
        country=country, event_type=event_type, category=category,
        intensity_min=intensity_min,
        intensity_max=intensity_max, participant=participant, limit=limit,
    )


@ontology_router.get("/timeline")
def timeline(ontology_id: str, entity_id: str | None = None, category: str | None = None, limit: int = Query(200, ge=1, le=1000)):
    return get_falkordb().temporal_timeline(ontology_id, entity_id=entity_id, category=category, limit=limit)


@ontology_router.get("/diff")
def diff(ontology_id: str, from_at: str, to_at: str, limit: int = Query(300, ge=1, le=1000)):
    return get_falkordb().temporal_diff(ontology_id, from_at=from_at, to_at=to_at, limit=limit)


@ontology_router.get("/growth")
def growth(ontology_id: str, limit: int = Query(200, ge=1, le=1000)):
    return get_falkordb().temporal_growth(ontology_id, limit=limit)


# Keep the first release's short aliases working for saved notebooks and
# browser bookmarks.  New code should use the ontology-scoped routes above;
# these wrappers deliberately expose the same ICEWS-capable query options.
@router.get("/ontologies/{ontology_id}/snapshot")
def snapshot_legacy(
    ontology_id: str, at: str | None = None,
    mode: str = Query("cumulative", pattern="^(window|cumulative)$"),
    date_from: str | None = None, date_to: str | None = None,
    country: str | None = None, event_type: str | None = None,
    category: str | None = None,
    intensity_min: float | None = None, intensity_max: float | None = None,
    participant: str | None = None, limit: int = Query(200, ge=1, le=1000),
):
    return get_falkordb().temporal_snapshot(
        ontology_id, at=at, mode=mode, date_from=date_from, date_to=date_to,
        country=country, event_type=event_type, category=category,
        intensity_min=intensity_min,
        intensity_max=intensity_max, participant=participant, limit=limit,
    )


@router.get("/ontologies/{ontology_id}/timeline")
def timeline_legacy(ontology_id: str, entity_id: str | None = None, category: str | None = None, limit: int = Query(200, ge=1, le=1000)):
    return get_falkordb().temporal_timeline(ontology_id, entity_id=entity_id, category=category, limit=limit)


@router.get("/ontologies/{ontology_id}/diff")
def diff_legacy(ontology_id: str, from_at: str, to_at: str, limit: int = Query(300, ge=1, le=1000)):
    return get_falkordb().temporal_diff(ontology_id, from_at=from_at, to_at=to_at, limit=limit)


@router.get("/ontologies/{ontology_id}/growth")
def growth_legacy(ontology_id: str, limit: int = Query(200, ge=1, le=1000)):
    return get_falkordb().temporal_growth(ontology_id, limit=limit)
