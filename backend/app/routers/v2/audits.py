"""Structured audit task API used by the model/review workbench."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.deps import get_current_user, require_editor
from app.models.audit_task import AuditTask
from app.models.ontology import OntologyProject
from app.services.v2.audit_runner import queue_local_audit, serialize_audit_task
from app.services.v2.revision_service import create_revision, serialize_revision, snapshot_ontology

router = APIRouter(dependencies=[Depends(get_current_user)])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class RepairDraftRequest(BaseModel):
    finding_indices: list[int] = Field(default_factory=list)
    note: str = ""


class RepairConfirmRequest(BaseModel):
    note: str = ""


class AuditStartRequest(BaseModel):
    ontology_id: str
    revision_id: str | None = None


@router.post("")
def start_audit(body: AuditStartRequest, db: Session = Depends(get_db), _=Depends(require_editor)):
    """Queue the configured local audit for an already published ontology.

    Builds enqueue this automatically.  This explicit endpoint is for a
    manual re-check after a user has changed mappings or restored a revision;
    it deliberately has no cloud-model selector.
    """
    project = db.query(OntologyProject).filter(OntologyProject.id == body.ontology_id).first()
    if not project:
        raise HTTPException(404, "本体不存在")
    task = queue_local_audit(
        db,
        ontology_id=project.id,
        revision_id=body.revision_id or project.current_revision_id,
        construction_run_id=None,
    )
    return serialize_audit_task(task)


@router.get("")
def list_audits(ontology_id: str | None = None, db: Session = Depends(get_db)):
    query = db.query(AuditTask).order_by(AuditTask.created_at.desc())
    if ontology_id:
        query = query.filter(AuditTask.ontology_id == ontology_id)
    rows = query.limit(100).all()
    return {"tasks": [serialize_audit_task(row) for row in rows], "count": len(rows)}


@router.get("/{task_id}")
def get_audit(task_id: str, db: Session = Depends(get_db)):
    task = db.query(AuditTask).filter(AuditTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "审查任务不存在")
    return serialize_audit_task(task)


@router.post("/{task_id}/cancel")
def cancel_audit(task_id: str, db: Session = Depends(get_db), _=Depends(require_editor)):
    task = db.query(AuditTask).filter(AuditTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "审查任务不存在")
    if task.status in {"queued", "running"}:
        task.cancel_requested = True
        task.status = "cancel_requested"
        db.commit()
    return serialize_audit_task(task)


@router.post("/{task_id}/retry")
def retry_audit(task_id: str, db: Session = Depends(get_db), _=Depends(require_editor)):
    task = db.query(AuditTask).filter(AuditTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "审查任务不存在")
    retried = queue_local_audit(db, ontology_id=task.ontology_id, revision_id=task.revision_id, construction_run_id=task.construction_run_id)
    return serialize_audit_task(retried)


@router.post("/{task_id}/findings/{finding_index}/ignore")
def ignore_finding(task_id: str, finding_index: int, note: str = "", db: Session = Depends(get_db), _=Depends(require_editor)):
    task = db.query(AuditTask).filter(AuditTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "审查任务不存在")
    findings = list(task.findings or [])
    if finding_index < 0 or finding_index >= len(findings):
        raise HTTPException(400, "审查发现编号不存在")
    findings[finding_index] = {**findings[finding_index], "disposition": "ignored", "disposition_note": note}
    task.findings = findings
    db.commit()
    return serialize_audit_task(task)


@router.post("/{task_id}/repair-draft")
def create_repair_draft(task_id: str, body: RepairDraftRequest, db: Session = Depends(get_db), _=Depends(require_editor)):
    task = db.query(AuditTask).filter(AuditTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "审查任务不存在")
    findings = list(task.findings or [])
    selected: list[dict[str, Any]] = []
    for index in body.finding_indices:
        if 0 <= index < len(findings) and findings[index].get("disposition") != "ignored":
            selected.append({"index": index, "finding": findings[index]})
    if not selected:
        raise HTTPException(400, "请至少选择一个未忽略的审查发现")
    draft = {
        "id": str(uuid.uuid4()), "status": "draft", "source_task_id": task.id,
        "revision_id": task.revision_id, "note": body.note,
        "engine": "m3" if task.model_id and str(task.model_name).lower() != "qwen3.5:0.8b" else "local_or_manual",
        "changes": [{"action": "review", "category": item["finding"].get("category", "other"), "title": item["finding"].get("title", ""), "affected_items": item["finding"].get("affected_items", [])} for item in selected],
    }
    task.repair_draft = draft
    db.commit()
    return {"task_id": task.id, "draft": draft}


@router.post("/{task_id}/repair-draft/confirm")
def confirm_repair_draft(task_id: str, body: RepairConfirmRequest | None = None, db: Session = Depends(get_db), _=Depends(require_editor)):
    task = db.query(AuditTask).filter(AuditTask.id == task_id).first()
    if not task or not task.repair_draft:
        raise HTTPException(404, "修订草案不存在")
    project = db.query(OntologyProject).filter(OntologyProject.id == task.ontology_id).first()
    if not project:
        raise HTTPException(404, "本体不存在")
    snapshot = snapshot_ontology(db, task.ontology_id)
    snapshot["repair_actions"] = task.repair_draft.get("changes", [])
    revision = create_revision(db, task.ontology_id, snapshot=snapshot, parent_revision_id=task.revision_id, summary={"repair_from_audit": task.id, "note": (body.note if body else ""), "change_count": len(task.repair_draft.get("changes", []))})
    task.repair_draft = {**task.repair_draft, "status": "confirmed", "revision_id": revision.id}
    db.commit()
    queue_local_audit(db, ontology_id=task.ontology_id, revision_id=revision.id, construction_run_id=task.construction_run_id)
    return {"revision": serialize_revision(revision), "audit_task_id": task.id}
