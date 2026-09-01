"""Evidence-first multimodal ingestion and MiniMax M3 construction runs."""
from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.deps import get_current_user, require_editor
from app.models.ontology import OntologyProject
from app.models.v2.construction import ConstructionRun
from app.models.v2.dataset import Dataset, DatasetVersion, MediaItem
from app.models.v2.multimodal import ExtractedFragment
from app.services.model_config_selector import is_vlm_config, llm_call_kwargs, select_llm_model_config
from app.services.storage_service import get_storage_service
from app.services.v2.construction_service import add_evidence, create_run, serialize_run, update_run
from app.services.v2.graph.falkordb_service import FalkorDBService

router = APIRouter(prefix="/multimodal", dependencies=[Depends(get_current_user)])
MODEL_NAME = "MiniMax-M3"


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class FragmentCreate(BaseModel):
    media_item_id: str
    dataset_version_id: str
    fragment_type: str = Field(pattern="^(text|ocr|table|metadata)$")
    content: str
    locator: dict = {}
    extractor: str = Field(pattern="^(markitdown|ocr|llm|rule|bridge_fallback)$")
    status: str = "completed"
    error: str | None = None


class MultimodalRunCreate(BaseModel):
    dataset_id: str
    ontology_id: str
    model_id: str | None = None
    sample_limit: int = Field(default=32, ge=1, le=32)
    prompt: str | None = None


@router.get("/status")
def multimodal_status(db: Session = Depends(get_db)):
    """Return safe M3 availability metadata without exposing credentials."""
    config = select_llm_model_config(db=db, purpose_tags=("VLM提取", "多模态"), allow_vlm=True)
    available = bool(config and is_vlm_config(config) and MODEL_NAME in [str(x) for x in (config.models or [])])
    return {
        "configured": bool(config and is_vlm_config(config)),
        "available": available,
        "model": MODEL_NAME,
        "model_id": config.id if available else None,
        "api_base": config.api_base if available else None,
        "message": "MiniMax M3 可用" if available else "MiniMax M3 尚未配置或当前 Key 不具备权限",
    }


@router.get("/sources")
def multimodal_sources(db: Session = Depends(get_db)):
    rows: list[dict[str, Any]] = []
    datasets = db.query(Dataset).filter(Dataset.kind == "unstructured").order_by(Dataset.created_at.desc()).all()
    for dataset in datasets:
        versions = db.query(DatasetVersion).filter(DatasetVersion.dataset_id == dataset.id).order_by(DatasetVersion.version_no.desc()).all()
        media = db.query(MediaItem).filter(MediaItem.dataset_version_id == versions[0].id).all() if versions else []
        rows.append({
            "id": dataset.id,
            "name": dataset.name,
            "kind": dataset.kind,
            "source": "uploaded" if not dataset.name.lower().startswith("mvtec") else "mvtec_ad2",
            "version_id": versions[0].id if versions else None,
            "media_count": len(media),
            "media": [{"id": x.id, "media_type": x.media_type, "storage_uri": x.storage_uri, "ocr_status": x.ocr_status} for x in media],
        })
    manifest = Path(__file__).resolve().parents[3] / "data" / "mvtec_ad2" / "manifest.json"
    if manifest.exists():
        rows.insert(0, {"id": "mvtec-ad2-builtin", "name": "MVTec AD 2 样例", "kind": "unstructured", "source": "mvtec_ad2", "media_count": 0, "media": [], "manifest": str(manifest)})
    return {"sources": rows, "count": len(rows), "note": "官方 MVTec 图片需先通过导入脚本安装；未安装时不会伪造样例"}


@router.post("/fragments")
def create_fragment(body: FragmentCreate, db: Session = Depends(get_db)):
    media = db.query(MediaItem).filter(MediaItem.id == body.media_item_id, MediaItem.dataset_version_id == body.dataset_version_id).first()
    if not media:
        raise HTTPException(404, "Media item or dataset version not found")
    fragment = ExtractedFragment(**body.model_dump())
    db.add(fragment)
    db.commit()
    db.refresh(fragment)
    return _serialize(fragment)


@router.get("/fragments/{media_item_id}")
def list_fragments(media_item_id: str, db: Session = Depends(get_db)):
    items = db.query(ExtractedFragment).filter(ExtractedFragment.media_item_id == media_item_id).order_by(ExtractedFragment.created_at.asc()).all()
    return {"media_item_id": media_item_id, "fragments": [_serialize(item) for item in items], "count": len(items)}


@router.post("/runs", status_code=202)
def create_multimodal_run(body: MultimodalRunCreate, background_tasks: BackgroundTasks, db: Session = Depends(get_db), _=Depends(require_editor)):
    if not db.query(Dataset).filter(Dataset.id == body.dataset_id).first():
        raise HTTPException(404, "Dataset not found")
    if not db.query(OntologyProject).filter(OntologyProject.id == body.ontology_id).first():
        raise HTTPException(404, "Ontology not found")
    config = select_llm_model_config(db=db, model_id=body.model_id, purpose_tags=("VLM提取", "多模态"), allow_vlm=True)
    model_name = str((config.models or [MODEL_NAME])[0]) if config else None
    run = create_run(db, ontology_id=body.ontology_id, dataset_id=body.dataset_id, mode="multimodal", model_name=model_name,
                     config={"sample_limit": body.sample_limit, "prompt": body.prompt or ""})
    background_tasks.add_task(_execute_multimodal_run, run.id, body.model_id, body.sample_limit, body.prompt)
    return serialize_run(run)


def _execute_multimodal_run(run_id: str, model_id: str | None, sample_limit: int, prompt: str | None) -> None:
    db = SessionLocal()
    try:
        run = db.query(ConstructionRun).filter(ConstructionRun.id == run_id).first()
        if not run:
            return
        config = select_llm_model_config(db=db, model_id=model_id, purpose_tags=("VLM提取", "多模态"), allow_vlm=True)
        if not config or not is_vlm_config(config) or MODEL_NAME not in [str(x) for x in (config.models or [])]:
            update_run(db, run, status="failed", error="MiniMax M3 未配置或不可用；已保留原媒体，未创建模型猜测的关系")
            return
        versions = db.query(DatasetVersion).filter(DatasetVersion.dataset_id == run.dataset_id).order_by(DatasetVersion.version_no.desc()).all()
        media = db.query(MediaItem).filter(MediaItem.dataset_version_id == versions[0].id).order_by(MediaItem.created_at.asc()).limit(sample_limit).all() if versions else []
        update_run(db, run, status="running", progress={"completed": 0, "total": len(media)})
        if not media:
            update_run(db, run, status="failed", progress={"completed": 0, "total": 0}, error="Dataset 没有可处理的媒体文件")
            return
        storage = get_storage_service()
        graph = FalkorDBService()
        nodes: list[dict] = []
        relations: list[dict] = []
        completed = 0
        failures = 0
        for item in media:
            item.ocr_status = "processing"
            db.commit()
            filename = Path(item.storage_uri.split("/")[-1]).name
            try:
                raw = storage.get_object(item.storage_uri)
                text = _call_minimax(config, raw, filename, prompt)
                if not text.strip():
                    raise RuntimeError("MiniMax M3 返回空内容")
                fragment = ExtractedFragment(media_item_id=item.id, dataset_version_id=versions[0].id, fragment_type="text",
                                             content=text, locator={"filename": filename, "storage_uri": item.storage_uri}, extractor="llm", status="completed")
                db.add(fragment)
                db.flush()
                add_evidence(db, run=run, assertion_id=f"fragment:{fragment.id}", assertion_kind="property", extractor="llm",
                             source_media_id=item.id, source_dataset_version=versions[0].id, model_name=MODEL_NAME,
                             confidence_method="model_self_report", evidence_text=text[:4000], content={"media_uri": item.storage_uri, "filename": filename})
                media_id = f"media:{item.id}"
                fragment_id = f"fragment:{fragment.id}"
                official_label = "anomaly" if "-anomaly-" in filename.lower() else ("normal" if "-normal-" in filename.lower() else "unknown")
                inspection_id = f"inspection:{item.id}"
                nodes.extend([
                    {"id": media_id, "entity_type": "MediaAsset", "properties": {"storage_uri": item.storage_uri, "media_type": item.media_type, "filename": filename}},
                    {"id": fragment_id, "entity_type": "ExtractedFragment", "properties": {"content": text[:8000], "extractor": "MiniMax-M3", "source_media_id": item.id}},
                    {"id": inspection_id, "entity_type": "InspectionEvent", "properties": {"official_label": official_label, "source_media_id": item.id}},
                ])
                relations.extend([
                    {"source": media_id, "target": fragment_id, "type": "DESCRIBES", "properties": {"extractor": "llm", "model_name": MODEL_NAME}},
                    {"source": media_id, "target": inspection_id, "type": "OBSERVED_IN", "properties": {"extractor": "rule", "label_source": "archive_path"}},
                ])
                if official_label == "anomaly":
                    anomaly_id = f"anomaly:{item.id}"
                    nodes.append({"id": anomaly_id, "entity_type": "AnomalyEvent", "properties": {"label": "anomaly", "source_media_id": item.id}})
                    relations.append({"source": inspection_id, "target": anomaly_id, "type": "HAS_ANOMALY", "properties": {"extractor": "rule"}})
                item.ocr_status = "done"
                completed += 1
            except Exception as exc:
                failures += 1
                item.ocr_status = "failed"
                db.commit()
                db.add(ExtractedFragment(media_item_id=item.id, dataset_version_id=versions[0].id, fragment_type="text", content="",
                                         locator={"filename": filename, "storage_uri": item.storage_uri}, extractor="llm", status="failed", error=str(exc)[:1000]))
                db.commit()
            update_run(db, run, progress={"completed": completed + failures, "total": len(media), "failed": failures})
        written_nodes = graph.upsert_instances(run.ontology_id, nodes) if nodes else 0
        written_edges = graph.upsert_relations(run.ontology_id, relations) if relations else 0
        update_run(db, run, status="completed" if completed else "failed", progress={"completed": len(media), "total": len(media), "failed": failures},
                   metrics={"media_processed": len(media), "successful": completed, "failed": failures, "nodes_written": written_nodes, "edges_written": written_edges,
                            "extractor": "llm", "model": MODEL_NAME, "confidence_method": "model_self_report"},
                   error=("部分媒体处理失败" if failures and completed else ("全部媒体处理失败" if failures else None)))
    except Exception as exc:
        db.rollback()
        run = db.query(ConstructionRun).filter(ConstructionRun.id == run_id).first()
        if run:
            update_run(db, run, status="failed", error=str(exc)[:2000])
    finally:
        db.close()


def _call_minimax(config, raw: bytes, filename: str, prompt: str | None) -> str:
    import base64
    from app.services.llm_service import _call_llm
    call_kwargs = llm_call_kwargs(config)
    if not call_kwargs:
        raise RuntimeError("MiniMax M3 凭据无法解密")
    mime = mimetypes.guess_type(filename)[0] or "image/png"
    data_url = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
    messages = [
        {"role": "system", "content": "You are an industrial inspection evidence extractor. Return concise Markdown; never invent equipment IDs or relationships."},
        {"role": "user", "content": [{"type": "text", "text": prompt or f"提取 {filename} 中可见文本、缺陷标签、设备编号和定位信息；没有证据的字段写 unknown。"}, {"type": "image_url", "image_url": {"url": data_url}}]},
    ]
    return _call_llm(call_kwargs["provider"], call_kwargs["api_key"], call_kwargs["api_base"], call_kwargs["model"], messages, json_mode=False)


def _serialize(item: ExtractedFragment) -> dict:
    return {"id": item.id, "media_item_id": item.media_item_id, "dataset_version_id": item.dataset_version_id,
            "fragment_type": item.fragment_type, "content": item.content, "locator": item.locator or {},
            "extractor": item.extractor, "status": item.status, "error": item.error,
            "created_at": item.created_at.isoformat() if item.created_at else None}
