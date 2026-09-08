"""Queue the local post-build audit without coupling it to Celery availability."""
from __future__ import annotations

import threading
import os
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models.audit_task import AuditTask
from app.models.model_config import ModelConfig

LOCAL_AUDIT_MODEL = "qwen3.5:0.8b"


def queue_local_audit(db: Session, *, ontology_id: str, revision_id: str | None, construction_run_id: str | None) -> AuditTask:
    config = next((item for item in db.query(ModelConfig).filter(ModelConfig.provider.in_(["ollama", "local"])).order_by(ModelConfig.updated_at.desc()).all() if LOCAL_AUDIT_MODEL in [str(name) for name in (item.models or [])]), None)
    if not config:
        task = AuditTask(id=str(uuid.uuid4()), ontology_id=ontology_id, model_id=None, model_name=LOCAL_AUDIT_MODEL, status="waiting_for_model", revision_id=revision_id, construction_run_id=construction_run_id, progress={"stage": "waiting_for_model", "pct": 0}, error="未找到本地模型槽；可在模型与审查中配置 Ollama qwen3.5:0.8b")
        db.add(task)
        db.commit()
        db.refresh(task)
        return task
    task = AuditTask(id=str(uuid.uuid4()), ontology_id=ontology_id, model_id=config.id, model_name=LOCAL_AUDIT_MODEL, status="queued", revision_id=revision_id, construction_run_id=construction_run_id, progress={"stage": "queued", "pct": 0}, react_trace=[])
    db.add(task)
    db.commit()
    db.refresh(task)
    task_id = task.id

    if os.getenv("CELERY_ENABLED", "").lower() in {"1", "true", "yes"}:
        try:
            from app.tasks.audit import run_audit
            run_audit.delay(task_id)
            return task
        except Exception as exc:
            task.status = "failed"
            task.error = f"Celery 任务派发失败：{str(exc)[:500]}"
            db.commit()
            return task

    def worker() -> None:
        try:
            from app.tasks.audit import run_audit
            run_audit(task_id)
        except Exception as exc:
            worker_db = None
            try:
                from app.database import SessionLocal
                worker_db = SessionLocal()
                current = worker_db.query(AuditTask).filter(AuditTask.id == task_id).first()
                if current:
                    current.status = "failed"
                    current.error = str(exc)[:2000]
                    worker_db.commit()
            finally:
                if worker_db:
                    worker_db.close()

    threading.Thread(target=worker, name=f"ontology-audit-{task_id[:8]}", daemon=True).start()
    return task


def serialize_audit_task(task: AuditTask) -> dict[str, Any]:
    # The model may return hidden reasoning fields alongside tool calls.  The
    # workbench exposes only the auditable tool name, arguments and observed
    # result; hidden chain-of-thought is neither persisted in the response nor
    # shown in the review UI.
    trace = [
        {key: value for key, value in item.items() if key not in {"thought", "reasoning_content"}}
        for item in (task.react_trace or [])
        if isinstance(item, dict)
    ]
    return {
        "id": task.id, "task_id": task.id, "ontology_id": task.ontology_id,
        "model_id": task.model_id, "model_name": task.model_name, "status": task.status,
        "revision_id": task.revision_id, "construction_run_id": task.construction_run_id,
        "progress": task.progress or {}, "error": task.error,
        "findings": task.findings or [], "react_trace": trace,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
    }
