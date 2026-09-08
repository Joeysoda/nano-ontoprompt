"""Deterministic profiling and strict MiniMax M3 schema analysis."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from app.models.model_config import ModelConfig
from app.models.v2.construction import EvidenceRef
from app.models.v2.construction_draft import ConstructionDraft  # noqa: F401 - register ModelInvocation FK target
from app.models.v2.dataset import Dataset, DatasetVersion
from app.models.v2.temporal_profile import TemporalDatasetProfile
from app.models.v2.workbench_task import ModelInvocation
from app.services import encryption_service
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
    numeric = 0
    for value in text_values:
        try:
            float(value)
            numeric += 1
        except (TypeError, ValueError):
            continue
    if text_values and numeric / len(text_values) >= 0.8:
        return "number"
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
        physical_type = _physical_type(values)
        numeric_values: list[float] = []
        if physical_type == "number":
            for value in nonempty:
                try:
                    numeric_values.append(float(value))
                except (TypeError, ValueError):
                    pass
        column_profiles.append({
            "name": column,
            "type": physical_type,
            "nullable": len(nonempty) != len(values),
            "null_count": len(values) - len(nonempty),
            "unique_count": len(counts),
            "sample": [str(value)[:120] for value in nonempty[:5]],
            "min": min(numeric_values) if numeric_values else None,
            "max": max(numeric_values) if numeric_values else None,
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
    return next((config for config in configs if "MiniMax-M3" in [str(item) for item in (config.models or [])] and str(config.provider or "").lower() not in {"ollama", "local"}), None)


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
    # A model can classify the semantics correctly while omitting one of the
    # explicit column fields.  Fill only from deterministic, existing
    # candidates; never invent a date or a column name.  The correction is
    # retained as a warning for human review.
    time_candidates = [str(item) for item in profile.get("time_candidates", []) if str(item) in columns]
    if suggestion.time_kind == "instant" and not suggestion.time_column:
        datetime_candidates = [item["name"] for item in profile.get("columns", []) if item.get("type") == "datetime" and item.get("name") in columns]
        if datetime_candidates:
            suggestion.time_column = datetime_candidates[0]
            suggestion.warnings.append("M3 未填写时间列，已采用确定性 datetime 候选")
        else:
            raise ValueError("M3 选择 Instant 但没有时间列")
    if suggestion.time_kind == "ordinal" and not suggestion.sequence_column:
        sequence_candidates = [item for item in time_candidates if re.search(r"cycle|step|seq|sequence|elapsed|time_s", item, re.I)]
        if sequence_candidates:
            suggestion.sequence_column = sequence_candidates[0]
            suggestion.warnings.append("M3 未填写顺序列，已采用确定性序列候选")
        else:
            raise ValueError("M3 选择 Ordinal 但没有顺序列")
    if suggestion.time_kind == "interval" and not suggestion.valid_from_column:
        start_candidates = [item for item in time_candidates if re.search(r"valid[_ ]?from|start|begin|from", item, re.I)]
        end_candidates = [item for item in time_candidates if re.search(r"valid[_ ]?to|end|finish|to", item, re.I)]
        if start_candidates:
            suggestion.valid_from_column = start_candidates[0]
            if not suggestion.valid_to_column and end_candidates:
                suggestion.valid_to_column = end_candidates[0]
            suggestion.warnings.append("M3 未填写区间起点，已采用确定性区间候选")
        else:
            raise ValueError("M3 选择 Interval 但没有开始列")
    if suggestion.time_kind == "interval" and not suggestion.valid_to_column:
        raise ValueError("M3 选择 Interval 但没有结束列")
    return suggestion.model_dump()


def run_profile(profile_id: str) -> None:
    from app.database import SessionLocal

    db = SessionLocal()
    invocation: ModelInvocation | None = None
    started = time.monotonic()
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
        request_payload = json.dumps({
            "profile_id": profile.id,
            "dataset_id": profile.dataset_id,
            "dataset_version_id": profile.dataset_version_id,
            "prompt": prompt,
        }, ensure_ascii=False, sort_keys=True, default=str)
        invocation = ModelInvocation(
            route_alias="MiniMax-M3",
            provider=str(config.provider or "cloud"),
            model_name=str(kwargs["model"]),
            status="running",
            request_ciphertext=encryption_service.encrypt(request_payload),
            request_hash=hashlib.sha256(request_payload.encode("utf-8")).hexdigest(),
            metadata_json={
                "purpose": "temporal_profile",
                "profile_id": profile.id,
                "dataset_id": profile.dataset_id,
                "dataset_version_id": profile.dataset_version_id,
                "sent_fields": ["deterministic_profile"],
            },
        )
        db.add(invocation)
        db.commit()
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
        invocation.status = "completed"
        invocation.response_ciphertext = encryption_service.encrypt(raw)
        invocation.response_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        invocation.duration_ms = int((time.monotonic() - started) * 1000)
        invocation.completed_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as exc:
        logger.warning("temporal profile failed: %s", exc)
        try:
            db.rollback()
            if invocation is not None:
                failed = db.query(ModelInvocation).filter(ModelInvocation.id == invocation.id).first()
                if failed:
                    failed.status = "failed"
                    failed.error = str(exc)[:1000]
                    failed.duration_ms = int((time.monotonic() - started) * 1000)
                    failed.completed_at = datetime.now(timezone.utc)
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
