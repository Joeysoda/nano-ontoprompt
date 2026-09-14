from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.deps import get_db, get_current_user, require_editor
from app.models.logic import LogicRule
from app.schemas.logic import LogicRuleCreate, LogicRuleUpdate, LogicRuleOut
import uuid

router = APIRouter()

@router.get("")
def list_logic(ontology_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    items = db.query(LogicRule).filter(LogicRule.ontology_id == ontology_id).all()
    return {"data": [LogicRuleOut.model_validate(r).model_dump() for r in items]}

@router.post("", status_code=201)
def create_logic(ontology_id: str, body: LogicRuleCreate, db: Session = Depends(get_db), user=Depends(require_editor)):
    from app.services.v2.dynamic_ontology_service import OntologyEditError, apply_change
    try:
        result = apply_change(db, ontology_id, {"target_kind": "logic_rule", "operation": "add", "payload": {k: v for k, v in body.model_dump().items() if v is not None}}, user_id=user.id)
    except OntologyEditError as exc:
        raise HTTPException(422, {"code": exc.code, "message": str(exc), **exc.details})
    r = db.query(LogicRule).filter(LogicRule.id == result["change"]["target_id"], LogicRule.ontology_id == ontology_id).first()
    return {"data": LogicRuleOut.model_validate(r).model_dump()}

@router.get("/{logic_id}")
def get_logic(ontology_id: str, logic_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    r = db.query(LogicRule).filter(LogicRule.id == logic_id, LogicRule.ontology_id == ontology_id).first()
    if not r:
        raise HTTPException(404, "Not found")
    return {"data": LogicRuleOut.model_validate(r).model_dump()}

@router.put("/{logic_id}")
def update_logic(ontology_id: str, logic_id: str, body: LogicRuleUpdate, db: Session = Depends(get_db), user=Depends(require_editor)):
    from app.services.v2.dynamic_ontology_service import OntologyEditError, apply_change
    try:
        apply_change(db, ontology_id, {"target_kind": "logic_rule", "operation": "update", "target_id": logic_id, "payload": body.model_dump(exclude_none=True)}, user_id=user.id)
    except OntologyEditError as exc:
        raise HTTPException(404 if exc.code == "NOT_FOUND" else 422, {"code": exc.code, "message": str(exc), **exc.details})
    r = db.query(LogicRule).filter(LogicRule.id == logic_id, LogicRule.ontology_id == ontology_id).first()
    return {"data": LogicRuleOut.model_validate(r).model_dump()}

@router.delete("/{logic_id}", status_code=204)
def delete_logic(ontology_id: str, logic_id: str, db: Session = Depends(get_db), user=Depends(require_editor)):
    from app.services.v2.dynamic_ontology_service import OntologyEditError, apply_change
    try:
        apply_change(db, ontology_id, {"target_kind": "logic_rule", "operation": "delete", "target_id": logic_id}, user_id=user.id)
    except OntologyEditError as exc:
        raise HTTPException(404 if exc.code == "NOT_FOUND" else 409, {"code": exc.code, "message": str(exc), **exc.details})


@router.post("/{logic_id}/toggle")
def toggle_logic(ontology_id: str, logic_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Human Review: 启用/禁用规则"""
    r = db.query(LogicRule).filter(LogicRule.id == logic_id, LogicRule.ontology_id == ontology_id).first()
    if not r:
        raise HTTPException(404, "Not found")
    r.enabled = not getattr(r, 'enabled', True)
    try:
        from app.models.v2.logic import OntologyLogicRule
        v2 = db.query(OntologyLogicRule).filter(
            OntologyLogicRule.ontology_id == ontology_id,
            OntologyLogicRule.name == r.name_cn,
        ).first()
        if v2:
            v2.enabled = r.enabled
            if not r.enabled and v2.status != "published":
                v2.status = "disabled"
    except Exception:
        pass
    db.commit()
    return {"enabled": r.enabled}


@router.post("/publish")
def publish_logic_rules(ontology_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Human Review: 发布所有草稿规则"""
    rules = db.query(LogicRule).filter(
        LogicRule.ontology_id == ontology_id,
        LogicRule.status != 'published',
        LogicRule.enabled == True,  # noqa: E712
    ).all()
    for r in rules:
        r.status = 'published'
    try:
        from app.models.v2.logic import OntologyLogicRule
        v2_rules = db.query(OntologyLogicRule).filter(
            OntologyLogicRule.ontology_id == ontology_id,
            OntologyLogicRule.status != "published",
            OntologyLogicRule.enabled == True,  # noqa: E712
        ).all()
        for r in v2_rules:
            r.status = "published"
    except Exception:
        pass
    db.commit()
    return {"published": len(rules)}
