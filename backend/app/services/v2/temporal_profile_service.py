"""Deterministic profiling and strict MiniMax M3 schema analysis."""
from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from app.models.model_config import ModelConfig
from app.models.v2.construction import EvidenceRef
from app.models.v2.dataset import Dataset, DatasetVersion
from app.models.v2.temporal_profile import TemporalDatasetProfile
from app.services.model_config_selector import llm_call_kwargs
from app.services.storage_service import get_storage_service
from app.services.v2.construction_service import serialize_run

logger = logging.getLogger(__name__)
PROFILE_PROMPT_VERSION = "temporal-profile-v1"


class LlmTemporalSuggestion(BaseModel):
    summary: str = ""
    time_kind: str = Field(pattern="^(instant|ordinal|interval)$")
    time_column: str | None = None
    sequence_column: str | None = None
    valid_from_column: str | None = None
    valid_to_column: str | None = None
    entity_column: str | None = None
    observation_id_column: str | None = None
    filter_columns: list[str] = Field(default_factory=list)
    measurement_columns: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    ontology_classes: list[str] = Field(default_factory=list)
    relations: list[dict[str, str]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _physical_type(values: list[Any]) -> str:
    nonempty = [value for value in values if value not in (None, "")]
    if not nonempty:
        return "empty"
    if all(isinstance(value, bool) for value in nonempty):
        return "boolean"
    if all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in nonempty):
        return "number"
    text_values = [str(value).strip() for value in nonempty[:100]]
    if text_values and sum(bool(re.match(r"^\d{4}-\d{1,2}-\d{1,2}(?:[T ].*)?$", value)) for value in text_values) / len(text_values) >= 0.8:
        return "datetime"
    return "string"


def profile_rows(rows: list[dict[str, Any]], *, filename: str | None = None, checksum: str | None = None) -> dict[str, Any]:
    columns = list(dict.fromkeys(str(key) for row in rows[:200] for key in row.keys() if not str(key).startswith("_")))
    column_profiles: list[dict[str, Any]] = []
    for column in columns:
        values = [row.get(column) for row in rows]
        nonempty = [value for value in values if value not in (None, "")]
        counts = Counter(str(value) for value in nonempty)
        column_profiles.append({
            "name": column,
            "type": _physical_type(values),
            "nullable": len(nonempty) != len(values),
            "null_count": len(values) - len(nonempty),
            "unique_count": len(counts),
            "sample": [str(value)[:120] for value in nonempty[:5]],
            "min": min(nonempty) if nonempty and all(isinstance(value, (int, float)) for value in nonempty) else None,
            "max": max(nonempty) if nonempty and all(isinstance(value, (int, float)) for value in nonempty) else None,
        })
    datetime_candidates = [p["name"] for p in column_profiles if p["type"] == "datetime" or re.search(r"time|date|timestamp", p["name"], re.I)]
    sequence_candidates = [p["name"] for p in column_profiles if re.search(r"cycle|step|seq|sequence|elapsed|time_s", p["name"], re.I)]
    entity_candidates = [
        p["name"] for p in column_profiles
        if re.search(r"episode|equipment|machine|device|unit|stream|series|group", p["name"], re.I)
        and not re.search(r"reading|observation|row", p["name"], re.I)
    ]
    dimensions = [p["name"] for p in column_profiles if p["type"] == "string" and 1 < p["unique_count"] <= 100 and not re.search(r"id|uuid|hash", p["name"], re.I)]
    measurements = [p["name"] for p in column_profiles if p["type"] == "number"]
    return {
        "filename": filename,
        "checksum": checksum,
        "row_count": len(rows),
        "column_count": len(columns),
        "columns": column_profiles,
        "time_candidates": list(dict.fromkeys(datetime_candidates + sequence_candidates)),
        "entity_candidates": entity_candidates,
        "dimensions": dimensions,
        "measurement_columns": measurements,
        "sample_rows": [{key: row.get(key) for key in columns[:40]} for row in rows[:5]],
    }


def _pick_m3_config(db: Session) -> ModelConfig | None:
    configs = db.query(ModelConfig).filter(ModelConfig.config_type == "llm").all()
    return next((config for config in configs if "MiniMax-M3" in [str(item) for item in (config.models or [])]), None)


def _validate_suggestion(raw: str, profile: dict[str, Any]) -> dict[str, Any]:
    from app.services.llm_service import _parse_response

    parsed = _parse_response(raw)
    suggestion = LlmTemporalSuggestion.model_validate(parsed)
    columns = {item["name"] for item in profile.get("columns", [])}
    for field in ("time_column", "sequence_column", "valid_from_column", "valid_to_column", "entity_column", "observation_id_column"):
        value = getattr(suggestion, field)
        if value and value not in columns:
            raise ValueError(f"M3 推荐了不存在的列: {value}")
    for field in ("filter_columns", "measurement_columns", "dimensions"):
        values = getattr(suggestion, field)
        unknown = [value for value in values if value not in columns]
        if unknown:
            raise ValueError(f"M3 推荐了不存在的列: {unknown[0]}")
    if suggestion.time_kind == "instant" and not suggestion.time_column:
        raise ValueError("M3 选择 Instant 但没有时间列")
    if suggestion.time_kind == "ordinal" and not suggestion.sequence_column:
        raise ValueError("M3 选择 Ordinal 但没有顺序列")
    if suggestion.time_kind == "interval" and not suggestion.valid_from_column:
        raise ValueError("M3 选择 Interval 但没有开始列")
    return suggestion.model_dump()


def run_profile(profile_id: str) -> None:
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        profile = db.query(TemporalDatasetProfile).filter(TemporalDatasetProfile.id == profile_id).first()
        if not profile:
            return
        version = db.query(DatasetVersion).filter(DatasetVersion.id == profile.dataset_version_id).first()
        if not version or not version.storage_uri:
            profile.status = "failed"; profile.error = "数据版本没有可读取的存储对象"; db.commit(); return
        from app.routers.v2.temporal import parse_temporal_bytes
        rows = parse_temporal_bytes(get_storage_service().get_object(version.storage_uri))
        profile.deterministic_profile = profile_rows(rows, checksum=version.checksum)
        config = _pick_m3_config(db)
        if not config:
            profile.status = "failed"; profile.error = "MiniMax-M3 未配置"; db.commit(); return
        kwargs = llm_call_kwargs(config)
        if not kwargs or not kwargs.get("api_key"):
            profile.status = "failed"; profile.error = "MiniMax-M3 凭据无法解密"; db.commit(); return
        prompt = (
            "你是工业时序数据结构分析器。只返回 JSON，不要 markdown。\n"
            "只能引用输入中真实存在的列名，不能发明日期、设备或关系。\n"
            "返回字段：summary,time_kind(instant|ordinal|interval),time_column,sequence_column,"
            "valid_from_column,valid_to_column,entity_column,observation_id_column,filter_columns,"
            "measurement_columns,dimensions,ontology_classes,relations,warnings。\n"
            f"数据画像：{json.dumps(profile.deterministic_profile, ensure_ascii=False, default=str)[:30000]}"
        )
        from app.services.llm_service import _call_llm, _parse_response
        raw = _call_llm(**kwargs, messages=[
            {"role": "system", "content": "严格依据数据画像分析工业时序表，不猜测不存在的字段。"},
            {"role": "user", "content": prompt},
        ], json_mode=False)
        suggestion = _validate_suggestion(raw, profile.deterministic_profile)
        profile.llm_suggestion = suggestion
        profile.status = "completed"
        profile.llm_used = True
        profile.model_name = kwargs["model"]
        profile.model_config_id = config.id
        profile.prompt_version = PROFILE_PROMPT_VERSION
        profile.response_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        profile.error = None
        db.commit()
    except Exception as exc:
        logger.warning("temporal profile failed: %s", exc)
        try:
            profile = db.query(TemporalDatasetProfile).filter(TemporalDatasetProfile.id == profile_id).first()
            if profile:
                profile.status = "failed"; profile.error = str(exc)[:2000]; db.commit()
        except Exception:
            db.rollback()
    finally:
        db.close()


def serialize_profile(profile: TemporalDatasetProfile) -> dict[str, Any]:
    return {
        "id": profile.id,
        "dataset_id": profile.dataset_id,
        "dataset_version_id": profile.dataset_version_id,
        "status": profile.status,
        "deterministic_profile": profile.deterministic_profile or {},
        "llm_suggestion": profile.llm_suggestion or {},
        "model_name": profile.model_name,
        "model_config_id": profile.model_config_id,
        "prompt_version": profile.prompt_version,
        "llm_used": bool(profile.llm_used),
        "response_hash": profile.response_hash,
        "error": profile.error,
        "created_at": profile.created_at.isoformat() if profile.created_at else None,
        "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
    }
