"""Schema migration planning and reversible pointer switching."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db, require_editor
from app.models.user import User
from app.models.v2.schema_migration import SchemaMigrationPlan, SchemaMigrationRun
from app.services.v2.schema_migration_service import SchemaMigrationError, _serialize_plan, _serialize_run, apply_plan, dry_run, reconcile_plan, revert_plan
from app.schemas.v2.semantic_core import MigrationRequest, SchemaMigrationPlanContract

router = APIRouter(dependencies=[Depends(get_current_user)])


def _raise(exc: SchemaMigrationError):
    status = 404 if exc.code == "NOT_FOUND" else 409 if exc.code == "REVISION_CONFLICT" else 422
    details = dict(exc.details or {})
    raise HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc), "next_action": details.pop("next_action", "检查迁移影响后重试"), "context_id": getattr(exc, "context_id", None) or uuid.uuid4().hex, **details})


@router.post("/ontologies/{ontology_id}/schema-migrations/dry-run", response_model=SchemaMigrationPlanContract)
def migration_dry_run(ontology_id: str, body: MigrationRequest, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    try:
        body_data = body.model_dump(exclude_none=True) if hasattr(body, "model_dump") else dict(body)
        return dry_run(db, ontology_id, body_data, user_id=user.id)
    except SchemaMigrationError as exc:
        _raise(exc)


@router.post("/ontologies/{ontology_id}/schema-migrations", response_model=SchemaMigrationPlanContract)
def migration_apply(ontology_id: str, body: MigrationRequest, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    try:
        body_data = body.model_dump(exclude_none=True) if hasattr(body, "model_dump") else dict(body)
        plan_id = str(body_data.get("plan_id") or "")
        if not plan_id:
            result = dry_run(db, ontology_id, body_data, user_id=user.id)
            plan_id = result["id"]
        return apply_plan(db, plan_id, user_id=user.id)
    except SchemaMigrationError as exc:
        _raise(exc)


@router.get("/schema-migrations/{run_id}", response_model=SchemaMigrationPlanContract)
def migration_get(run_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    run = db.query(SchemaMigrationRun).filter(SchemaMigrationRun.id == run_id).first()
    plan = db.query(SchemaMigrationPlan).filter(SchemaMigrationPlan.id == (run.plan_id if run else run_id)).first()
    if not plan:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "迁移计划不存在", "next_action": "检查 run_id 后重试", "context_id": uuid.uuid4().hex})
    from app.models.v2.schema_migration import SchemaMigrationInstruction
    rows = db.query(SchemaMigrationInstruction).filter(SchemaMigrationInstruction.plan_id == plan.id).order_by(SchemaMigrationInstruction.sequence_no.asc()).all()
    result = _serialize_plan(plan, rows)
    result["run"] = _serialize_run(run or db.query(SchemaMigrationRun).filter(SchemaMigrationRun.plan_id == plan.id).order_by(SchemaMigrationRun.created_at.desc()).first())
    return result


@router.post("/schema-migrations/{run_id}/reconcile", response_model=SchemaMigrationPlanContract)
def migration_reconcile(run_id: str, db: Session = Depends(get_db), _=Depends(require_editor)):
    try:
        return reconcile_plan(db, run_id)
    except SchemaMigrationError as exc:
        _raise(exc)


@router.post("/schema-migrations/{run_id}/revert", response_model=SchemaMigrationPlanContract)
def migration_revert(run_id: str, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    try:
        return revert_plan(db, run_id, user_id=user.id)
    except SchemaMigrationError as exc:
        _raise(exc)
