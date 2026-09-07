"""Temporal dataset and construction APIs.

FactoryNet CNC is the product-facing temporal source in this release. Legacy
ICEWS/BTS callers remain import-compatible but are not shown in the catalog.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import uuid
import os
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, UploadFile, File
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.deps import get_current_user, require_admin, require_editor
from app.models.user import User
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
from app.services.v2.datasets.factorynet_installer import (
    FACTORYNET_DATASET_NAME, FACTORYNET_FILE, FACTORYNET_LICENSE, FACTORYNET_SHA256,
    FACTORYNET_SOURCE_ID, FACTORYNET_URL, find_factorynet_dataset, install_factorynet_dataset,
)
from app.models.v2.temporal_profile import TemporalDatasetProfile
from app.services.v2.temporal_profile_service import serialize_profile, run_profile, profile_rows
from app.services.v2.dataset_service import DatasetService
from app.config import settings
from app.services.v2.graph.falkordb_service import FalkorDBService
from app.services.storage_service import get_storage_service

router = APIRouter(prefix="/temporal", dependencies=[Depends(get_current_user)])
ontology_router = APIRouter(prefix="/{ontology_id}/temporal", dependencies=[Depends(get_current_user)])

CMAPSS_SOURCE_ID = "cmapss_fd004"
CMAPSS_DATASET_NAME = "C-MAPSS FD004 时序案例包"
CMAPSS_SOURCE_URL = "https://data.nasa.gov/dataset/?organization=nasa&res_format=ZIP&tags=ivhm&tags=phm&tags=prognostics"


def get_falkordb() -> FalkorDBService:
    return FalkorDBService()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _read_source_object(storage_uri: str | None) -> bytes:
    """Translate missing-object failures into a repairable API response."""
    if not storage_uri:
        raise HTTPException(424, detail={"error": "STORAGE_OBJECT_MISSING", "message": "数据版本没有存储地址；可运行存储修复后重试"})
    try:
        return get_storage_service().get_object(storage_uri)
    except Exception as exc:
        raise HTTPException(424, detail={"error": "STORAGE_OBJECT_MISSING", "message": f"源文件不在当前对象存储：{exc}。可运行存储修复后重试", "storage_uri": storage_uri}) from exc


class TemporalRunCreate(BaseModel):
    # ``source`` is retained for callers of the first BTS version; the new UI
    # always sends ``source_id=icews_2023_demo`` explicitly.  Keeping the old
    # defaults here means a legacy BTS client does not silently switch data
    # sources just because the product catalog changed.
    source: str = "factorynet_cnc"
    source_id: str | None = None
    dataset_id: str | None = None
    adapter: str = "factorynet"
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
    profile_id: str | None = None
    model_config = {"protected_namespaces": ()}


class TemporalAtomicRunCreate(TemporalRunCreate):
    ontology_mode: str = Field(default="create", pattern="^(create|reuse)$")
    ontology_id: str | None = None
    ontology_name: str = "FactoryNet CNC 时序本体"
    ontology_domain: str = "制造"
    ontology_description: str | None = None
    model_config = {"protected_namespaces": ()}


class TemporalQueryBody(BaseModel):
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=25, ge=1, le=200)
    equals: dict[str, Any] = Field(default_factory=dict)
    contains: dict[str, str] = Field(default_factory=dict)
    ranges: dict[str, dict[str, Any]] = Field(default_factory=dict)
    max_records: int | None = Field(default=None, ge=1, le=1000000)
    entity_column: str | None = None


class TemporalAnalysisRequest(BaseModel):
    """Privacy is chosen before profiling so private data never reaches M3."""
    privacy_level: str = Field(default="standard", pattern="^(standard|private)$")


def _queue_temporal_profile(profile_id: str, background: BackgroundTasks | None) -> None:
    if os.getenv("CELERY_ENABLED", "").lower() in {"1", "true", "yes"}:
        try:
            from app.tasks.v2.workbench import run_temporal_profile_task
            run_temporal_profile_task.delay(profile_id)
            return
        except Exception:
            # The caller keeps the profile queued and exposes a retryable
            # state; it must not silently call another model or mark success.
            return
    if background is not None:
        from app.services.v2.temporal_profile_service import run_profile
        background.add_task(run_profile, profile_id)


def _source_id(body: TemporalRunCreate) -> str:
    return body.source_id or body.source or ICEWS_SOURCE_ID


def _find_cmapss_parts(db: Session) -> tuple[Dataset | None, Dataset | None]:
    """Return the raw sensor table and its equipment dimension.

    The historical database stores the two files as separate Dataset rows;
    the user-facing source is one package so a construction run can preserve
    the equipment_id relationship without asking the user to open a second
    connection.
    """
    rows = db.query(Dataset).filter(Dataset.name.in_(["sensor_readings", "equipment"])).all()
    by_name = {str(item.name).lower(): item for item in rows}
    return by_name.get("sensor_readings"), by_name.get("equipment")


def _latest_version(db: Session, dataset: Dataset | None) -> DatasetVersion | None:
    if not dataset:
        return None
    version = db.query(DatasetVersion).filter(DatasetVersion.id == dataset.latest_version_id).first() if dataset.latest_version_id else None
    return version or db.query(DatasetVersion).filter(DatasetVersion.dataset_id == dataset.id).order_by(DatasetVersion.version_no.desc()).first()


def _source_item(db: Session, source_id: str) -> dict[str, Any]:
    if source_id == FACTORYNET_SOURCE_ID:
        dataset = find_factorynet_dataset(db)
        manifest = dict(dataset.schema_json or {}) if dataset else {}
        version = db.query(DatasetVersion).filter(DatasetVersion.id == dataset.latest_version_id).first() if dataset and dataset.latest_version_id else None
        return {
            "id": FACTORYNET_SOURCE_ID,
            "name": FACTORYNET_DATASET_NAME,
            "kind": "temporal",
            "installed": bool(dataset and version),
            "privacy_level": dataset.privacy_level if dataset else "standard",
            "dataset_id": dataset.id if dataset else None,
            "version_id": version.id if version else None,
            "records": version.rowcount if version else None,
            "columns": manifest.get("columns", []),
            "supports": ["ordinal"],
            "time_kind": "ordinal",
            "source_url": FACTORYNET_URL,
            "license": FACTORYNET_LICENSE,
            "sha256": manifest.get("sha256", FACTORYNET_SHA256),
            "filename": manifest.get("filename", FACTORYNET_FILE),
            "manifest": manifest,
        }
    if source_id == CMAPSS_SOURCE_ID:
        sensor, equipment = _find_cmapss_parts(db)
        version = _latest_version(db, sensor)
        equipment_version = _latest_version(db, equipment)
        manifest = dict(sensor.schema_json or {}) if sensor else {}
        columns = list(manifest.get("columns") or [])
        if not columns and version and version.storage_uri:
            try:
                rows = _parse_dataset_rows(_read_source_object(version.storage_uri))
                columns = [key for key in (rows[0].keys() if rows else []) if not str(key).startswith("_")]
            except Exception:
                columns = []
        ready = bool(sensor and equipment and version and equipment_version and version.storage_uri and equipment_version.storage_uri)
        return {
            "id": CMAPSS_SOURCE_ID,
            "name": CMAPSS_DATASET_NAME,
            "kind": "temporal",
            "installed": ready,
            "privacy_level": sensor.privacy_level if sensor else "standard",
            "dataset_id": sensor.id if sensor else None,
            "related_dataset_id": equipment.id if equipment else None,
            "version_id": version.id if version else None,
            "related_version_id": equipment_version.id if equipment_version else None,
            "records": version.rowcount if version else None,
            "columns": columns,
            "supports": ["ordinal"],
            "time_kind": "ordinal",
            "time_column": "cycle",
            "entity_column": "equipment_id",
            "source_url": CMAPSS_SOURCE_URL,
            "license": "NASA C-MAPSS 数据集来源，按原始许可使用",
            "filename": "sensor_readings.csv + equipment.csv",
            "sha256": manifest.get("sha256") or version.checksum if version else None,
            "manifest": {**manifest, "source_id": CMAPSS_SOURCE_ID, "temporal_source": True, "related_dataset_id": equipment.id if equipment else None},
        }
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
            "privacy_level": dataset.privacy_level if dataset else "standard",
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
                    rows = _parse_dataset_rows(_read_source_object(version.storage_uri))
                    columns = [key for key in rows[0].keys() if not key.startswith("_")] if rows else []
                except Exception:
                    columns = []
            return {
                "id": source_id, "name": dataset.name, "kind": "existing_dataset",
                "installed": bool(version), "dataset_id": dataset.id,
                "privacy_level": dataset.privacy_level or "standard",
                "records": version.rowcount if version else None,
                "columns": columns, "supports": ["instant", "ordinal", "interval"],
                "source": "existing_dataset",
            }
    return {"id": source_id, "name": source_id, "kind": "temporal", "installed": False, "supports": []}


def parse_temporal_bytes(raw: bytes) -> list[dict[str, Any]]:
    """Read a DatasetVersion for the generic temporal entry point.

    Dataset uploads are stored as ``data.bin`` without their extension in the
    object key, so the parser identifies JSON, Excel and delimited text from
    the bytes instead of trusting the storage filename.
    """
    if raw[:4] == b"PAR1":
        try:
            import pyarrow.parquet as pq
            table = pq.read_table(io.BytesIO(raw))
            return [{**dict(row), "_source_row_index": index} for index, row in enumerate(table.to_pylist())]
        except Exception as exc:
            raise ValueError(f"unable to parse Parquet dataset: {exc}") from exc
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
    if "\n" in text and text.lstrip().splitlines() and text.lstrip().splitlines()[0].lstrip().startswith("{"):
        rows = []
        for index, line in enumerate(text.splitlines()):
            if line.strip():
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append({**value, "_source_row_index": index})
        return rows
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


_parse_dataset_rows = parse_temporal_bytes


def _existing_dataset_sources(db: Session) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for dataset in db.query(Dataset).filter(Dataset.kind.in_(["structured", "semi"]), Dataset.data_class == "temporal").order_by(Dataset.created_at.desc()).all():
        manifest = dataset.schema_json or {}
        if not manifest.get("temporal_source"):
            continue
        if dataset.name in {"sensor_readings", "equipment"} or manifest.get("cmapss_role"):
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
    """List built-in temporal packages and explicitly uploaded temporal files."""
    return [_source_item(db, FACTORYNET_SOURCE_ID), _source_item(db, CMAPSS_SOURCE_ID), *_existing_dataset_sources(db)]


@router.post("/sources/factorynet_cnc/install", status_code=201)
def install_factorynet(_admin=Depends(require_admin), db: Session = Depends(get_db)):
    try:
        return install_factorynet_dataset(db)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/sources/upload", status_code=201)
async def upload_temporal_source(file: UploadFile = File(...), db: Session = Depends(get_db)):
    filename = file.filename or "temporal-upload"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    allowed = {"csv", "tsv", "json", "jsonl", "xlsx", "xls", "parquet"}
    if ext not in allowed:
        raise HTTPException(400, f"不支持的时序文件类型: .{ext}")
    raw = await file.read()
    if len(raw) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"文件超过大小限制 {settings.max_upload_mb}MB")
    try:
        rows = parse_temporal_bytes(raw)
    except Exception as exc:
        raise HTTPException(422, f"文件解析失败: {exc}") from exc
    if not rows:
        raise HTTPException(422, "文件没有可用记录")
    import os
    import hashlib
    name = os.path.splitext(filename)[0]
    svc = DatasetService(db)
    dataset = svc.create_dataset(
        name=name, kind="structured", data_class="temporal",
        schema_json={"temporal_source": True, "source": "uploaded"},
    )
    version = svc.create_version(dataset.id, raw, rowcount=len(rows))
    dataset.latest_version_id = version.id
    digest = hashlib.sha256(raw).hexdigest()
    manifest = {"temporal_source": True, "source": "uploaded", "filename": filename, "format": ext,
                "sha256": digest, "records": len(rows), "columns": [key for key in rows[0].keys() if not str(key).startswith("_")]}
    version.checksum = digest
    dataset.schema_json = manifest
    db.commit()
    profile = TemporalDatasetProfile(dataset_id=dataset.id, dataset_version_id=version.id, status="queued")
    db.add(profile); db.commit(); db.refresh(profile)
    return {"source_id": f"dataset:{dataset.id}", "dataset_id": dataset.id, "version_id": version.id,
            "name": dataset.name, "records": len(rows), "columns": manifest["columns"], "profile_id": profile.id}


@router.post("/sources/{source_id}/analyses", status_code=202)
def create_temporal_analysis(source_id: str, body: TemporalAnalysisRequest | None = None, background: BackgroundTasks = None, db: Session = Depends(get_db)):
    dataset_id = source_id.split(":", 1)[1] if source_id.startswith("dataset:") else None
    if source_id == FACTORYNET_SOURCE_ID:
        dataset = find_factorynet_dataset(db)
    elif source_id == CMAPSS_SOURCE_ID:
        dataset, equipment = _find_cmapss_parts(db)
        if not equipment:
            raise HTTPException(409, "C-MAPSS 案例包缺少 equipment 关联表")
    else:
        dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first() if dataset_id else None
    if not dataset:
        raise HTTPException(404, "时序数据源未安装")
    version = db.query(DatasetVersion).filter(DatasetVersion.id == dataset.latest_version_id).first() if dataset.latest_version_id else None
    if not version:
        raise HTTPException(404, "时序数据源没有版本")
    privacy = (body.privacy_level if body else None) or dataset.privacy_level or "standard"
    dataset.privacy_level = privacy
    db.commit()
    existing = db.query(TemporalDatasetProfile).filter(TemporalDatasetProfile.dataset_version_id == version.id).order_by(TemporalDatasetProfile.updated_at.desc()).first()
    if existing:
        if privacy == "private":
            rows = parse_temporal_bytes(_read_source_object(version.storage_uri))
            existing.deterministic_profile = profile_rows(rows, checksum=version.checksum)
            existing.llm_suggestion = {}
            existing.llm_used = False
            existing.model_name = None
            existing.error = None
            existing.status = "completed"
            db.commit()
        elif existing.status == "queued":
            _queue_temporal_profile(existing.id, background)
        return serialize_profile(existing)
    if privacy == "private":
        rows = parse_temporal_bytes(_read_source_object(version.storage_uri))
        profile = TemporalDatasetProfile(dataset_id=dataset.id, dataset_version_id=version.id, status="completed", deterministic_profile=profile_rows(rows, checksum=version.checksum), llm_suggestion={}, llm_used=False, error=None)
        db.add(profile); db.commit(); db.refresh(profile)
        return serialize_profile(profile)
    profile = TemporalDatasetProfile(dataset_id=dataset.id, dataset_version_id=version.id, status="queued")
    db.add(profile); db.commit(); db.refresh(profile)
    _queue_temporal_profile(profile.id, background)
    return serialize_profile(profile)


@router.get("/analyses/{profile_id}")
def get_temporal_analysis(profile_id: str, db: Session = Depends(get_db)):
    profile = db.query(TemporalDatasetProfile).filter(TemporalDatasetProfile.id == profile_id).first()
    if not profile:
        raise HTTPException(404, "时序分析不存在")
    return serialize_profile(profile)


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
    if source_id not in {ICEWS_SOURCE_ID, FACTORYNET_SOURCE_ID, CMAPSS_SOURCE_ID} and not source_id.startswith("dataset:"):
        raise HTTPException(404, "temporal source not found")
    if source_id == FACTORYNET_SOURCE_ID:
        dataset = find_factorynet_dataset(db)
        if not dataset or not dataset.latest_version_id:
            raise HTTPException(status_code=409, detail={"error": "SOURCE_NOT_INSTALLED", "message": "请先安装 FactoryNet CNC 官方样例"})
        version = db.query(DatasetVersion).filter(DatasetVersion.id == dataset.latest_version_id).first()
        if not version or not version.storage_uri:
            raise HTTPException(422, "FactoryNet dataset version has no storage object")
        all_rows = parse_temporal_bytes(_read_source_object(version.storage_uri))
        selected = all_rows[offset:offset + limit]
        return {"source_id": source_id, "dataset_id": dataset.id, "offset": offset, "limit": limit,
                "rows": selected, "columns": [key for key in all_rows[0].keys() if not str(key).startswith("_")] if all_rows else [],
                "total_rows": len(all_rows), "total_source_rows": len(all_rows),
                "summary": {"rows": len(all_rows), "episodes": len({str(row.get("episode_id")) for row in all_rows}),
                            "machines": len({str(row.get("machine_type")) for row in all_rows}), "time_kind": "ordinal",
                            "time_column": "time_s", "time_from": min((row.get("time_s") for row in all_rows if row.get("time_s") is not None), default=None),
                            "time_to": max((row.get("time_s") for row in all_rows if row.get("time_s") is not None), default=None)}}
    if source_id == CMAPSS_SOURCE_ID:
        sensor, equipment = _find_cmapss_parts(db)
        version = _latest_version(db, sensor)
        equipment_version = _latest_version(db, equipment)
        if not sensor or not equipment or not version or not equipment_version or not version.storage_uri or not equipment_version.storage_uri:
            raise HTTPException(409, detail={"error": "SOURCE_NOT_READY", "message": "C-MAPSS 案例包缺少 sensor_readings/equipment 可读版本"})
        all_rows = _parse_dataset_rows(_read_source_object(version.storage_uri))
        selected = all_rows[offset:offset + limit]
        return {
            "source_id": source_id, "dataset_id": sensor.id, "related_dataset_id": equipment.id,
            "offset": offset, "limit": limit, "rows": selected,
            "columns": [key for key in all_rows[0].keys() if not str(key).startswith("_")] if all_rows else [],
            "total_rows": len(all_rows), "total_source_rows": len(all_rows),
            "summary": {"rows": len(all_rows), "equipment": len({str(row.get("equipment_id")) for row in all_rows if row.get("equipment_id") not in (None, "")}), "time_kind": "ordinal", "time_column": "cycle", "entity_column": "equipment_id", "related_table": "equipment"},
        }
    if source_id.startswith("dataset:"):
        dataset_id = source_id.split(":", 1)[1]
        dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
        version = db.query(DatasetVersion).filter(DatasetVersion.id == dataset.latest_version_id).first() if dataset and dataset.latest_version_id else None
        if version is None and dataset is not None:
            version = db.query(DatasetVersion).filter(DatasetVersion.dataset_id == dataset.id).order_by(DatasetVersion.version_no.desc()).first()
        if not dataset or not version or not version.storage_uri:
            raise HTTPException(404, "dataset source not found")
        rows = _parse_dataset_rows(_read_source_object(version.storage_uri))
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
    rows = parse_icews_tsv(_read_source_object(version.storage_uri))
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


@router.post("/sources/{source_id}/query")
def query_temporal_source(source_id: str, body: TemporalQueryBody, db: Session = Depends(get_db)):
    item = _source_item(db, source_id)
    if not item.get("installed") or not item.get("dataset_id"):
        raise HTTPException(409, "时序数据源尚未安装")
    version = db.query(DatasetVersion).filter(DatasetVersion.id == item.get("version_id")).first()
    if not version or not version.storage_uri:
        raise HTTPException(404, "时序数据源版本不存在")
    rows = parse_temporal_bytes(_read_source_object(version.storage_uri))
    from app.tasks.v2.temporal_construction import _filter_generic_rows
    selected = _filter_generic_rows(rows, body.model_dump(exclude_none=True), entity_column=body.entity_column)
    page = selected[body.offset:body.offset + body.limit]
    columns = [key for key in rows[0].keys() if not str(key).startswith("_")] if rows else []
    return {"source_id": source_id, "dataset_id": item["dataset_id"], "offset": body.offset, "limit": body.limit,
            "rows": page, "columns": columns, "total_rows": len(selected), "total_source_rows": len(rows),
            "summary": {"rows": len(selected), "source_rows": len(rows), "time_kind": item.get("time_kind") or "unknown"}}


@router.get("/catalog")
def catalog(db: Session = Depends(get_db)):
    """Compatibility alias for the temporal source catalog."""
    return [_source_item(db, FACTORYNET_SOURCE_ID), _source_item(db, CMAPSS_SOURCE_ID), *_existing_dataset_sources(db)]


@router.get("/catalog/{dataset_key}/preview")
def catalog_preview(dataset_key: str, limit: int = Query(25, ge=1, le=100)):
    if dataset_key not in {ICEWS_SOURCE_ID, FACTORYNET_SOURCE_ID, CMAPSS_SOURCE_ID} and not dataset_key.startswith("dataset:"):
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
    ontology = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    if not ontology:
        raise HTTPException(404, "Ontology not found")
    if ontology.data_class != "temporal":
        raise HTTPException(409, "只能追加到时序数据本体")
    source_id = _source_id(body)
    dataset_id = body.dataset_id
    if source_id == FACTORYNET_SOURCE_ID:
        dataset = find_factorynet_dataset(db)
        if not dataset:
            raise HTTPException(status_code=409, detail={"error": "SOURCE_NOT_INSTALLED", "message": "请先安装 FactoryNet CNC 官方样例"})
        dataset_id = dataset.id
        if body.time_kind != "ordinal":
            raise HTTPException(422, "FactoryNet CNC 使用 Ordinal 相对时间")
    elif source_id == CMAPSS_SOURCE_ID:
        sensor, equipment = _find_cmapss_parts(db)
        sensor_version = _latest_version(db, sensor)
        equipment_version = _latest_version(db, equipment)
        if not sensor or not equipment or not sensor_version or not equipment_version:
            raise HTTPException(status_code=409, detail={"error": "SOURCE_NOT_READY", "message": "请先恢复 C-MAPSS 的 sensor_readings 与 equipment 两个版本"})
        dataset_id = sensor.id
        if body.time_kind != "ordinal":
            raise HTTPException(422, "C-MAPSS cycle 使用 Ordinal 相对时间")
    elif source_id == ICEWS_SOURCE_ID:
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
    # The desktop demo must remain responsive when Redis/Celery is not
    # running.  Celery is opt-in; otherwise persist the run and use the local
    # FastAPI background worker.  This avoids a 20-second broker retry before
    # every FactoryNet click on a clean workstation.
    import os
    celery_enabled = os.getenv("CELERY_ENABLED", "").lower() in {"1", "true", "yes"}
    try:
        from app.tasks.v2.temporal_construction import run_temporal_construction_task, run_temporal_construction
        if celery_enabled and run_temporal_construction_task is not None:
            run_temporal_construction_task.delay(run.id)
        else:
            background.add_task(run_temporal_construction, run.id)
    except Exception as exc:
        background.add_task(run_temporal_construction, run.id)
        run.error = f"Celery unavailable; background fallback: {exc}"
        db.commit()
    return serialize_run(run)


@router.post("/runs", status_code=202)
def create_temporal_run_atomic(body: TemporalAtomicRunCreate, background: BackgroundTasks, db: Session = Depends(get_db), current_user: User = Depends(require_editor)):
    """Atomically create/reuse a target ontology and start a validated run."""
    profile = db.query(TemporalDatasetProfile).filter(TemporalDatasetProfile.id == body.profile_id).first()
    dataset_for_profile = db.query(Dataset).filter(Dataset.id == profile.dataset_id).first() if profile else None
    private_profile = bool(dataset_for_profile and dataset_for_profile.privacy_level == "private")
    if not profile or profile.status != "completed" or (not profile.llm_used and not private_profile):
        raise HTTPException(status_code=409, detail={"error": "PROFILE_NOT_READY", "message": "MiniMax M3 分析尚未成功，不能开始构建"})
    dataset = dataset_for_profile
    if not dataset:
        raise HTTPException(404, "分析对应的数据集不存在")
    if body.ontology_mode == "reuse":
        if not body.ontology_id:
            raise HTTPException(422, "请选择已有本体")
        ontology_id = body.ontology_id
        existing_ontology = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
        if not existing_ontology:
            raise HTTPException(404, "目标本体不存在")
        if existing_ontology.data_class != "temporal":
            raise HTTPException(409, "只能追加到时序数据本体")
    else:
        name = body.ontology_name.strip()
        if not name:
            raise HTTPException(422, "本体名称不能为空")
        duplicate = db.query(OntologyProject).filter(OntologyProject.name.ilike(name)).first()
        if duplicate:
            raise HTTPException(status_code=409, detail={"error": "DUPLICATE_NAME", "message": "本体名称已存在，请选择复用或修改名称", "existing_id": duplicate.id})
        ontology = OntologyProject(id=str(uuid.uuid4()), name=name, domain=body.ontology_domain,
                                   description=body.ontology_description or "由时序案例数据构建的工业本体",
                                   build_mode="temporal_pipeline", data_class="temporal", created_by=current_user.id)
        db.add(ontology); db.commit(); db.refresh(ontology)
        ontology_id = ontology.id
    config_body = TemporalRunCreate(**body.model_dump(exclude={"ontology_mode", "ontology_id", "ontology_name", "ontology_domain", "ontology_description"}))
    result = create_temporal_run(ontology_id, config_body, background, db)
    return {**result, "ontology_id": ontology_id, "profile_id": body.profile_id}


@router.post("/ontologies/{ontology_id}/runs", status_code=202)
def create_temporal_run_legacy(ontology_id: str, body: TemporalRunCreate, background: BackgroundTasks, db: Session = Depends(get_db)):
    return create_temporal_run(ontology_id, body, background, db)


@router.get("/runs")
def list_temporal_runs(ontology_id: str | None = None, limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    query = db.query(ConstructionRun).filter(ConstructionRun.mode == "temporal")
    if ontology_id:
        query = query.filter(ConstructionRun.ontology_id == ontology_id)
    return [serialize_run(run) for run in query.order_by(ConstructionRun.created_at.desc()).limit(limit).all()]


@router.get("/runs/{run_id}/graph")
def temporal_run_graph(
    run_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=500),
    entity_type: str | None = None,
    seq_from: int | None = None,
    seq_to: int | None = None,
    relation_state: str = Query("all", pattern="^(all|current)$"),
    db: Session = Depends(get_db),
):
    """Paginated instance graph for the investigation workbench.

    The browser can request ``全部`` by following ``next_offset`` instead of
    downloading an unbounded graph in one response.
    """
    run = db.query(ConstructionRun).filter(ConstructionRun.id == run_id).first()
    if not run:
        raise HTTPException(404, "构建任务不存在")
    if run.status != "completed":
        raise HTTPException(409, "构建任务尚未完成")
    return get_falkordb().get_graph_data(
        run.ontology_id, offset=offset, limit=limit, entity_type=entity_type,
        seq_from=seq_from, seq_to=seq_to, relation_state=relation_state,
    ) | {"run_id": run_id, "ontology_id": run.ontology_id}


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
