"""v2 Dataset API"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.deps import get_current_user, require_admin
from app.services.v2.dataset_service import DatasetService, DatasetStorageError
from app.models.v2.dataset import Dataset, DatasetVersion, MediaItem
from app.models.v2.connection import Connection
from app.models.v2.curated import CuratedDataset, CuratedReview, CuratedRowEdit
from app.models.v2.pipeline import Pipeline, PipelineRun
from app.models.v2.construction import ConstructionRun
from app.models.v2.mapping import OntologyMapping, OntologyLinkMapping
from app.services.storage_service import get_storage_service

router = APIRouter(dependencies=[Depends(get_current_user)])
data_sources_router = APIRouter(dependencies=[Depends(get_current_user)])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@data_sources_router.get("/data-sources")
def unified_data_sources(include_history: bool = Query(False), db: Session = Depends(get_db)):
    """One source directory for the three construction entry points."""
    cases = [
        {"id": "cmapss_fd001_regular_subset", "name": "NASA C-MAPSS FD001", "data_class": "regular", "input": ["equipment", "sensor_readings", "100 条下采样读数"], "installed": bool(db.query(Dataset.id).filter(Dataset.name.ilike("%C-MAPSS FD001%"), Dataset.data_class == "regular").first())},
        {"id": "factorynet_cnc", "name": "FactoryNet CNC", "data_class": "temporal", "input": ["Parquet"], "installed": bool(db.query(Dataset.id).filter(Dataset.name.ilike("%FactoryNet%"), Dataset.data_class == "temporal").first())},
        {"id": "ibadas_12_demo", "name": "I-BADAS RGB-D 12 组", "data_class": "multimodal", "input": ["ZIP + manifest", "RGB", "depth", "mask", "point cloud"], "installed": bool(db.query(Dataset.id).filter(Dataset.name.ilike("%I-BADAS%"), Dataset.data_class == "multimodal", Dataset.readiness == "ready").first())},
    ]
    query = db.query(Dataset)
    if not include_history:
        query = query.filter(Dataset.readiness.in_([None, "ready"]))
        datasets = [item for item in query.order_by(Dataset.created_at.desc()).all() if not bool((item.schema_json or {}).get("technical")) and not any(token in item.name.lower() for token in ("_pipeline", " curated", "test"))]
    else:
        datasets = query.order_by(Dataset.created_at.desc()).all()
    return {"cases": cases, "datasets": [{"id": item.id, "name": item.name, "data_class": item.data_class, "privacy_level": item.privacy_level, "readiness": item.readiness, "latest_version_id": item.latest_version_id} for item in datasets], "connections": [{"id": item.id, "name": item.name, "kind": item.kind, "status": item.status} for item in db.query(Connection).order_by(Connection.created_at.desc()).all()]}


@data_sources_router.get("/storage/integrity")
def storage_integrity(include_history: bool = Query(True), db: Session = Depends(get_db)):
    """Return a read-only object-storage integrity report for dataset assets.

    The report deliberately checks metadata and existence only; it never
    downloads or rewrites user data.  Missing objects are surfaced with the
    exact URI so the UI can guide the operator to a storage repair.
    """
    query = db.query(Dataset)
    if not include_history:
        query = query.filter(Dataset.readiness.in_([None, "ready"]))
    storage = get_storage_service()
    checked = 0
    present = 0
    missing: list[dict[str, str]] = []
    for dataset in query.order_by(Dataset.created_at.asc()).all():
        versions = db.query(DatasetVersion).filter(DatasetVersion.dataset_id == dataset.id).all()
        for version in versions:
            if version.storage_uri:
                checked += 1
                if storage.object_exists(version.storage_uri):
                    present += 1
                else:
                    missing.append({"kind": "dataset_version", "dataset_id": dataset.id, "version_id": version.id, "storage_uri": version.storage_uri})
            for media in db.query(MediaItem).filter(MediaItem.dataset_version_id == version.id).all():
                checked += 1
                if storage.object_exists(media.storage_uri):
                    present += 1
                else:
                    missing.append({"kind": "media_item", "dataset_id": dataset.id, "version_id": version.id, "media_id": media.id, "storage_uri": media.storage_uri})
    return {
        "status": "ok" if not missing else "repair_needed",
        "checked": checked,
        "present": present,
        "missing_count": len(missing),
        "missing": missing[:200],
        "truncated": len(missing) > 200,
        "next": "运行存储修复或重新导入缺失来源" if missing else "无需修复",
    }

class DatasetResponse(BaseModel):
    id: str
    name: str
    kind: str
    data_class: str = "regular"
    privacy_level: str = "standard"
    source_connection_id: str | None = None
    latest_version_id: str | None = None
    version_count: int = 0
    rowcount: int | None = None
    readiness: str | None = None
    used_by_pipeline: bool = False
    used_by_mapping: bool = False
    class Config:
        from_attributes = True

@router.post("/upload", status_code=201)
async def upload_dataset(
    file: UploadFile = File(...),
    data_class: str = Query("regular", pattern="^(regular|temporal|multimodal)$"),
    privacy_level: str = Query("standard", pattern="^(standard|private)$"),
    connection_id: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """上传 CSV/Excel 文件，自动创建 raw Dataset + DatasetVersion"""
    import os
    from app.config import settings

    name = os.path.splitext(file.filename or "upload")[0]
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    allowed = {e.strip() for e in settings.allowed_upload_extensions.split(",") if e.strip()}
    if ext not in allowed:
        raise HTTPException(400, f"不支持的文件类型: .{ext} (允许: {settings.allowed_upload_extensions})")

    content = await file.read()
    if len(content) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"文件超过大小限制 {settings.max_upload_mb}MB")
    # 推断 kind
    if ext in ("csv", "xlsx", "xls"):
        kind = "structured"
    elif ext in ("json", "xml"):
        kind = "semi"
    else:
        kind = "unstructured"

    if data_class == "multimodal":
        raise HTTPException(422, "多模态数据请使用 ZIP + manifest 导入，保证样本和资产关联完整")
    svc = DatasetService(db)
    ds = svc.create_dataset(name=name, kind=kind, connection_id=connection_id, data_class=data_class, privacy_level=privacy_level)
    # Regular uploads stay in the default standard lane.  Multimodal ZIP
    # ingestion uses the dedicated manifest endpoint so every sample/asset
    # relationship is validated before it is committed.
    # 估算行数
    rowcount = None
    if ext == "csv":
        try:
            rowcount = content.count(b"\n")
        except Exception:
            pass
    version = svc.create_version(ds.id, content, rowcount=rowcount)
    media_items = []
    media_exts = {"png": "image", "jpg": "image", "jpeg": "image", "webp": "image",
                  "pdf": "pdf", "docx": "docx", "doc": "docx", "mp4": "video", "mov": "video"}
    if ext in media_exts:
        # Keep the original filename in the media bucket while the Dataset
        # version remains the immutable raw upload.  This makes provenance
        # and browser previews independent from the tabular path.
        media_key = f"datasets/{ds.id}/v{version.version_no}/{file.filename or name}"
        media_uri = get_storage_service().put_bytes("media", media_key, content, content_type=file.content_type or "application/octet-stream")
        item = MediaItem(dataset_version_id=version.id, media_type=media_exts[ext], storage_uri=media_uri)
        db.add(item)
        db.commit()
        db.refresh(item)
        media_items.append({"id": item.id, "media_type": item.media_type, "storage_uri": item.storage_uri, "ocr_status": item.ocr_status})
    return {"data": {"id": ds.id, "name": ds.name, "kind": ds.kind, "data_class": ds.data_class, "privacy_level": ds.privacy_level, "dataset_type": "raw_dataset", "schema_type": "tabular", "version_id": version.id, "media_items": media_items}}

@router.get("", response_model=list[DatasetResponse])
def list_datasets(kind: str | None = None, include_history: bool = Query(False), db: Session = Depends(get_db)):
    svc = DatasetService(db)
    items = svc.list_datasets(kind=kind)
    if not include_history:
        items = [item for item in items if (getattr(item, "readiness", None) in {None, "ready"}) and not bool((item.schema_json or {}).get("technical")) and not any(token in item.name.lower() for token in ("_pipeline", " curated", "test"))]
    result = []
    for dataset in items:
        versions = svc.list_versions(dataset.id)
        result.append({
            "id": dataset.id,
            "name": dataset.name,
            "kind": dataset.kind,
            "data_class": dataset.data_class,
            "privacy_level": dataset.privacy_level,
            "source_connection_id": dataset.source_connection_id,
            "latest_version_id": dataset.latest_version_id,
            "version_count": len(versions),
            "rowcount": versions[-1].rowcount if versions else None,
            "readiness": getattr(dataset, "readiness", None),
            "used_by_pipeline": db.query(Pipeline.id).filter(Pipeline.source_dataset_id == dataset.id).first() is not None,
            "used_by_mapping": db.query(OntologyMapping.id).filter(OntologyMapping.curated_dataset_id == dataset.id).first() is not None,
        })
    return result

@router.get("/{dataset_id}", response_model=DatasetResponse)
def get_dataset(dataset_id: str, db: Session = Depends(get_db)):
    svc = DatasetService(db)
    ds = svc.get_dataset(dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    return ds

@router.get("/{dataset_id}/versions")
def list_versions(dataset_id: str, db: Session = Depends(get_db)):
    svc = DatasetService(db)
    versions = svc.list_versions(dataset_id)
    return [{"id": v.id, "version_no": v.version_no, "rowcount": v.rowcount, "storage_uri": v.storage_uri} for v in versions]

@router.get("/{dataset_id}/versions/{version_no}/preview")
def preview_data(dataset_id: str, version_no: int, limit: int = 100, db: Session = Depends(get_db)):
    svc = DatasetService(db)
    try:
        return svc.preview(dataset_id, version_no, limit)
    except DatasetStorageError as exc:
        raise HTTPException(424, detail={"error": "STORAGE_OBJECT_MISSING", "message": str(exc), "dataset_id": exc.dataset_id, "storage_uri": exc.storage_uri}) from exc


@router.get("/{dataset_id}/schema")
def get_schema(dataset_id: str, db: Session = Depends(get_db)):
    """返回数据集的 schema（列名、类型、样本值）"""
    svc = DatasetService(db)
    ds = svc.get_dataset(dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")

    # Use latest version for schema inference
    versions = svc.list_versions(dataset_id)
    if not versions:
        return {"dataset_id": dataset_id, "columns": []}

    latest_version_no = versions[-1].version_no
    try:
        rows = svc.preview(dataset_id, latest_version_no, limit=10)
    except DatasetStorageError as exc:
        raise HTTPException(424, detail={"error": "STORAGE_OBJECT_MISSING", "message": str(exc), "dataset_id": exc.dataset_id, "storage_uri": exc.storage_uri}) from exc
    if not rows:
        return {"dataset_id": dataset_id, "columns": []}

    columns = []
    all_keys = list(rows[0].keys()) if rows else []
    for key in all_keys:
        sample_values = [row.get(key) for row in rows if row.get(key) is not None][:5]
        # Infer type from sample values
        col_type = "string"
        for val in sample_values:
            if isinstance(val, bool):
                col_type = "boolean"
                break
            elif isinstance(val, int):
                col_type = "integer"
                break
            elif isinstance(val, float):
                col_type = "float"
                break
            elif isinstance(val, str):
                try:
                    int(val)
                    col_type = "integer"
                except ValueError:
                    try:
                        float(val)
                        col_type = "float"
                    except ValueError:
                        col_type = "string"
                break
        columns.append({"name": key, "type": col_type, "sample_values": sample_values})

    return {"dataset_id": dataset_id, "columns": columns}


@router.get("/{dataset_id}/stats")
def get_stats(dataset_id: str, db: Session = Depends(get_db)):
    """返回数据集统计信息"""
    svc = DatasetService(db)
    ds = svc.get_dataset(dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")

    versions = svc.list_versions(dataset_id)
    version_count = len(versions)

    # Use latest version for row/column counts and null rates
    row_count = 0
    column_count = 0
    null_rates: dict = {}

    if versions:
        latest = versions[-1]
        row_count = latest.rowcount or 0
        rows = svc.preview(dataset_id, latest.version_no, limit=100)
        if rows:
            column_count = len(rows[0].keys())
            # Compute null rates per column
            for key in rows[0].keys():
                null_count = sum(1 for row in rows if row.get(key) is None or row.get(key) == "")
                null_rates[key] = round(null_count / len(rows), 4)

    return {
        "dataset_id": dataset_id,
        "row_count": row_count,
        "column_count": column_count,
        "null_rates": null_rates,
        "version_count": version_count,
    }


@router.delete("/{dataset_id}", status_code=204)
def delete_dataset(
    dataset_id: str,
    cascade: bool = Query(False, description="仅在确认删除关联 Curated 记录时使用"),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
):
    """删除 Dataset 及其对象；默认拒绝删除有 Pipeline/Mapping 引用的数据。"""
    ds = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    curated = db.query(CuratedDataset).filter(CuratedDataset.id == dataset_id).first()
    if not ds and not curated:
        raise HTTPException(404, "Dataset not found")
    pipeline_refs = db.query(Pipeline.id).filter(Pipeline.source_dataset_id == dataset_id).all() if ds else []
    pipeline_run_refs = db.query(PipelineRun.id).join(DatasetVersion, PipelineRun.dataset_version_id == DatasetVersion.id).filter(DatasetVersion.dataset_id == dataset_id).all() if ds else []
    construction_refs = db.query(ConstructionRun.id).filter(ConstructionRun.dataset_id == dataset_id).all()
    mapping_refs = []
    if curated:
        mapping_refs = db.query(OntologyMapping.id).filter(OntologyMapping.curated_dataset_id == dataset_id).all()
        mapping_refs += db.query(OntologyLinkMapping.id).filter((OntologyLinkMapping.src_dataset_id == dataset_id) | (OntologyLinkMapping.tgt_dataset_id == dataset_id)).all()
    references = {"pipelines": [x[0] for x in pipeline_refs], "pipeline_runs": [x[0] for x in pipeline_run_refs], "construction_runs": [x[0] for x in construction_refs], "mappings": [x[0] for x in mapping_refs]}
    if any(references.values()) and not cascade:
        raise HTTPException(status_code=409, detail={"error": "DATASET_REFERENCED", "references": references, "message": "数据集仍被 Pipeline/构建任务/映射使用，请先解除引用或显式 cascade=true"})
    if pipeline_refs or pipeline_run_refs:
        raise HTTPException(status_code=409, detail={"error": "PIPELINE_RUN_REFERENCED", "references": references, "message": "Pipeline 运行仍引用版本，不能级联删除；请先删除 Pipeline"})

    storage = get_storage_service()
    if ds:
        versions = db.query(DatasetVersion).filter(DatasetVersion.dataset_id == dataset_id).all()
        for version in versions:
            uris = [version.storage_uri] if version.storage_uri else []
            uris += [item.storage_uri for item in db.query(MediaItem).filter(MediaItem.dataset_version_id == version.id).all()]
            for uri in uris:
                try:
                    storage.delete_object(uri)
                except Exception:
                    pass
    if mapping_refs and cascade:
        db.query(OntologyMapping).filter(OntologyMapping.curated_dataset_id == dataset_id).delete(synchronize_session=False)
        db.query(OntologyLinkMapping).filter((OntologyLinkMapping.src_dataset_id == dataset_id) | (OntologyLinkMapping.tgt_dataset_id == dataset_id)).delete(synchronize_session=False)
    if curated and cascade:
        review_ids = [x[0] for x in db.query(CuratedReview.id).filter(CuratedReview.curated_dataset_id == dataset_id).all()]
        if review_ids:
            db.query(CuratedRowEdit).filter(CuratedRowEdit.review_id.in_(review_ids)).delete(synchronize_session=False)
            db.query(CuratedReview).filter(CuratedReview.id.in_(review_ids)).delete(synchronize_session=False)
        db.query(CuratedDataset).filter(CuratedDataset.id == dataset_id).delete(synchronize_session=False)
    if ds:
        # Bulk deletes avoid loading unrelated legacy FK mappers (some old
        # development databases do not have every v2 table imported in the
        # request process).
        db.query(Dataset).filter(Dataset.id == dataset_id).delete(synchronize_session=False)
    db.commit()
