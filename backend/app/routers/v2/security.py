"""Object/property security policy API."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models.ontology import OntologyProject
from app.models.user import User
from app.models.v2.security import OntologySecurityPolicy
from app.services.v2.authorization_service import AuthorizationContext, AuthorizationError, redact_record, resolve_projection
from app.services.v2.semantic_core_service import serialize_policy_shape
from app.schemas.v2.semantic_core import PolicyEvaluateRequest, SecurityPolicyPatch, SecurityPolicyRequest

router = APIRouter(dependencies=[Depends(get_current_user)])


def _http_error(status: int, code: str, message: str, next_action: str = "检查请求参数和权限后重试") -> HTTPException:
    return HTTPException(status_code=status, detail={
        "code": code,
        "message": message,
        "next_action": next_action,
        "context_id": uuid.uuid4().hex,
    })


def _can_manage(project: OntologyProject | None, user: User) -> bool:
    return bool(isinstance(user, User) and project and (user.role == "admin" or str(project.created_by) == str(user.id)))


def _project_or_404(db: Session, ontology_id: str) -> OntologyProject:
    project = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    if not project:
        raise _http_error(404, "NOT_FOUND", "本体不存在", "检查 ontology_id")
    return project


def _validate(body: dict) -> None:
    if str(body.get("subject_kind") or "user") not in {"user", "role"}:
        raise _http_error(422, "INVALID_SUBJECT_KIND", "subject_kind 必须是 user 或 role")
    if str(body.get("effect") or "allow") not in {"allow", "deny"}:
        raise _http_error(422, "INVALID_EFFECT", "effect 必须是 allow 或 deny")
    if str(body.get("scope_kind") or "ontology") not in {"ontology", "object_type", "property", "link"}:
        raise _http_error(422, "INVALID_SCOPE", "scope_kind 不支持")
    scope_kind = str(body.get("scope_kind") or "ontology")
    if scope_kind != "ontology" and not str(body.get("scope_id") or "").strip():
        raise _http_error(422, "INVALID_SCOPE", "对象类型、属性或关系策略必须指定 scope_id")
    conditions = body.get("conditions", body.get("conditions_json", []))
    if not isinstance(conditions, (list, dict)):
        raise _http_error(422, "INVALID_CONDITIONS", "conditions 必须是结构化 JSON 条件")
    condition_items = conditions.get("all", []) if isinstance(conditions, dict) and isinstance(conditions.get("all"), list) else (conditions if isinstance(conditions, list) else [conditions])
    for item in condition_items:
        if not isinstance(item, dict) or not item.get("field") and not item.get("property"):
            raise _http_error(422, "INVALID_CONDITIONS", "每条条件必须指定 field")


@router.get("/{ontology_id}/security/policies")
def list_policies(ontology_id: str, limit: int = Query(200, ge=1, le=500), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    project = _project_or_404(db, ontology_id)
    if not _can_manage(project, user):
        raise _http_error(404, "NOT_FOUND", "本体不存在", "检查 ontology_id")
    rows = db.query(OntologySecurityPolicy).filter(OntologySecurityPolicy.ontology_id == ontology_id).order_by(OntologySecurityPolicy.priority.asc(), OntologySecurityPolicy.created_at.asc()).limit(limit).all()
    return {"ontology_id": ontology_id, "policies": [serialize_policy_shape(row) for row in rows], "count": len(rows)}


@router.post("/{ontology_id}/security/policies")
def create_policy(ontology_id: str, body: SecurityPolicyRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    body = body.model_dump(exclude_none=True) if hasattr(body, "model_dump") else dict(body)
    project = _project_or_404(db, ontology_id)
    if not _can_manage(project, user):
        raise _http_error(403, "POLICY_ADMIN_REQUIRED", "只有管理员或本体所有者可以修改数据权限", "请本体所有者或管理员操作")
    _validate(body)
    row = OntologySecurityPolicy(
        ontology_id=ontology_id,
        revision_id=project.current_revision_id,
        name=str(body.get("name") or "未命名策略"),
        subject_kind=str(body.get("subject_kind") or "user"), subject_id=str(body.get("subject_id") or ""),
        effect=str(body.get("effect") or "allow"), scope_kind=str(body.get("scope_kind") or "ontology"), scope_id=body.get("scope_id"),
        conditions_json=body.get("conditions", []), field_allowlist_json=list(body.get("field_allowlist", []) or []),
        enabled=bool(body.get("enabled", True)), priority=int(body.get("priority", 100)), created_by=user.id, note=body.get("note"),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"policy": serialize_policy_shape(row)}


@router.patch("/{ontology_id}/security/policies/{policy_id}")
def update_policy(ontology_id: str, policy_id: str, body: SecurityPolicyPatch, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    body = body.model_dump(exclude_none=True) if hasattr(body, "model_dump") else dict(body)
    project = _project_or_404(db, ontology_id)
    if not _can_manage(project, user):
        raise _http_error(403, "POLICY_ADMIN_REQUIRED", "只有管理员或本体所有者可以修改数据权限", "请本体所有者或管理员操作")
    row = db.query(OntologySecurityPolicy).filter(OntologySecurityPolicy.ontology_id == ontology_id, OntologySecurityPolicy.id == policy_id).first()
    if not row:
        raise _http_error(404, "NOT_FOUND", "权限策略不存在", "检查 policy_id")
    merged = {
        "subject_kind": body.get("subject_kind", row.subject_kind),
        "effect": body.get("effect", row.effect),
        "scope_kind": body.get("scope_kind", row.scope_kind),
        "scope_id": body.get("scope_id", row.scope_id),
        "conditions": body.get("conditions", row.conditions_json or []),
    }
    _validate(merged)
    for field, key in (("name", "name"), ("subject_kind", "subject_kind"), ("subject_id", "subject_id"), ("effect", "effect"), ("scope_kind", "scope_kind"), ("scope_id", "scope_id"), ("enabled", "enabled"), ("priority", "priority"), ("note", "note")):
        if key in body:
            setattr(row, field, body[key])
    if "conditions" in body or "conditions_json" in body:
        row.conditions_json = body.get("conditions", body.get("conditions_json"))
    if "field_allowlist" in body or "field_allowlist_json" in body:
        row.field_allowlist_json = list(body.get("field_allowlist", body.get("field_allowlist_json")) or [])
    db.commit()
    db.refresh(row)
    return {"policy": serialize_policy_shape(row)}


@router.delete("/{ontology_id}/security/policies/{policy_id}")
def delete_policy(ontology_id: str, policy_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    project = _project_or_404(db, ontology_id)
    if not _can_manage(project, user):
        raise _http_error(403, "POLICY_ADMIN_REQUIRED", "只有管理员或本体所有者可以修改数据权限", "请本体所有者或管理员操作")
    row = db.query(OntologySecurityPolicy).filter(OntologySecurityPolicy.ontology_id == ontology_id, OntologySecurityPolicy.id == policy_id).first()
    if not row:
        raise _http_error(404, "NOT_FOUND", "权限策略不存在", "检查 policy_id")
    db.delete(row)
    db.commit()
    return {"deleted": True, "policy_id": policy_id}


@router.post("/{ontology_id}/security/evaluate")
def evaluate_policy(ontology_id: str, body: PolicyEvaluateRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    body = body.model_dump(exclude_none=True) if hasattr(body, "model_dump") else dict(body)
    project = _project_or_404(db, ontology_id)
    if not _can_manage(project, user):
        raise _http_error(404, "NOT_FOUND", "本体不存在", "检查 ontology_id")
    context = AuthorizationContext(
        ontology_id=ontology_id, principal_id=str(body.get("principal_id") or user.id), principal_role=str(body.get("principal_role") or user.role or ""),
        metadata_revision_id=body.get("metadata_revision_id"), requested_fields=tuple(str(item) for item in (body.get("requested_fields") or [])), object_type_id=body.get("object_type_id"),
    )
    records = [item for item in (body.get("records") or []) if isinstance(item, dict)]
    try:
        projection = resolve_projection(db, context, records=records)
    except AuthorizationError as exc:
        raise HTTPException(404 if exc.code == "NOT_FOUND" else 422, detail={"code": exc.code, "message": str(exc), "next_action": "检查主体、策略和本体修订", "context_id": exc.context_id}) from exc
    redacted = [redact_record(projection, item) for item in records]
    return {"projection": {"allowed": projection.allowed, "visible_object_ids": sorted(projection.visible_object_ids) if projection.visible_object_ids is not None else None, "allowed_fields": sorted(projection.allowed_fields) if projection.allowed_fields is not None else None, "allowed_fields_by_object": {key: sorted(value) if value is not None else None for key, value in projection.allowed_fields_by_object.items()}, "denied_fields": sorted(projection.denied_fields), "denied_fields_by_object": {key: sorted(value) for key, value in projection.denied_fields_by_object.items()}, "policy_ids": projection.policy_ids, "permission_digest": projection.permission_digest, "reason": projection.reason}, "records": [item for item in redacted if item is not None]}
