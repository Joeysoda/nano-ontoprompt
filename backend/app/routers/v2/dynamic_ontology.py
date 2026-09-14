"""Revision-aware ontology editor API."""
from __future__ import annotations

import hashlib
import json
import re
import time
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.deps import get_current_user, require_editor
from app.models.model_config import ModelConfig
from app.models.user import User
from app.models.v2.dynamic_ontology import OntologyChange
from app.models.v2.workbench_task import ModelInvocation
from app.services.v2.dynamic_ontology_service import (
    OntologyEditError,
    apply_change,
    apply_batch_changes,
    editor_payload,
    impact_for_change,
    serialize_change,
    validate_batch_changes,
)

router = APIRouter(dependencies=[Depends(get_current_user)])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _raise(exc: OntologyEditError) -> None:
    status = 404 if exc.code == "NOT_FOUND" else 409 if exc.code in {"REVISION_CONFLICT", "CHANGE_BLOCKED"} else 422
    raise HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc), **exc.details})


@router.get("/{ontology_id}/editor")
def get_editor(ontology_id: str, db: Session = Depends(get_db)):
    try:
        return editor_payload(db, ontology_id)
    except OntologyEditError as exc:
        _raise(exc)


@router.post("/{ontology_id}/changes/impact")
def get_change_impact(ontology_id: str, body: dict, db: Session = Depends(get_db)):
    try:
        return impact_for_change(db, ontology_id, body)
    except OntologyEditError as exc:
        _raise(exc)


@router.post("/{ontology_id}/changes")
def create_change(
    ontology_id: str,
    body: dict,
    db: Session = Depends(get_db),
    user: User = Depends(require_editor),
):
    try:
        return apply_change(db, ontology_id, body, user_id=user.id)
    except OntologyEditError as exc:
        _raise(exc)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail={"code": "CHANGE_FAILED", "message": str(exc)[:800]})


@router.post("/{ontology_id}/changes/batch/validate")
def validate_batch_change_set(
    ontology_id: str,
    body: dict,
    db: Session = Depends(get_db),
    _=Depends(require_editor),
):
    """Dry-run a selected set of model suggestions as one coherent change."""
    try:
        return validate_batch_changes(db, ontology_id, body)
    except OntologyEditError as exc:
        _raise(exc)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail={"code": "BATCH_INVALID", "message": str(exc)[:800]})


@router.post("/{ontology_id}/changes/batch")
def create_batch_change(
    ontology_id: str,
    body: dict,
    db: Session = Depends(get_db),
    user: User = Depends(require_editor),
):
    """Apply selected model suggestions atomically as one new revision."""
    try:
        return apply_batch_changes(db, ontology_id, body, user_id=user.id)
    except OntologyEditError as exc:
        _raise(exc)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail={"code": "BATCH_FAILED", "message": str(exc)[:800]})


@router.get("/{ontology_id}/changes")
def list_changes(
    ontology_id: str,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    rows = db.query(OntologyChange).filter(OntologyChange.ontology_id == ontology_id).order_by(OntologyChange.created_at.desc()).limit(limit).all()
    return {"ontology_id": ontology_id, "changes": [serialize_change(row) for row in rows], "count": len(rows)}


@router.post("/{ontology_id}/model-suggestions")
def model_suggestions(
    ontology_id: str,
    body: dict | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_editor),
):
    """Return optional, unapplied suggestions for the editor.

    Suggestions are deliberately returned as draft operations.  This endpoint
    never calls a cloud model for a private ontology and never writes ontology
    tables, even when a model is available.
    """
    body = body or {}
    from app.models.ontology import OntologyProject
    from app.models.v2.construction import ConstructionRun
    from app.models.v2.dataset import Dataset
    project = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    if not project:
        raise HTTPException(404, "本体不存在")
    configs = db.query(ModelConfig).order_by(ModelConfig.updated_at.desc()).all()
    # OntologyProject predates the dataset privacy field.  When the caller
    # does not send an explicit privacy value, derive it from the latest
    # construction run/dataset so opening the editor for a private build can
    # never accidentally route a suggestion to a cloud model.
    requested_privacy = str(body.get("privacy_level") or "").casefold()
    latest_run = db.query(ConstructionRun).filter(
        ConstructionRun.ontology_id == ontology_id
    ).order_by(ConstructionRun.updated_at.desc(), ConstructionRun.created_at.desc()).first()
    inferred_privacy = ""
    if re.search(r"private|私密|隐私", project.name or "", re.IGNORECASE):
        # Some legacy projects predate a persisted dataset privacy field but
        # were explicitly named as private.  Keep those projects on the safe
        # local-only path until their source metadata is migrated.
        inferred_privacy = "private"
    if latest_run:
        run_config = latest_run.config if isinstance(latest_run.config, dict) else {}
        run_privacy = str(run_config.get("privacy_level") or "").casefold()
        if run_privacy:
            inferred_privacy = run_privacy
        if inferred_privacy != "private" and latest_run.dataset_id:
            source_dataset = db.query(Dataset).filter(Dataset.id == latest_run.dataset_id).first()
            if source_dataset and str(source_dataset.privacy_level or "").casefold() == "private":
                inferred_privacy = "private"
    private = requested_privacy == "private" or inferred_privacy == "private"
    candidates = []
    for config in configs:
        capabilities = config.options.get("capabilities", []) if isinstance(config.options, dict) else []
        if isinstance(capabilities, str):
            capabilities = [capabilities]
        if not capabilities or "build" in capabilities:
            if private and str(config.provider).casefold() not in {"ollama", "local"}:
                continue
            candidates.append({"id": config.id, "name": config.name, "provider": config.provider, "models": config.models or []})
    response: dict = {
        "ontology_id": ontology_id,
        "privacy_level": "private" if private else "standard",
        "status": "draft_only",
        "message": "模型建议仅生成待填写操作，不会自动保存",
        "model_candidates": candidates,
        "suggestions": [],
        "requested": bool(body.get("request")),
    }
    if not body.get("request"):
        return response
    if not candidates:
        response.update({"status": "waiting_for_model", "message": "没有符合当前隐私范围的模型配置；建议仍未生成"})
        return response

    selected = next(
        (
            item for item in configs
            if item.id == candidates[0]["id"]
        ),
        None,
    )
    if selected is None:
        response.update({"status": "waiting_for_model", "message": "模型配置不可读取；建议仍未生成"})
        return response

    # The suggestion prompt contains only the schema currently visible in the
    # editor.  It does not include source rows, images, credentials, or model
    # hidden reasoning.  ModelGateway keeps the request on the configured
    # LiteLLM boundary just like construction and audit calls.
    editor = editor_payload(db, ontology_id)
    request_payload = {
        "ontology_id": ontology_id,
        "revision_id": editor.get("current_revision_id"),
        "instruction": str(body.get("instruction") or "检查本体结构并提出可选修改"),
        "entities": editor.get("entities", [])[:100],
        "relationships": editor.get("relationships", [])[:200],
        "logic_rules": editor.get("logic_rules", [])[:100],
    }
    request_text = json.dumps(request_payload, ensure_ascii=False, sort_keys=True, default=str)
    invocation = None
    started = time.monotonic()
    try:
        from app.services import encryption_service
        from app.services.model_gateway import ModelGateway

        model_name = str((selected.models or [selected.name])[0])
        invocation = ModelInvocation(
            route_alias=str(selected.name), provider=str(selected.provider), model_name=model_name,
            status="running", phase="ontology_mapping",
            request_ciphertext=encryption_service.encrypt(request_text),
            request_hash=hashlib.sha256(request_text.encode("utf-8")).hexdigest(),
            metadata_json={"purpose": "ontology_model_suggestion", "ontology_id": ontology_id, "revision_id": editor.get("current_revision_id"), "private": private},
        )
        db.add(invocation)
        db.commit()
        raw = ModelGateway(selected).call(
            [
                {"role": "system", "content": "你是本体审校助手，只返回 JSON，不自动发布任何修改。"},
                {"role": "user", "content": request_text + "\n请返回 {\"suggestions\":[{\"target_kind\":\"entity_type|property|relationship|logic_rule\",\"operation\":\"add|update|delete\",\"target_id\":\"可选\",\"payload\":{}}]}"},
            ],
            json_mode=True,
        )
        from app.services.llm_service import _parse_response
        parsed = _parse_response(raw) if isinstance(raw, str) else raw
        suggestions = []
        for item in (parsed.get("suggestions", []) if isinstance(parsed, dict) else []):
            if not isinstance(item, dict):
                continue
            kind = str(item.get("target_kind") or "")
            operation = str(item.get("operation") or "")
            if kind not in {"entity_type", "property", "relationship", "logic_rule"} or operation not in {"add", "update", "delete"}:
                continue
            suggestions.append({"target_kind": kind, "operation": operation, "target_id": item.get("target_id"), "payload": item.get("payload") or {}, "reason": str(item.get("reason") or "")[:500]})
            if len(suggestions) >= 20:
                break
        response["suggestions"] = suggestions
        response["status"] = "draft_only"
        response["message"] = "建议已生成，请逐项填写并单独保存；没有自动发布"
        response_payload = json.dumps({"suggestions": suggestions}, ensure_ascii=False, sort_keys=True)
        if invocation:
            invocation.status = "completed"
            invocation.response_ciphertext = encryption_service.encrypt(response_payload)
            invocation.response_hash = hashlib.sha256(response_payload.encode("utf-8")).hexdigest()
            invocation.duration_ms = int((time.monotonic() - started) * 1000)
            db.commit()
    except Exception as exc:
        db.rollback()
        response["status"] = "failed"
        response["message"] = f"模型建议未生成：{str(exc)[:400]}"
        if invocation is not None:
            try:
                invocation.status = "failed"
                invocation.error = str(exc)[:1000]
                invocation.duration_ms = int((time.monotonic() - started) * 1000)
                db.add(invocation)
                db.commit()
            except Exception:
                db.rollback()
    return response
