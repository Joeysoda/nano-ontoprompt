"""Five-step draft/preflight/mapping API shared by regular and multimodal flows."""
from __future__ import annotations

import json
import uuid
import hashlib
import base64
import io
import os
import time
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.deps import get_current_user, require_editor
from app.models.user import User
from app.models.model_config import ModelConfig
from app.models.ontology import OntologyProject
from app.models.v2.construction import ConstructionRun
from app.models.v2.construction_draft import ConstructionDraft
from app.models.v2.dataset import Dataset, DatasetVersion
from app.models.v2.workbench_task import MappingTask, ModelInvocation
from app.services.model_config_selector import llm_call_kwargs
from app.services import encryption_service
from app.services.v2.construction_service import create_run, serialize_run, update_run, add_evidence
from app.services.v2.ontology_materializer import (
    attach_revision_to_materialized_rows,
    mapping_suggestions,
    materialize_ontology,
    normalise_mapping,
)

router = APIRouter(prefix="/construction/drafts", dependencies=[Depends(get_current_user)])
mapping_tasks_router = APIRouter(dependencies=[Depends(get_current_user)])
MODEL_NAME = "MiniMax-M3"


_SECRET_SOURCE_KEYS = {"password", "passwd", "pwd", "token", "api_key", "apikey", "access_token", "signature", "sig", "secret"}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _safe_source_locator(value: str | None) -> str | None:
    """Keep a meaningful source address while never serialising credentials."""
    if not value:
        return value
    text = str(value)
    try:
        parsed = urlsplit(text)
        if parsed.scheme:
            username = parsed.username
            host = parsed.hostname or ""
            netloc = host
            if parsed.port:
                netloc = f"{netloc}:{parsed.port}"
            if username:
                netloc = f"{username}@{netloc}"
            query = urlencode([(key, val) for key, val in parse_qsl(parsed.query, keep_blank_values=True) if key.casefold() not in _SECRET_SOURCE_KEYS])
            return urlunsplit((parsed.scheme, netloc, parsed.path, query, ""))
    except Exception:
        pass
    # DSN-ish strings and local paths: retain the locator but mask common
    # password segments before the planning request/log is persisted.
    return re.sub(r"(?i)(password|passwd|pwd|token|api[_-]?key|secret)\s*=\s*[^\s;,&]+", r"\1=***", text)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class DraftCreate(BaseModel):
    dataset_id: str
    ontology_id: str | None = None
    data_class: str = Field(default="regular", pattern="^(regular|temporal|multimodal)$")
    target_mode: str | None = Field(default=None, pattern="^(create|append)$")
    new_ontology_name: str | None = Field(default=None, max_length=200)
    new_ontology_domain: str | None = Field(default=None, max_length=100)
    privacy_level: str = Field(default="standard", pattern="^(standard|private)$")
    selection: dict[str, Any] = Field(default_factory=dict)
    processing_config: dict[str, Any] = Field(default_factory=dict)


class DraftPatch(BaseModel):
    data_class: str | None = Field(default=None, pattern="^(regular|temporal|multimodal)$")
    privacy_level: str | None = Field(default=None, pattern="^(standard|private)$")
    selection: dict[str, Any] | None = None
    processing_config: dict[str, Any] | None = None
    mapping: dict[str, Any] | None = None
    ontology_id: str | None = None
    target_mode: str | None = Field(default=None, pattern="^(create|append)$")
    new_ontology_name: str | None = Field(default=None, max_length=200)
    new_ontology_domain: str | None = Field(default=None, max_length=100)
    status: str | None = Field(default=None, pattern="^(draft|preflight_ready|mapping_ready|building|built|waiting_for_model|failed)$")


class BuildRequest(BaseModel):
    mapping_confirmed: bool = False
    mode: str = Field(default="create", pattern="^(create|append)$")
    model_id: str | None = None


def serialize_draft(draft: ConstructionDraft) -> dict[str, Any]:
    return {
        "id": draft.id, "dataset_id": draft.dataset_id, "ontology_id": draft.ontology_id,
        "data_class": draft.data_class, "privacy_level": draft.privacy_level, "status": draft.status,
        "target_mode": draft.target_mode, "new_ontology_name": draft.new_ontology_name,
        "new_ontology_domain": draft.new_ontology_domain, "mapping_task_id": draft.mapping_task_id,
        "selection": draft.selection_json or {}, "processing_config": draft.processing_json or {},
        "preflight": draft.preflight_json or {}, "mapping": draft.mapping_json or {},
        "build_run_id": draft.build_run_id, "error": draft.error,
        "created_at": draft.created_at.isoformat() if draft.created_at else None,
        "updated_at": draft.updated_at.isoformat() if draft.updated_at else None,
    }


def _deterministic_mapping(draft: ConstructionDraft, *, descriptor: dict[str, Any] | None = None) -> dict[str, Any]:
    dataset_name = str(((descriptor or {}).get("dataset") or {}).get("name") or "")
    if draft.data_class == "regular" and "c-mapss" in dataset_name.casefold():
        selection = draft.selection_json or {}
        fields = [str(field) for field in (selection.get("fields") or selection.get("columns") or (descriptor or {}).get("headers") or [])]
        equipment_fields = {"equipment_id", "equipment_type", "source_unit", "description"}

        def field_type(field: str) -> str:
            lower = field.casefold()
            if lower in {"cycle", "source_unit", "sensor_17", "sensor_18"}:
                return "integer"
            if lower.startswith("sensor_") or lower.startswith("op_setting_"):
                return "decimal"
            return "string"

        properties = []
        for field in fields:
            entity_type = "equipment" if field in equipment_fields else "sensor_reading"
            properties.append({
                "id": f"{entity_type}-{field}",
                "entity_type": entity_type,
                "name": field,
                "label": field,
                "type": field_type(field),
                "is_identifier": field in {"equipment_id", "reading_id"},
                "source_field": field,
                "confidence": 1.0,
            })
        return {
            "schema_version": "ontology-mapping-v2",
            "entity_types": [
                {
                    "id": "equipment",
                    "name": "Equipment",
                    "name_cn": "设备",
                    "name_en": "Equipment",
                    "description": "NASA C-MAPSS FD001 中由 equipment_id 标识的涡扇发动机设备。",
                    "source_fields": [field for field in fields if field in equipment_fields],
                    "identifier_property": "equipment_id",
                    "confidence": 1.0,
                },
                {
                    "id": "sensor_reading",
                    "name": "SensorReading",
                    "name_cn": "传感器读数",
                    "name_en": "SensorReading",
                    "description": "NASA C-MAPSS FD001 中每台设备每个 cycle 的真实运行和传感器读数。",
                    "source_fields": [field for field in fields if field not in equipment_fields],
                    "identifier_property": "reading_id",
                    "confidence": 1.0,
                },
            ],
            "properties": properties,
            "relationships": [{
                "id": "equipment-has-sensor-reading",
                "name": "HAS_SENSOR_READING",
                "from": "equipment",
                "to": "sensor_reading",
                "cardinality": "one-to-many",
                "description": "设备具有按 cycle 记录的传感器读数。",
                "source_fields": ["equipment_id"],
                "confidence": 1.0,
            }],
            "logic_rules": [{
                "id": "cmapss-cycle-ordinal",
                "name": "C-MAPSS 读数顺序",
                "name_cn": "C-MAPSS 读数顺序",
                "description": "cycle 仅表示同一设备内的来源顺序，不转换为日期。",
                "if": {"all": [{"entity": "sensor_reading", "property": "cycle", "operator": ">=", "value": 1}], "time_kind": "Ordinal"},
                "then": {"relation": "HAS_SENSOR_READING", "order_by": "cycle", "meaning": "同一设备内的相对顺序"},
                "linked_entities": ["equipment", "sensor_reading"],
                "evidence": {"source_fields": ["equipment_id", "reading_id", "cycle"]},
                "confidence": 1.0,
            }],
            "source": {"engine": "rules", "case": "cmapss_fd001"},
        }
    if draft.data_class == "temporal":
        suggestions = [
            {"kind": "class", "source": "episode_id", "target": "Episode", "confidence": 1.0, "extractor": "rule"},
            {"kind": "property", "source": "time_s / event_seq", "target": "Instant / Ordinal", "confidence": 1.0, "extractor": "rule"},
            {"kind": "relation", "source": "observation", "target": "episode", "target_relation": "HAS_OBSERVATION", "confidence": 1.0, "extractor": "rule"},
        ]
    elif draft.data_class == "multimodal":
        selected_assets = [str(value) for value in ((draft.selection_json or {}).get("selected_assets") or (draft.selection_json or {}).get("modalities") or [])]
        if not selected_assets:
            selected_assets = sorted({
                str(role)
                for sample in ((descriptor or {}).get("multimodal_catalog") or [])
                for role in (sample.get("modalities") or [])
                if role
            })
        return {
            "schema_version": "ontology-mapping-v2",
            "entity_types": [
                {"id": "scene", "name": "Scene", "name_cn": "场景", "name_en": "Scene", "description": "I-BADAS 采集场景。", "source_fields": ["scene_id"], "identifier_property": "scene_id", "confidence": 1.0},
                {"id": "multimodalsample", "name": "MultimodalSample", "name_cn": "多模态样例", "name_en": "MultimodalSample", "description": "由官方 sample_key 标识的 I-BADAS 多模态样例。", "source_fields": ["sample_key", "scene_id", "label"], "identifier_property": "sample_key", "confidence": 1.0},
                {"id": "mediaasset", "name": "MediaAsset", "name_cn": "媒体资产", "name_en": "MediaAsset", "description": "与样例绑定的 RGB、深度、掩码、点云或元数据资产。", "source_fields": ["asset_role", "mime_type", "checksum", "source_path"], "identifier_property": "id", "confidence": 1.0},
                {"id": "anomalyevent", "name": "AnomalyEvent", "name_cn": "异常标注", "name_en": "AnomalyEvent", "description": "I-BADAS 官方提供的正常或异常标签。", "source_fields": ["label", "labels"], "identifier_property": "label", "confidence": 1.0},
            ],
            "properties": [
                {"id": "scene-id", "entity_type": "scene", "name": "scene_id", "type": "string", "is_identifier": True, "source_field": "scene_id", "confidence": 1.0},
                {"id": "sample-key", "entity_type": "multimodalsample", "name": "sample_key", "type": "string", "is_identifier": True, "source_field": "sample_key", "confidence": 1.0},
                {"id": "sample-label", "entity_type": "multimodalsample", "name": "label", "type": "string", "source_field": "label", "confidence": 1.0},
                {"id": "asset-id", "entity_type": "mediaasset", "name": "id", "type": "string", "is_identifier": True, "source_field": "id", "confidence": 1.0},
                {"id": "asset-role", "entity_type": "mediaasset", "name": "asset_role", "type": "string", "source_field": "asset_role", "values": selected_assets, "confidence": 1.0},
                {"id": "asset-checksum", "entity_type": "mediaasset", "name": "checksum", "type": "string", "source_field": "checksum", "confidence": 1.0},
                {"id": "anomaly-label", "entity_type": "anomalyevent", "name": "label", "type": "string", "is_identifier": True, "source_field": "label", "confidence": 1.0},
            ],
            "relationships": [
                {"id": "scene-has-sample", "name": "HAS_SAMPLE", "from": "scene", "to": "multimodalsample", "cardinality": "one-to-many", "description": "场景包含多模态样例。", "source_fields": ["scene_id"], "confidence": 1.0},
                {"id": "sample-has-asset", "name": "HAS_ASSET", "from": "multimodalsample", "to": "mediaasset", "cardinality": "one-to-many", "description": "样例包含关联媒体资产。", "source_fields": ["sample_id", "asset_role"], "confidence": 1.0},
                {"id": "sample-has-label", "name": "HAS_LABEL", "from": "multimodalsample", "to": "anomalyevent", "cardinality": "many-to-one", "description": "样例保留官方正常或异常标注。", "source_fields": ["label"], "confidence": 1.0},
            ],
            "logic_rules": [{
                "id": "ibadas-official-anomaly-label",
                "name": "I-BADAS 官方异常标注",
                "name_cn": "I-BADAS 官方异常标注",
                "description": "当来源标签为 anomaly 时，样例关联到对应的官方异常标注；不由模型生成标签。",
                "if": {"all": [{"entity": "multimodalsample", "property": "label", "operator": "=", "value": "anomaly"}]},
                "then": {"relation": "HAS_LABEL", "target_entity": "anomalyevent", "source": "official_label"},
                "linked_entities": ["multimodalsample", "anomalyevent"],
                "evidence": {"source_fields": ["label", "labels"], "authority": "I-BADAS official annotation"},
                "confidence": 1.0,
            }],
            "source": {"engine": "rules", "case": "multimodal_manifest"},
        }
    else:
        suggestions = [
            {"kind": "class", "source": "selected columns", "target": "Domain entity", "confidence": 1.0, "extractor": "deterministic_profile"},
            {"kind": "property", "source": "typed fields", "target": "属性 / datatype", "confidence": 1.0, "extractor": "deterministic_profile"},
        ]
    return {"status": "ready", "engine": "rules", "suggestions": suggestions, "confirmed": False}


def _select_m3_config(db: Session, *, model_id: str | None = None, multimodal: bool = False) -> ModelConfig | None:
    """Return only an actual MiniMax-M3 slot.

    ``select_llm_model_config`` intentionally has a broad compatibility
    fallback for legacy text extraction.  A construction draft cannot use
    that fallback: standard data must either call the configured M3 model or
    remain in ``waiting_for_model``.  In particular, the local qwen slot is
    reserved for audit/private work and must never silently become the M3
    builder.
    """
    # The generic selector intentionally filters VLM/local slots for legacy
    # callers.  Construction is stricter: scan the registry for the exact
    # MiniMax-M3 model so a correctly configured M3 slot is still selected
    # when it has not been given a usage tag yet.
    configs: list[ModelConfig] = []
    if model_id:
        selected = db.query(ModelConfig).filter(ModelConfig.id == model_id).first()
        if selected:
            configs.append(selected)
    if not configs:
        configs = db.query(ModelConfig).filter(ModelConfig.config_type == "llm").order_by(ModelConfig.updated_at.desc()).all()
    return next(
        (
            config for config in configs
            if MODEL_NAME in [str(item) for item in (config.models or [])]
            and str(config.provider or "").lower() not in {"ollama", "local"}
        ),
        None,
    )


def _default_domain(data_class: str) -> str:
    return "供应链" if data_class == "regular" else "制造"


def _build_mode(data_class: str) -> str:
    return {"regular": "regular_workbench", "temporal": "temporal_pipeline", "multimodal": "multimodal_workbench"}[data_class]


def _validate_target(db: Session, *, dataset: Dataset, data_class: str, target_mode: str, ontology_id: str | None, new_ontology_name: str | None) -> None:
    if dataset.data_class != data_class:
        raise HTTPException(409, f"数据集分类为 {dataset.data_class}，不能按 {data_class} 构筑")
    if target_mode == "create":
        if not (new_ontology_name or "").strip():
            raise HTTPException(422, "请输入新本体名称")
        return
    if not ontology_id:
        raise HTTPException(422, "请选择要追加的目标本体")
    ontology = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    if not ontology:
        raise HTTPException(404, "目标本体不存在")
    if ontology.data_class != data_class:
        raise HTTPException(409, f"只能追加到同类本体；目标本体为 {ontology.data_class}")


def _dataset_version(db: Session, dataset: Dataset) -> DatasetVersion | None:
    if dataset.latest_version_id:
        version = db.query(DatasetVersion).filter(DatasetVersion.id == dataset.latest_version_id).first()
        if version:
            return version
    return db.query(DatasetVersion).filter(DatasetVersion.dataset_id == dataset.id).order_by(DatasetVersion.version_no.desc()).first()


def _source_descriptor(db: Session, draft: ConstructionDraft) -> dict[str, Any]:
    """Create the first, deliberately bounded M3 planning request."""
    dataset = db.query(Dataset).filter(Dataset.id == draft.dataset_id).first()
    if not dataset:
        raise ValueError("数据集不存在")
    version = _dataset_version(db, dataset)
    selection = dict(draft.selection_json or {})
    descriptor: dict[str, Any] = {
        "dataset": {
            "id": dataset.id,
            "name": dataset.name,
            "data_class": dataset.data_class,
            "privacy_level": dataset.privacy_level,
            "schema": dataset.schema_json or {},
            "source_locator": _safe_source_locator((version.source_path if version else None) or (version.storage_uri if version else None)),
            "storage_uri": _safe_source_locator(version.storage_uri if version else None),
            "checksum": version.checksum if version else None,
            "record_count": version.rowcount if version else None,
            "version": version.version_no if version else None,
        },
        "selection": selection,
        "data_class": draft.data_class,
    }
    if draft.data_class == "multimodal":
        from app.models.v2.dataset import MediaItem, MultimodalSample
        if not version:
            raise ValueError("多模态数据集没有可读取版本")
        samples = db.query(MultimodalSample).filter(MultimodalSample.dataset_version_id == version.id).order_by(MultimodalSample.sample_key.asc()).all()
        selected_sample_ids = {str(value) for value in selection.get("sample_ids", [])}
        if selected_sample_ids:
            samples = [item for item in samples if item.id in selected_sample_ids]
        media = db.query(MediaItem).filter(MediaItem.dataset_version_id == version.id).all()
        roles_by_sample: dict[str, list[str]] = {}
        for item in media:
            if item.sample_id:
                roles_by_sample.setdefault(item.sample_id, []).append(item.asset_role)
        descriptor["multimodal_catalog"] = [
            {"sample_id": item.id, "sample_key": item.sample_key, "scene_id": item.scene_id, "label": item.label, "labels": item.labels or [], "modalities": sorted(set(roles_by_sample.get(item.id, []))), "metadata": item.metadata_json or {}}
            for item in samples[:100]
        ]
        descriptor["allowed_asset_roles"] = list(selection.get("selected_assets") or selection.get("modalities") or ["rgb", "depth", "mask", "point_cloud", "metadata"])
    else:
        from app.services.v2.dataset_service import DatasetService
        if version:
            rows = DatasetService(db).preview(dataset.id, version.version_no, limit=25)
        else:
            rows = []
        columns = list(rows[0].keys()) if rows else list((dataset.schema_json or {}).get("columns") or [])
        descriptor["headers"] = columns
        descriptor["header_profile"] = [
            {"name": str(column), "sample_values": [row.get(column) for row in rows[:5]]}
            for column in columns[:100]
        ]
        descriptor["preview_row_count"] = len(rows)
    return descriptor


def _deterministic_acquisition_plan(descriptor: dict[str, Any]) -> dict[str, Any]:
    selection = descriptor.get("selection") or {}
    if descriptor.get("data_class") == "multimodal":
        return {
            "items": [{"kind": "samples", "sample_ids": list(selection.get("sample_ids") or []), "reason": "使用已选多模态样例"}, {"kind": "assets", "asset_roles": list(selection.get("selected_assets") or selection.get("modalities") or ["rgb"]), "reason": "使用已选证据模态"}],
            "summary": "本地规则根据用户选定的多模态样例和资产准备映射内容。",
        }
    fields = list(selection.get("fields") or selection.get("columns") or descriptor.get("headers") or [])
    return {"items": [{"kind": "table", "fields": fields, "max_rows": int(selection.get("row_limit") or selection.get("sample_limit") or 200), "reason": "使用已选字段和记录范围"}], "summary": "本地规则根据用户选定的字段和记录范围准备映射内容。"}


def _clip_acquisition_plan(plan: dict[str, Any], descriptor: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Intersect M3's plan with the explicit selection and record every clamp."""
    selection = descriptor.get("selection") or {}
    allowed_fields = {str(value) for value in (selection.get("fields") or selection.get("columns") or descriptor.get("headers") or [])}
    allowed_samples = {str(value) for value in selection.get("sample_ids", [])}
    allowed_assets = {str(value) for value in (selection.get("selected_assets") or selection.get("modalities") or descriptor.get("allowed_asset_roles") or [])}
    trace: list[dict[str, Any]] = []
    accepted_items: list[dict[str, Any]] = []
    for raw in _as_list(plan.get("items") if isinstance(plan, dict) else []):
        item = dict(raw) if isinstance(raw, dict) else {}
        fields = [str(value) for value in _as_list(item.get("fields"))]
        if fields and allowed_fields:
            clipped = [value for value in fields if value in allowed_fields]
            if set(clipped) != set(fields):
                trace.append({"stage": "范围校验", "status": "trimmed", "detail": "模型请求的字段已限制为用户选定字段", "requested": fields, "accepted": clipped})
            item["fields"] = clipped
        elif not fields and allowed_fields and item.get("kind") in {"table", "rows", "fields"}:
            item["fields"] = sorted(allowed_fields)
        sample_ids = [str(value) for value in _as_list(item.get("sample_ids"))]
        if sample_ids and allowed_samples:
            clipped = [value for value in sample_ids if value in allowed_samples]
            if set(clipped) != set(sample_ids):
                trace.append({"stage": "范围校验", "status": "trimmed", "detail": "模型请求的样例已限制为用户选定样例", "requested": sample_ids, "accepted": clipped})
            item["sample_ids"] = clipped
        elif not sample_ids and allowed_samples and item.get("kind") in {"samples", "assets", "multimodal"}:
            item["sample_ids"] = sorted(allowed_samples)
        roles = [str(value) for value in _as_list(item.get("asset_roles") or item.get("modalities"))]
        if roles and allowed_assets:
            clipped = [value for value in roles if value in allowed_assets]
            if set(clipped) != set(roles):
                trace.append({"stage": "范围校验", "status": "trimmed", "detail": "模型请求的模态已限制为用户选定模态", "requested": roles, "accepted": clipped})
            item["asset_roles"] = clipped
        elif not roles and allowed_assets and item.get("kind") in {"assets", "multimodal"}:
            item["asset_roles"] = sorted(allowed_assets)
        try:
            item["max_rows"] = max(1, min(int(item.get("max_rows") or selection.get("row_limit") or selection.get("sample_limit") or 200), 2000))
        except (TypeError, ValueError):
            item["max_rows"] = 200
        accepted_items.append(item)
    if not accepted_items:
        accepted_items = _deterministic_acquisition_plan(descriptor)["items"]
    return {"items": accepted_items, "summary": str(plan.get("summary") or plan.get("rationale") or "模型已生成读取计划")}, trace


def _pack_transfer_content(db: Session, draft: ConstructionDraft, descriptor: dict[str, Any], plan: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Materialise the second call's bounded data payload and its audit manifest."""
    dataset = db.query(Dataset).filter(Dataset.id == draft.dataset_id).first()
    if not dataset:
        raise ValueError("数据集不存在")
    version = _dataset_version(db, dataset)
    manifest: dict[str, Any] = {"dataset_id": dataset.id, "version_id": version.id if version else None, "items": []}
    if draft.data_class == "multimodal":
        from app.models.v2.dataset import MediaItem, MultimodalSample
        if not version:
            raise ValueError("多模态数据集没有版本")
        wanted_samples = {sample for item in plan.get("items", []) for sample in _as_list(item.get("sample_ids"))}
        wanted_roles = {role for item in plan.get("items", []) for role in _as_list(item.get("asset_roles"))}
        samples = db.query(MultimodalSample).filter(MultimodalSample.dataset_version_id == version.id).all()
        if wanted_samples:
            samples = [item for item in samples if item.id in wanted_samples]
        media = db.query(MediaItem).filter(MediaItem.dataset_version_id == version.id).all()
        packed_assets = []
        for item in media:
            if wanted_samples and item.sample_id not in wanted_samples:
                continue
            if wanted_roles and item.asset_role not in wanted_roles:
                continue
            # Mapping receives only addresses and deterministic summaries.
            # RGB and derived image previews are attached by the multimodal
            # worker when the route supports vision content; raw depth/mask/
            # point-cloud bytes are never represented here.
            packed_assets.append({"media_id": item.id, "sample_id": item.sample_id, "asset_role": item.asset_role, "mime_type": item.mime_type, "storage_uri": _safe_source_locator(item.storage_uri), "checksum": item.checksum, "metadata": item.metadata_json or {}})
        payload = {"source": descriptor, "plan": plan, "samples": [{"id": item.id, "sample_key": item.sample_key, "scene_id": item.scene_id, "label": item.label, "labels": item.labels or [], "metadata": item.metadata_json or {}} for item in samples], "assets": packed_assets}
        manifest["items"] = [{"kind": "asset", "media_id": item["media_id"], "sample_id": item["sample_id"], "asset_role": item["asset_role"], "checksum": item["checksum"]} for item in packed_assets]
        return payload, manifest
    from app.services.v2.dataset_service import DatasetService
    if not version:
        raise ValueError("数据集没有可读取版本")
    selected_fields = {field for item in plan.get("items", []) for field in _as_list(item.get("fields"))}
    max_rows = max([int(item.get("max_rows") or 1) for item in plan.get("items", [])] or [200])
    rows = DatasetService(db).preview(dataset.id, version.version_no, limit=max_rows)
    if selected_fields:
        rows = [{key: value for key, value in row.items() if key in selected_fields} for row in rows]
    payload = {"source": descriptor, "plan": plan, "headers": list(rows[0].keys()) if rows else descriptor.get("headers", []), "rows": rows}
    manifest["items"] = [{"kind": "row", "row_index": index + 1, "fields": list(row.keys())} for index, row in enumerate(rows)]
    return payload, manifest


def _multimodal_mapping_message_parts(
    db: Session,
    draft: ConstructionDraft,
    transfer_payload: dict[str, Any],
    transfer_manifest: dict[str, Any],
    mapping_request: str,
) -> list[dict[str, Any]] | str:
    """Attach explicitly selected visual evidence without persisting Base64.

    The durable invocation log stores ``mapping_request`` and the manifest
    only.  Image bytes exist in memory solely for this standard-privacy model
    call, then are discarded.  Depth arrays are converted to a pseudo-colour
    PNG and masks are overlaid over their paired RGB image so the model never
    receives raw numerical or binary modality files.
    """
    if draft.data_class != "multimodal":
        return mapping_request
    from app.models.v2.dataset import MediaItem
    from app.services.storage_service import get_storage_service

    allowed_roles = set((draft.selection_json or {}).get("selected_assets") or (draft.selection_json or {}).get("modalities") or [])
    visual_roles = {"rgb", "depth", "mask", "mask_visible"}
    if not (allowed_roles & visual_roles):
        return mapping_request
    listed = [item for item in transfer_payload.get("assets", []) if item.get("asset_role") in allowed_roles & visual_roles]
    # The fixed demo can contain 12 synchronized samples.  Cap the visual
    # pack deterministically so a user selection cannot create an unbounded
    # cloud request.  The full selected catalogue remains in the text payload.
    role_order = {"rgb": 0, "depth": 1, "mask": 2, "mask_visible": 3}
    listed.sort(key=lambda item: (str(item.get("sample_id") or ""), role_order.get(str(item.get("asset_role")), 9), str(item.get("media_id") or "")))
    selected = listed[:12]
    by_sample: dict[str, dict[str, MediaItem]] = {}
    media_by_id: dict[str, MediaItem] = {}
    if selected:
        ids = [str(item["media_id"]) for item in selected if item.get("media_id")]
        for item in db.query(MediaItem).filter(MediaItem.id.in_(ids)).all():
            media_by_id[item.id] = item
            if item.sample_id:
                by_sample.setdefault(item.sample_id, {})[item.asset_role] = item
    parts: list[dict[str, Any]] = [{"type": "text", "text": mapping_request}]
    visual_manifest: list[dict[str, Any]] = []
    omissions: list[dict[str, Any]] = []
    storage = get_storage_service()
    for listed_asset in selected:
        media = media_by_id.get(str(listed_asset.get("media_id") or ""))
        if media is None:
            continue
        role = media.asset_role
        try:
            payload: bytes
            mime: str
            label = role
            if role == "depth":
                # Reuse the deterministic rendering used by the browser.
                from app.routers.v2.multimodal import _depth_preview
                payload, _ = _depth_preview(media)
                mime = "image/png"
                label = "depth_pseudocolor"
            elif role in {"mask", "mask_visible"}:
                from PIL import Image
                mask_raw = storage.get_object(media.storage_uri)
                rgb_item = (by_sample.get(media.sample_id or "") or {}).get("rgb")
                if rgb_item is None:
                    raise ValueError("没有同一样例的 RGB 资产用于掩码叠加")
                rgb_raw = storage.get_object(rgb_item.storage_uri)
                rgb = Image.open(io.BytesIO(rgb_raw)).convert("RGBA")
                mask = Image.open(io.BytesIO(mask_raw)).convert("L").resize(rgb.size)
                overlay = Image.new("RGBA", rgb.size, (0, 220, 255, 0))
                overlay.putalpha(mask.point(lambda value: int(value * 0.58)))
                rendered = Image.alpha_composite(rgb, overlay).convert("RGB")
                output = io.BytesIO(); rendered.save(output, format="PNG", optimize=True)
                payload = output.getvalue(); mime = "image/png"; label = "mask_overlay"
            else:
                payload = storage.get_object(media.storage_uri)
                mime = media.mime_type or "image/png"
            data_url = f"data:{mime};base64,{base64.b64encode(payload).decode('ascii')}"
            parts.append({"type": "text", "text": f"视觉证据：{label}；asset_id={media.id}；sample_id={media.sample_id or ''}；sha256={media.checksum or ''}"})
            parts.append({"type": "image_url", "image_url": {"url": data_url}})
            visual_manifest.append({"media_id": media.id, "sample_id": media.sample_id, "asset_role": media.asset_role, "rendered_as": label, "checksum": media.checksum, "image_bytes_logged": False})
        except Exception as exc:
            omissions.append({"media_id": media.id, "asset_role": role, "reason": str(exc)[:180]})
    transfer_manifest["visual_evidence"] = visual_manifest
    if omissions:
        transfer_manifest["visual_evidence_omissions"] = omissions
    return parts


def _execute_regular_run(run_id: str, sample_limit: int = 5000) -> None:
    """Build a bounded, provenance-first graph for tabular datasets.

    This deterministic path is deliberately small: M3 proposes the mapping,
    while row identity, field values, evidence and graph writes remain
    reproducible and inspectable.  It also gives the regular five-step shell a
    real background task instead of leaving a permanently queued placeholder.
    """
    db = SessionLocal()
    try:
        run = db.query(ConstructionRun).filter(ConstructionRun.id == run_id).first()
        if not run:
            return
        from app.services.v2.dataset_service import DatasetService
        from app.services.v2.graph.falkordb_service import FalkorDBService
        from app.services.v2.revision_service import create_revision
        from app.services.v2.audit_runner import queue_local_audit
        dataset = db.query(Dataset).filter(Dataset.id == run.dataset_id).first() if run.dataset_id else None
        if not dataset:
            update_run(db, run, status="failed", error="数据集不存在")
            return
        version = db.query(DatasetVersion).filter(DatasetVersion.id == dataset.latest_version_id).first() if dataset.latest_version_id else None
        if version is None:
            version = db.query(DatasetVersion).filter(DatasetVersion.dataset_id == dataset.id).order_by(DatasetVersion.version_no.desc()).first()
        if not version:
            update_run(db, run, status="failed", error="数据集没有可用版本")
            return
        rows = DatasetService(db).preview(dataset.id, version.version_no, limit=max(1, min(int(sample_limit), 10000)))
        if not rows:
            update_run(db, run, status="failed", progress={"completed": 0, "total": 0}, error="数据集预览为空，无法构建")
            return
        update_run(db, run, status="running", progress={"stage": "读取常规数据", "completed": 0, "total": len(rows), "pct": 10})
        selection = (run.config or {}).get("selection") or {}
        fields = selection.get("fields") or selection.get("columns") or list(rows[0].keys())
        fields = [str(field) for field in fields if str(field) in rows[0]][:80]
        built_in = "c-mapss" in str(dataset.name or "").casefold() or "cmapss" in str(dataset.name or "").casefold()
        nodes: list[dict[str, Any]] = []
        relations: list[dict[str, Any]] = []
        if built_in:
            # C-MAPSS is a concrete equipment/readings model, not a generic
            # Dataset → Record graph.  Keep the graph plane aligned with the
            # canonical SQL ontology and its inspector terminology.
            seen_equipment_nodes: set[str] = set()
            for index, row in enumerate(rows):
                equipment_id = str(row.get("equipment_id") or "").strip()
                reading_id = str(row.get("reading_id") or f"row-{index + 1}").strip()
                if equipment_id and equipment_id not in seen_equipment_nodes:
                    seen_equipment_nodes.add(equipment_id)
                    nodes.append({
                        "id": f"equipment:{dataset.id}:{equipment_id}",
                        "entity_type": "Equipment",
                        "properties": {field: row.get(field) for field in fields if field in {"equipment_id", "equipment_type", "source_unit", "description"}},
                    })
                sensor_node = f"sensor-reading:{dataset.id}:{reading_id}"
                nodes.append({
                    "id": sensor_node,
                    "entity_type": "SensorReading",
                    "properties": {"row_index": index, **{field: row.get(field) for field in fields}},
                })
                if equipment_id:
                    relations.append({
                        "source": f"equipment:{dataset.id}:{equipment_id}",
                        "target": sensor_node,
                        "type": "HAS_SENSOR_READING",
                        "properties": {"equipment_id": equipment_id, "reading_id": reading_id, "dataset_version_id": version.id},
                    })
                if index % 100 == 0 or index == len(rows) - 1:
                    update_run(db, run, progress={"stage": "整理数据实例", "completed": index + 1, "total": len(rows), "pct": 20 + int((index + 1) / len(rows) * 30)})
        else:
            dataset_node = f"dataset:{dataset.id}"
            nodes.append({"id": dataset_node, "entity_type": "Dataset", "properties": {"name": dataset.name, "dataset_id": dataset.id, "version_id": version.id}})
            for index, row in enumerate(rows):
                key = next((str(row.get(field)).strip() for field in fields if row.get(field) not in (None, "")), str(index + 1))
                record_id = f"record:{dataset.id}:{index}:{key[:80]}"
                properties = {field: row.get(field) for field in fields}
                nodes.append({"id": record_id, "entity_type": "Record", "properties": {"row_index": index, "row_key": key, **properties}})
                relations.append({"source": dataset_node, "target": record_id, "type": "HAS_RECORD", "properties": {"row_index": index, "dataset_version_id": version.id}})
                if index % 100 == 0 or index == len(rows) - 1:
                    update_run(db, run, progress={"stage": "整理数据实例", "completed": index + 1, "total": len(rows), "pct": 20 + int((index + 1) / len(rows) * 30)})
        update_run(db, run, progress={"stage": "写入本体", "completed": len(rows), "total": len(rows), "pct": 58})
        mapping = ((run.config or {}).get("mapping") or {}).get("ontology_mapping") or ((run.config or {}).get("mapping") or {})
        if built_in:
            checked = normalise_mapping(mapping, data_class="regular")
            if checked.errors:
                raise ValueError("C-MAPSS 映射校验失败：" + "；".join(checked.errors))
            entity_ids = {item["id"] for item in checked.mapping.get("entity_types", [])}
            relationships = checked.mapping.get("relationships", [])
            if not {"equipment", "sensor_reading"}.issubset(entity_ids):
                raise ValueError("C-MAPSS 映射必须确认 Equipment 与 SensorReading 两个实体类型")
            if not any(item.get("from") == "equipment" and item.get("to") == "sensor_reading" for item in relationships):
                raise ValueError("C-MAPSS 映射必须确认 Equipment → SensorReading 关系")
            equipment_rows: list[dict[str, Any]] = []
            seen_equipment: set[str] = set()
            for row in rows:
                equipment_id = str(row.get("equipment_id") or "")
                if not equipment_id or equipment_id in seen_equipment:
                    continue
                seen_equipment.add(equipment_id)
                equipment_rows.append({key: row.get(key) for key in ("equipment_id", "equipment_type", "source_unit", "description") if key in row})
            materialized = materialize_ontology(
                db, run=run, mapping=checked.mapping, instances=equipment_rows, data_class="regular",
                source_file=version.source_path or version.storage_uri, source_version=version.id,
                instance_entity="equipment", model_name=run.model_name, require_rule=True,
            )
            reading_materialized = materialize_ontology(
                db, run=run, mapping=checked.mapping, instances=rows, data_class="regular",
                source_file=version.source_path or version.storage_uri, source_version=version.id,
                instance_entity="sensor_reading", model_name=run.model_name, require_rule=False,
            )
            materialized.instance_count += reading_materialized.instance_count
            materialized.evidence_count += reading_materialized.evidence_count
        else:
            materialized = materialize_ontology(
                db, run=run, mapping=mapping, instances=rows, data_class="regular",
                source_file=version.source_path or version.storage_uri, source_version=version.id,
                instance_entity="Record", model_name=run.model_name, require_rule=False,
            )
        db.commit()
        update_run(db, run, progress={"stage": "写入证据关系", "completed": len(rows), "total": len(rows), "pct": 76})
        graph = FalkorDBService()
        written_nodes = graph.upsert_instances(run.ontology_id, nodes)
        written_edges = graph.upsert_relations(run.ontology_id, relations)
        revision = create_revision(db, run.ontology_id, source_run_id=run.id, summary={"rows_processed": len(rows), "nodes_written": written_nodes, "edges_written": written_edges, "entity_type_count": materialized.entity_type_count, "instance_count": materialized.instance_count, "relation_count": materialized.relation_count, "logic_rule_count": materialized.rule_count, "privacy_level": (run.config or {}).get("privacy_level", "standard")})
        run.revision_id = revision.id
        attach_revision_to_materialized_rows(db, run=run, revision_id=revision.id)
        project = db.query(OntologyProject).filter(OntologyProject.id == run.ontology_id).first()
        if project:
            project.status = "created"
        db.commit()
        queue_local_audit(db, ontology_id=run.ontology_id, revision_id=revision.id, construction_run_id=run.id)
        update_run(db, run, status="completed", progress={"stage": "构建完成", "completed": len(rows), "total": len(rows), "pct": 100}, metrics={"rows_processed": len(rows), "nodes_written": written_nodes, "edges_written": written_edges, "entity_type_count": materialized.entity_type_count, "instance_count": materialized.instance_count, "relation_count": materialized.relation_count, "logic_rule_count": materialized.rule_count, "revision_id": revision.id, "privacy_level": (run.config or {}).get("privacy_level", "standard")})
    except Exception as exc:
        db.rollback()
        run = db.query(ConstructionRun).filter(ConstructionRun.id == run_id).first()
        if run:
            update_run(db, run, status="failed", error=str(exc)[:2000])
    finally:
        db.close()


@router.post("", status_code=201)
def create_draft(body: DraftCreate, db: Session = Depends(get_db), _=Depends(require_editor)):
    dataset = db.query(Dataset).filter(Dataset.id == body.dataset_id).first()
    if not dataset:
        raise HTTPException(404, "数据集不存在")
    target_mode = body.target_mode or ("append" if body.ontology_id else "create")
    _validate_target(db, dataset=dataset, data_class=body.data_class or dataset.data_class, target_mode=target_mode, ontology_id=body.ontology_id, new_ontology_name=body.new_ontology_name)
    privacy = "private" if dataset.privacy_level == "private" else body.privacy_level
    draft = ConstructionDraft(
        dataset_id=dataset.id,
        ontology_id=body.ontology_id,
        data_class=body.data_class or dataset.data_class or "regular",
        target_mode=target_mode,
        new_ontology_name=(body.new_ontology_name or "").strip() or None,
        new_ontology_domain=(body.new_ontology_domain or "").strip() or None,
        privacy_level=privacy,
        selection_json=body.selection,
        processing_json=body.processing_config,
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return serialize_draft(draft)


@router.get("/{draft_id}")
def get_draft(draft_id: str, db: Session = Depends(get_db)):
    draft = db.query(ConstructionDraft).filter(ConstructionDraft.id == draft_id).first()
    if not draft:
        raise HTTPException(404, "构筑草案不存在")
    return serialize_draft(draft)


@router.patch("/{draft_id}")
def patch_draft(draft_id: str, body: DraftPatch, db: Session = Depends(get_db), _=Depends(require_editor)):
    draft = db.query(ConstructionDraft).filter(ConstructionDraft.id == draft_id).first()
    if not draft:
        raise HTTPException(404, "构筑草案不存在")
    updates = body.model_dump(exclude_none=True)
    if "privacy_level" in updates:
        dataset = db.query(Dataset).filter(Dataset.id == draft.dataset_id).first()
        requested = str(updates["privacy_level"])
        if dataset and dataset.privacy_level == "private":
            requested = "private"
        updates["privacy_level"] = requested
    target_mode = updates.get("target_mode", draft.target_mode)
    target_id = updates.get("ontology_id", draft.ontology_id)
    target_name = updates.get("new_ontology_name", draft.new_ontology_name)
    target_data_class = updates.get("data_class", draft.data_class)
    dataset = db.query(Dataset).filter(Dataset.id == draft.dataset_id).first()
    if dataset:
        _validate_target(db, dataset=dataset, data_class=target_data_class, target_mode=target_mode, ontology_id=target_id, new_ontology_name=target_name)
    if "selection" in updates:
        selection = updates.pop("selection")
        # The UI persists its current selection before both mapping and build.
        # Only a material change invalidates the confirmed send scope; an
        # identical PATCH must not erase a just-confirmed mapping.
        if selection != (draft.selection_json or {}):
            draft.selection_json = selection
            draft.preflight_json = {}
            draft.mapping_json = {}
            draft.mapping_task_id = None
            draft.status = "draft"
    if "processing_config" in updates:
        draft.processing_json = updates.pop("processing_config")
    if "mapping" in updates:
        incoming = updates.pop("mapping") or {}
        # Existing mapping pages submit their checkbox selection as
        # ``suggestions``/``confirmed``.  Preserve the canonical v2 mapping
        # produced by the two-stage task instead of replacing it with a flat
        # display list.
        current = dict(draft.mapping_json or {})
        if current.get("ontology_mapping") and any(key in incoming for key in ("suggestions", "confirmed")):
            current["confirmed"] = incoming.get("confirmed", incoming.get("suggestions", []))
            current["suggestions"] = incoming.get("suggestions", current.get("suggestions", []))
            current["confirmed_at"] = datetime.now(timezone.utc).isoformat()
            draft.mapping_json = current
        else:
            draft.mapping_json = incoming
    for key, value in updates.items():
        setattr(draft, key, value)
    db.commit()
    db.refresh(draft)
    return serialize_draft(draft)


@router.post("/{draft_id}/m3-preflight")
def m3_preflight(draft_id: str, db: Session = Depends(get_db), _=Depends(require_editor)):
    draft = db.query(ConstructionDraft).filter(ConstructionDraft.id == draft_id).first()
    if not draft:
        raise HTTPException(404, "构筑草案不存在")
    selected = draft.selection_json or {}
    summary = {
        "dataset_id": draft.dataset_id, "data_class": draft.data_class,
        "privacy_level": draft.privacy_level, "sample_ids": selected.get("sample_ids", []),
        "fields": selected.get("fields", selected.get("columns", [])),
        "modalities": selected.get("modalities", selected.get("selected_assets", [])),
        "send_fields": selected.get("send_fields", []),
        "binary_excluded": ["raw_point_cloud", "depth_array", "mask_binary"],
    }
    config = None if draft.privacy_level == "private" else _select_m3_config(db, multimodal=draft.data_class == "multimodal")
    preflight = {"allowed": draft.privacy_level == "standard" and bool(config), "privacy_level": draft.privacy_level, "model": str((config.models or [None])[0]) if config else None, "model_id": config.id if config else None, "summary": summary, "requires_explicit_confirm": False, "two_stage": True, "stages": ["来源识别", "读取计划", "范围校验", "内容发送", "本体生成"]}
    draft.preflight_json = preflight
    draft.status = "preflight_ready" if preflight["allowed"] or draft.privacy_level == "private" else "waiting_for_model"
    draft.error = None if preflight["allowed"] or draft.privacy_level == "private" else "标准数据需要先配置 MiniMax M3；不会自动切换本地模型"
    db.commit()
    return {"draft": serialize_draft(draft), "preflight": preflight}


def _merge_mapping_suggestions(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep every M3 class/property/relation suggestion in deterministic order."""
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group, kind in (("classes", "class"), ("properties", "property"), ("relations", "relation"), ("suggestions", "suggestion")):
        values = payload.get(group) or []
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, dict):
                value = {"target": str(value)}
            item = {"kind": value.get("kind") or kind, **value}
            key = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
            if key not in seen:
                seen.add(key)
                merged.append(item)
    return merged


def _execute_mapping_task(task_id: str) -> None:
    db = SessionLocal()
    invocation = None
    started = time.monotonic()
    try:
        task = db.query(MappingTask).filter(MappingTask.id == task_id).with_for_update().first()
        if not task:
            return
        # Celery can deliver a message more than once after a worker/API
        # restart.  Claim the task row atomically so only one process calls
        # MiniMax or writes a mapping result.
        if task.status in {"running", "completed"}:
            return
        if task.cancel_requested:
            task.status = "cancelled"
            db.commit()
            return
        task.status = "running"
        task.stage = "来源识别"
        task.progress = {"stage": "来源识别", "pct": 8}
        task.trace_json = [{"step": 1, "stage": "来源识别", "status": "running", "detail": "正在读取数据来源、表头和已选范围"}]
        task.started_at = datetime.now(timezone.utc)
        db.commit()
        draft = db.query(ConstructionDraft).filter(ConstructionDraft.id == task.draft_id).first()
        if not draft:
            raise ValueError("构筑草案不存在")
        config = _select_m3_config(db, multimodal=draft.data_class == "multimodal") if draft.privacy_level != "private" else None
        descriptor = _source_descriptor(db, draft)
        trace = list(task.trace_json or [])
        trace.append({"step": 1, "stage": "来源识别", "status": "completed", "detail": "已读取来源地址、结构、表头/模态目录和用户选定范围"})
        if draft.privacy_level == "private":
            raw_plan = _deterministic_acquisition_plan(descriptor)
            plan_engine = "rules"
        else:
            kwargs = llm_call_kwargs(config) if config else None
            if not kwargs:
                raise RuntimeError("MiniMax M3 尚未配置或无法解密；映射停在等待模型")
            from app.services.llm_service import _call_llm, _parse_response
            task.stage = "读取计划"
            task.progress = {"stage": "读取计划", "pct": 22}
            trace.append({"step": 2, "stage": "读取计划", "status": "running", "detail": "MiniMax 正在根据来源与表头规划下一次读取内容"})
            request_payload = json.dumps(descriptor, ensure_ascii=False, sort_keys=True, default=str)
            invocation = ModelInvocation(
                draft_id=draft.id, mapping_task_id=task.id, route_alias=task.model_route or MODEL_NAME,
                provider=config.provider, model_name=str((config.models or [MODEL_NAME])[0]), status="running", phase="source_analysis",
                request_ciphertext=encryption_service.encrypt(request_payload), request_hash=hashlib.sha256(request_payload.encode("utf-8")).hexdigest(),
                metadata_json={"purpose": "source_analysis", "data_class": draft.data_class, "privacy_level": draft.privacy_level, "source_locator": descriptor.get("dataset", {}).get("source_locator")},
            )
            db.add(invocation); db.commit()
            raw = _call_llm(kwargs["provider"], kwargs["api_key"], kwargs["api_base"], kwargs["model"], [
                {"role": "system", "content": "只返回 JSON。你是本体构筑的数据读取规划器。根据来源档案和表头，返回 {items:[{kind,fields?,sample_ids?,asset_roles?,max_rows,reason}],summary}。只能请求已选范围内的数据；不要输出隐藏推理、不要生成本体。"},
                {"role": "user", "content": request_payload},
            ], json_mode=True)
            parsed = _parse_response(raw)
            if not isinstance(parsed, dict):
                raise ValueError("MiniMax M3 来源读取计划不是对象")
            raw_plan = parsed.get("data_acquisition_plan") if isinstance(parsed.get("data_acquisition_plan"), dict) else parsed
            invocation.status = "completed"; invocation.phase = "source_analysis"
            response_payload = json.dumps(raw_plan, ensure_ascii=False, sort_keys=True, default=str)
            invocation.response_ciphertext = encryption_service.encrypt(response_payload)
            invocation.response_hash = hashlib.sha256(response_payload.encode("utf-8")).hexdigest()
            invocation.duration_ms = int((time.monotonic() - started) * 1000); invocation.completed_at = datetime.now(timezone.utc)
            db.commit()
            plan_engine = "m3"
        plan, clip_trace = _clip_acquisition_plan(raw_plan, descriptor)
        trace.append({"step": 2, "stage": "读取计划", "status": "completed", "detail": plan.get("summary") or "已生成读取计划"})
        trace.extend({"step": 3, **entry} for entry in clip_trace)
        if not clip_trace:
            trace.append({"step": 3, "stage": "范围校验", "status": "completed", "detail": "计划全部位于用户已选范围内"})
        task.source_plan_json = plan
        task.stage = "内容准备"; task.progress = {"stage": "内容准备", "pct": 42}; task.trace_json = trace
        db.commit()
        transfer_payload, transfer_manifest = _pack_transfer_content(db, draft, descriptor, plan)
        task.transfer_manifest_json = transfer_manifest
        trace.append({"step": 4, "stage": "内容发送", "status": "completed", "detail": f"已按计划准备 {len(transfer_manifest.get('items', []))} 项内容"})
        if draft.privacy_level == "private":
            raw_mapping = _deterministic_mapping(draft, descriptor=descriptor)
            plan_engine = "rules"
        else:
            kwargs = llm_call_kwargs(config) if config else None
            if not kwargs:
                raise RuntimeError("MiniMax M3 尚未配置或无法解密；映射停在等待模型")
            task.stage = "本体生成"; task.progress = {"stage": "本体生成", "pct": 62}; task.trace_json = trace + [{"step": 5, "stage": "本体生成", "status": "running", "detail": "MiniMax 正在生成实体、属性、关系和逻辑规则"}]
            db.commit()
            mapping_request = json.dumps(transfer_payload, ensure_ascii=False, sort_keys=True, default=str)
            # Keep the durable request text-only.  Selected multimodal visual
            # evidence is appended only in memory for this one M3 request;
            # the task manifest records asset references and hashes, never
            # image bytes or Base64.
            mapping_message = _multimodal_mapping_message_parts(
                db,
                draft,
                transfer_payload,
                transfer_manifest,
                mapping_request,
            )
            task.transfer_manifest_json = transfer_manifest
            invocation = ModelInvocation(
                draft_id=draft.id, mapping_task_id=task.id, route_alias=task.model_route or MODEL_NAME,
                provider=config.provider, model_name=str((config.models or [MODEL_NAME])[0]), status="running", phase="ontology_mapping",
                request_ciphertext=encryption_service.encrypt(mapping_request), request_hash=hashlib.sha256(mapping_request.encode("utf-8")).hexdigest(),
                metadata_json={"purpose": "ontology_mapping", "data_class": draft.data_class, "privacy_level": draft.privacy_level, "transfer_manifest": transfer_manifest},
            )
            db.add(invocation); db.commit()
            fixed_case_instruction = ""
            dataset_name = str((descriptor.get("dataset") or {}).get("name") or "")
            if draft.data_class == "regular" and "c-mapss" in dataset_name.casefold():
                fixed_case_instruction = " 这是 NASA C-MAPSS FD001 固定子集：必须包含 id=equipment 的 Equipment（设备）和 id=sensor_reading 的 SensorReading（传感器读数）实体类型，必须包含从 equipment 到 sensor_reading 的 one-to-many 关系；规则只能引用 cycle、运行设置或实际传感器字段，不能编造阈值或日期。"
            raw = _call_llm(kwargs["provider"], kwargs["api_key"], kwargs["api_base"], kwargs["model"], [
                {"role": "system", "content": "只返回 JSON。基于已发送数据生成 ontology-mapping-v2：{schema_version:'ontology-mapping-v2',entity_types:[{id,name,name_cn?,name_en?,description,source_fields,identifier_property,confidence}],properties:[{id,entity_type,name,type,is_identifier?,unit?,values?,description?,source_field,confidence}],relationships:[{id,name,from,to,cardinality,description,attributes?,source_fields?,confidence}],logic_rules:[{id,name,name_cn?,description,if,then,linked_entities,confidence,evidence}]}. 关系必须连接已有实体；规则必须有 IF 和 THEN，且只能基于已发送字段/样例提出。不要生成配置项、隐私策略或无来源的业务实体。" + fixed_case_instruction},
                {"role": "user", "content": mapping_message},
            ], json_mode=True)
            raw_mapping = _parse_response(raw)
            if not isinstance(raw_mapping, dict):
                raise ValueError("MiniMax M3 本体映射不是对象")
            invocation.status = "completed"; invocation.phase = "ontology_mapping"
            response_payload = json.dumps(raw_mapping, ensure_ascii=False, sort_keys=True, default=str)
            invocation.response_ciphertext = encryption_service.encrypt(response_payload)
            invocation.response_hash = hashlib.sha256(response_payload.encode("utf-8")).hexdigest()
            invocation.duration_ms = int((time.monotonic() - started) * 1000); invocation.completed_at = datetime.now(timezone.utc)
            db.commit()
        validation = normalise_mapping(raw_mapping, data_class=draft.data_class)
        if validation.errors:
            raise ValueError("本体映射校验失败：" + "；".join(validation.errors))
        mapping = {"status": "ready", "engine": plan_engine, "model": MODEL_NAME if plan_engine == "m3" else "规则 + 人工", "ontology_mapping": validation.mapping, "suggestions": mapping_suggestions(validation.mapping, extractor="m3" if plan_engine == "m3" else "规则处理"), "source_plan": plan, "transfer_manifest": transfer_manifest, "trace": trace + [{"step": 5, "stage": "本体生成", "status": "completed", "detail": "映射结构已通过引用、数据类型和基数校验"}], "validation_warnings": validation.warnings, "confirmed": False}
        if task.cancel_requested:
            task.status = "cancelled"
            draft.status = "draft"
            db.commit()
            return
        task.result_json = mapping
        task.stage = "等待确认"
        task.progress = {"stage": "等待确认", "pct": 100}
        task.trace_json = mapping["trace"]
        task.source_plan_json = plan
        task.transfer_manifest_json = transfer_manifest
        task.status = "completed"
        task.completed_at = datetime.now(timezone.utc)
        task.error = None
        draft.mapping_json = mapping
        draft.mapping_task_id = task.id
        draft.status = "mapping_ready"
        draft.error = None
        # Private drafts are deliberately mapped by deterministic rules and
        # have no cloud ModelInvocation.  Only persist a model response when
        # an upstream model was actually called; otherwise the private path
        # would fail after producing a valid mapping.
        if invocation is not None:
            response_payload = json.dumps(mapping, ensure_ascii=False, sort_keys=True, default=str)
            invocation.status = "completed"
            invocation.response_ciphertext = encryption_service.encrypt(response_payload)
            invocation.response_hash = hashlib.sha256(response_payload.encode("utf-8")).hexdigest()
            invocation.duration_ms = int((time.monotonic() - started) * 1000)
            invocation.completed_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as exc:
        db.rollback()
        task = db.query(MappingTask).filter(MappingTask.id == task_id).first()
        if task:
            task.status = "waiting_for_model" if "MiniMax M3" in str(exc) else "failed"
            task.stage = "等待模型" if task.status == "waiting_for_model" else "失败"
            task.progress = {"stage": task.stage, "pct": 0}
            task.error = str(exc)[:1000]
            task.completed_at = datetime.now(timezone.utc)
            draft = db.query(ConstructionDraft).filter(ConstructionDraft.id == task.draft_id).first()
            if draft:
                draft.status = "waiting_for_model" if task.status == "waiting_for_model" else "failed"
                draft.error = task.error
            if invocation is not None:
                failed_invocation = db.query(ModelInvocation).filter(ModelInvocation.id == invocation.id).first()
                if failed_invocation:
                    failed_invocation.status = task.status
                    failed_invocation.error = str(exc)[:1000]
                    failed_invocation.duration_ms = int((time.monotonic() - started) * 1000)
                    failed_invocation.completed_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()


@router.post("/{draft_id}/generate-mapping", status_code=202)
def generate_mapping(draft_id: str, db: Session = Depends(get_db), _=Depends(require_editor)):
    draft = db.query(ConstructionDraft).filter(ConstructionDraft.id == draft_id).first()
    if not draft:
        raise HTTPException(404, "构筑草案不存在")
    active = db.query(MappingTask).filter(MappingTask.draft_id == draft.id, MappingTask.status.in_(["queued", "running"])).order_by(MappingTask.created_at.desc()).first()
    if active:
        return {"draft": serialize_draft(draft), "mapping_task_id": active.id, "mapping": {"status": active.status, "stage": active.stage, "suggestions": []}}
    payload_hash = hashlib.sha256(json.dumps({"data_class": draft.data_class, "selection": draft.selection_json or {}, "processing": draft.processing_json or {}}, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
    task = MappingTask(draft_id=draft.id, status="queued", stage="排队", progress={"stage": "排队", "pct": 0}, trace_json=[{"step": 0, "stage": "排队", "status": "queued", "detail": "映射任务已提交"}], source_plan_json={}, transfer_manifest_json={}, schema_version="ontology-mapping-v2", model_route=MODEL_NAME if draft.privacy_level != "private" else "rules", payload_hash=payload_hash, result_json={})
    db.add(task)
    draft.mapping_task_id = task.id
    draft.status = "draft" if draft.privacy_level == "private" else "preflight_ready"
    draft.error = None
    db.commit()
    db.refresh(task)
    try:
        if os.getenv("CELERY_ENABLED", "").lower() in {"1", "true", "yes"}:
            from app.tasks.v2.workbench import run_mapping_task
            run_mapping_task.delay(task.id)
        else:
            _execute_mapping_task(task.id)
    except Exception as exc:
        task.status = "failed"
        task.error = f"映射任务未派发：{str(exc)[:500]}"
        db.commit()
    return {"draft": serialize_draft(draft), "mapping_task_id": task.id, "mapping": {"status": task.status, "stage": task.stage, "suggestions": task.result_json.get("suggestions", []) if task.status == "completed" else [], "error": task.error}}


@mapping_tasks_router.get("/mapping-tasks/{task_id}")
def get_mapping_task(task_id: str, db: Session = Depends(get_db)):
    task = db.query(MappingTask).filter(MappingTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "映射任务不存在")
    return {"id": task.id, "draft_id": task.draft_id, "status": task.status, "stage": task.stage, "progress": task.progress or {}, "trace": task.trace_json or [], "source_plan": task.source_plan_json or {}, "transfer_manifest": task.transfer_manifest_json or {}, "result": task.result_json or {}, "error": task.error, "cancel_requested": task.cancel_requested}


@mapping_tasks_router.post("/mapping-tasks/{task_id}/retry")
def retry_mapping_task(task_id: str, db: Session = Depends(get_db), _=Depends(require_editor)):
    task = db.query(MappingTask).filter(MappingTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "映射任务不存在")
    if task.status in {"queued", "running"}:
        return {"id": task.id, "status": task.status}
    task.status, task.stage, task.error, task.cancel_requested = "queued", "排队", None, False
    task.progress = {"stage": "排队", "pct": 0}
    task.trace_json = [{"step": 0, "stage": "排队", "status": "queued", "detail": "任务已重新提交"}]
    db.commit()
    try:
        if os.getenv("CELERY_ENABLED", "").lower() in {"1", "true", "yes"}:
            from app.tasks.v2.workbench import run_mapping_task
            run_mapping_task.delay(task.id)
        else:
            _execute_mapping_task(task.id)
    except Exception as exc:
        task.status, task.error = "failed", str(exc)[:500]
        db.commit()
    return {"id": task.id, "status": task.status}


@mapping_tasks_router.post("/mapping-tasks/{task_id}/cancel")
def cancel_mapping_task(task_id: str, db: Session = Depends(get_db), _=Depends(require_editor)):
    task = db.query(MappingTask).filter(MappingTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "映射任务不存在")
    if task.status in {"queued", "running"}:
        task.cancel_requested = True
        db.commit()
    return {"id": task.id, "status": task.status, "cancel_requested": task.cancel_requested}


@router.post("/{draft_id}/build", status_code=202)
def build_draft(draft_id: str, body: BuildRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db), current_user: User = Depends(require_editor)):
    draft = db.query(ConstructionDraft).filter(ConstructionDraft.id == draft_id).first()
    if not draft:
        raise HTTPException(404, "构筑草案不存在")
    mapping_payload = (draft.mapping_json or {}).get("ontology_mapping") or (draft.mapping_json or {})
    validation = normalise_mapping(mapping_payload, data_class=draft.data_class)
    if not body.mapping_confirmed or not (draft.mapping_json or {}).get("suggestions"):
        raise HTTPException(409, "请先确认本体映射建议")
    if validation.errors:
        raise HTTPException(409, "本体映射不可构建：" + "；".join(validation.errors))
    dataset = db.query(Dataset).filter(Dataset.id == draft.dataset_id).first()
    if not dataset:
        raise HTTPException(404, "数据集不存在")
    _validate_target(db, dataset=dataset, data_class=draft.data_class, target_mode=draft.target_mode, ontology_id=draft.ontology_id, new_ontology_name=draft.new_ontology_name)
    if draft.target_mode == "create":
        name = (draft.new_ontology_name or "").strip()
        duplicate = db.query(OntologyProject).filter(OntologyProject.name.ilike(name)).first()
        if duplicate:
            raise HTTPException(409, f"Ontology 名称「{name}」已存在")
        project = OntologyProject(
            id=str(uuid.uuid4()), name=name,
            domain=draft.new_ontology_domain or _default_domain(draft.data_class),
            build_mode=_build_mode(draft.data_class), data_class=draft.data_class,
            created_by=current_user.id,
        )
        db.add(project)
        db.flush()
        draft.ontology_id = project.id
    if not draft.ontology_id:
        raise HTTPException(409, "构筑草案没有目标本体")
    run = create_run(db, ontology_id=draft.ontology_id, dataset_id=draft.dataset_id, mode=draft.data_class, model_name=(draft.preflight_json or {}).get("model"), config={"draft_id": draft.id, "privacy_level": draft.privacy_level, "selection": draft.selection_json or {}, "processing": draft.processing_json or {}, "mapping": {**(draft.mapping_json or {}), "ontology_mapping": validation.mapping}, "mode": body.mode})
    draft.build_run_id = run.id
    draft.status = "building"
    db.commit()
    celery_enabled = os.getenv("CELERY_ENABLED", "").lower() in {"1", "true", "yes"}
    if draft.data_class == "multimodal":
        selection = draft.selection_json or {}
        args = (run.id, body.model_id, int(selection.get("sample_limit", 32)), selection.get("prompt"), selection.get("sample_ids", []), selection.get("selected_assets", []), draft.privacy_level, selection.get("send_fields", []))
        if celery_enabled:
            from app.tasks.v2.workbench import run_multimodal_construction_task
            run_multimodal_construction_task.delay(*args)
        else:
            from app.routers.v2.multimodal import _execute_multimodal_run
            background_tasks.add_task(_execute_multimodal_run, *args)
    elif draft.data_class == "regular":
        selection = draft.selection_json or {}
        sample_limit = int(selection.get("row_limit", selection.get("sample_limit", 5000)))
        if celery_enabled:
            from app.tasks.v2.workbench import run_regular_construction_task
            run_regular_construction_task.delay(run.id, sample_limit)
        else:
            background_tasks.add_task(_execute_regular_run, run.id, sample_limit)
    return serialize_run(run)
