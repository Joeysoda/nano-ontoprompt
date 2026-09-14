from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.deps import get_db, get_current_user, require_editor
from app.models.entity import Entity
from app.schemas.entity import EntityCreate, EntityUpdate, EntityOut
import uuid

router = APIRouter()

@router.get("")
def list_entities(ontology_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    items = db.query(Entity).filter(Entity.ontology_id == ontology_id).all()
    return {"data": [EntityOut.model_validate(e).model_dump() for e in items]}

@router.post("", status_code=201)
def create_entity(ontology_id: str, body: EntityCreate, db: Session = Depends(get_db), user=Depends(require_editor)):
    from app.services.v2.dynamic_ontology_service import OntologyEditError, apply_change
    try:
        result = apply_change(db, ontology_id, {"target_kind": "entity_type", "operation": "add", "payload": {k: v for k, v in body.model_dump().items() if v is not None}}, user_id=user.id)
    except OntologyEditError as exc:
        raise HTTPException(409 if exc.code in {"CHANGE_BLOCKED", "REVISION_CONFLICT"} else 422, {"code": exc.code, "message": str(exc), **exc.details})
    e = db.query(Entity).filter(Entity.id == result["change"]["target_id"], Entity.ontology_id == ontology_id).first()
    return {"data": EntityOut.model_validate(e).model_dump()}

@router.get("/{entity_id}")
def get_entity(ontology_id: str, entity_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    e = db.query(Entity).filter(Entity.id == entity_id, Entity.ontology_id == ontology_id).first()
    if not e:
        raise HTTPException(404, "Not found")
    return {"data": EntityOut.model_validate(e).model_dump()}

@router.put("/{entity_id}")
def update_entity(ontology_id: str, entity_id: str, body: EntityUpdate, db: Session = Depends(get_db), user=Depends(require_editor)):
    from app.services.v2.dynamic_ontology_service import OntologyEditError, apply_change
    try:
        apply_change(db, ontology_id, {"target_kind": "entity_type", "operation": "update", "target_id": entity_id, "payload": body.model_dump(exclude_none=True)}, user_id=user.id)
    except OntologyEditError as exc:
        raise HTTPException(404 if exc.code == "NOT_FOUND" else 409 if exc.code in {"CHANGE_BLOCKED", "REVISION_CONFLICT"} else 422, {"code": exc.code, "message": str(exc), **exc.details})
    e = db.query(Entity).filter(Entity.id == entity_id, Entity.ontology_id == ontology_id).first()
    return {"data": EntityOut.model_validate(e).model_dump()}

@router.delete("/{entity_id}", status_code=204)
def delete_entity(ontology_id: str, entity_id: str, db: Session = Depends(get_db), user=Depends(require_editor)):
    from app.services.v2.dynamic_ontology_service import OntologyEditError, apply_change
    try:
        apply_change(db, ontology_id, {"target_kind": "entity_type", "operation": "delete", "target_id": entity_id}, user_id=user.id)
    except OntologyEditError as exc:
        raise HTTPException(404 if exc.code == "NOT_FOUND" else 409, {"code": exc.code, "message": str(exc), **exc.details})

@router.get("/{entity_id}/instances")
def list_entity_instances(
    ontology_id: str,
    entity_id: str,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    """概念实体下挂的行级实例数据（Pipeline Mapping 与简易 LLM 提取共用同一张表）"""
    from app.models.entity_instance import EntityInstance

    entity = db.query(Entity).filter(
        Entity.id == entity_id, Entity.ontology_id == ontology_id
    ).first()
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")

    instances = db.query(EntityInstance).filter(
        EntityInstance.entity_id == entity_id, EntityInstance.ontology_id == ontology_id
    ).order_by(EntityInstance.created_at).all()

    return {"data": [
        {"id": i.id, "row_identity": i.row_identity, "row_data": i.row_data, "created_at": i.created_at.isoformat()}
        for i in instances
    ]}
