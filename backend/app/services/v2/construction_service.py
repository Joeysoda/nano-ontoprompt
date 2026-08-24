"""Persistence helpers for reproducible ontology construction runs."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.v2.construction import ConstructionRun, EvidenceRef


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def serialize_run(run: ConstructionRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "run_id": run.id,
        "ontology_id": run.ontology_id,
        "dataset_id": run.dataset_id,
        "mode": run.mode,
        "status": run.status,
        "model_name": run.model_name,
        "config": run.config or {},
        "progress": run.progress or {},
        "metrics": run.metrics or {},
        "artifact_uri": run.artifact_uri,
        "error": run.error,
        "created_at": _iso(run.created_at),
        "started_at": _iso(run.started_at),
        "completed_at": _iso(run.completed_at),
        "updated_at": _iso(run.updated_at),
    }


def create_run(
    db: Session,
    *,
    ontology_id: str,
    mode: str,
    dataset_id: str | None = None,
    model_name: str | None = None,
    config: dict[str, Any] | None = None,
) -> ConstructionRun:
    run = ConstructionRun(
        ontology_id=ontology_id,
        dataset_id=dataset_id,
        mode=mode,
        status="queued",
        model_name=model_name,
        config=config or {},
        progress={"completed": 0, "total": 0},
        metrics={},
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def update_run(
    db: Session,
    run: ConstructionRun,
    *,
    status: str | None = None,
    progress: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
    artifact_uri: str | None = None,
    error: str | None = None,
) -> ConstructionRun:
    if status is not None:
        run.status = status
        now = datetime.now(timezone.utc)
        if status == "running" and run.started_at is None:
            run.started_at = now
        if status in {"completed", "failed", "cancelled"}:
            run.completed_at = now
    if progress is not None:
        run.progress = progress
    if metrics is not None:
        run.metrics = metrics
    if artifact_uri is not None:
        run.artifact_uri = artifact_uri
    if error is not None:
        run.error = error
    run.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(run)
    return run


def add_evidence(
    db: Session,
    *,
    run: ConstructionRun,
    assertion_id: str,
    assertion_kind: str = "node",
    extractor: str = "rule",
    source_file: str | None = None,
    source_row: int | None = None,
    source_media_id: str | None = None,
    source_dataset_version: str | None = None,
    model_name: str | None = None,
    confidence: float | None = None,
    confidence_method: str = "not_calibrated",
    evidence_text: str | None = None,
    content: Any = None,
) -> EvidenceRef:
    digest_input = content if content is not None else evidence_text or assertion_id
    encoded = json.dumps(digest_input, sort_keys=True, default=str).encode()
    ref = EvidenceRef(
        construction_run_id=run.id,
        ontology_id=run.ontology_id,
        assertion_id=assertion_id,
        assertion_kind=assertion_kind,
        source_file=source_file,
        source_row_id=str(source_row) if source_row is not None else None,
        source_media_id=source_media_id,
        source_version=source_dataset_version,
        extractor=extractor,
        model_name=model_name,
        confidence=confidence,
        confidence_method=confidence_method,
        evidence_text=evidence_text,
        content_hash=hashlib.sha256(encoded).hexdigest(),
    )
    db.add(ref)
    db.commit()
    db.refresh(ref)
    return ref


def serialize_evidence(ref: EvidenceRef) -> dict[str, Any]:
    return {
        "id": ref.id,
        "construction_run_id": ref.construction_run_id,
        "ontology_id": ref.ontology_id,
        "assertion_id": ref.assertion_id,
        "assertion_kind": ref.assertion_kind,
        "source_file": ref.source_file,
        "source_row": ref.source_row_id,
        "source_media_id": ref.source_media_id,
        "source_dataset_version": ref.source_version,
        "extractor": ref.extractor,
        "model_name": ref.model_name,
        "confidence": ref.confidence,
        "confidence_method": ref.confidence_method,
        "evidence_text": ref.evidence_text,
        "content_hash": ref.content_hash,
        "created_at": _iso(ref.created_at),
    }
