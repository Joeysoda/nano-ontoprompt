from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db, require_editor
from app.models.v2.logic import OntologyStateMachine
from app.schemas.v2.process_model import CreateProcessModel, ProcessDefinition, PublishProcessModel, UpdateProcessModel
from app.services.v2.object_query.authorization import require_ontology_access
from app.services.v2.object_query.metadata import load_sql_metadata
from app.services.v2.process_models import ProcessError, publish, validate_metadata

router = APIRouter()


def access(db, ontology_id, user):
    try: return require_ontology_access(db, ontology_id, str(user.id), user.role)
    except Exception as exc: raise HTTPException(404, "Ontology not found or inaccessible") from exc


def row(db, ontology_id, machine_id):
    value = db.get(OntologyStateMachine, machine_id)
    if value is None or value.ontology_id != ontology_id:
        raise HTTPException(404, {"code": "process_not_found", "message": "Process model not found"})
    return value


def summary(value):
    return {"id": value.id, "name": value.name, "description": value.description,
            "entity_type_name": value.entity_type_name, "state_property": value.state_property,
            "states": value.states, "transitions": value.transitions, "status": value.status,
            "version": value.version, "etag": value.etag}


@router.get("/{ontology_id}/process-models")
def list_models(ontology_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    access(db, ontology_id, user)
    return {"process_models": [summary(item) for item in db.query(OntologyStateMachine).filter_by(ontology_id=ontology_id).all()]}


@router.post("/{ontology_id}/process-models", status_code=201)
def create_model(ontology_id: str, body: CreateProcessModel, db: Session = Depends(get_db), user=Depends(require_editor)):
    access(db, ontology_id, user)
    try: validate_metadata(load_sql_metadata(db, ontology_id), body.entity_type_name, body.state_property)
    except ProcessError as exc: raise HTTPException(exc.status, {"code": exc.code, "message": exc.message, "path": exc.path}) from exc
    definition = body.definition.model_dump(mode="json", by_alias=True)
    value = OntologyStateMachine(ontology_id=ontology_id, name=body.name, description=body.description,
                                 entity_type_name=body.entity_type_name, state_property=body.state_property,
                                 states=definition["states"], transitions=definition["transitions"], created_by=user.id)
    db.add(value); db.commit(); db.refresh(value); return summary(value)


@router.patch("/{ontology_id}/process-models/{machine_id}")
def update_model(ontology_id: str, machine_id: str, body: UpdateProcessModel,
                 db: Session = Depends(get_db), user=Depends(require_editor)):
    access(db, ontology_id, user); value = row(db, ontology_id, machine_id)
    if value.etag != body.expected_etag: raise HTTPException(409, {"code": "etag_mismatch", "message": "Process changed; refresh and retry"})
    if body.definition:
        definition = body.definition.model_dump(mode="json", by_alias=True)
        value.states, value.transitions = definition["states"], definition["transitions"]
    if body.name is not None: value.name = body.name
    if body.description is not None: value.description = body.description
    value.status, value.etag, value.updated_at = "draft", value.etag + 1, datetime.now(timezone.utc)
    db.commit(); return summary(value)


@router.post("/{ontology_id}/process-models/{machine_id}:publish")
def publish_model(ontology_id: str, machine_id: str, body: PublishProcessModel,
                  db: Session = Depends(get_db), user=Depends(require_editor)):
    access(db, ontology_id, user); value = row(db, ontology_id, machine_id)
    if value.etag != body.expected_etag: raise HTTPException(409, {"code": "etag_mismatch", "message": "Process changed; refresh and retry"})
    try:
        ProcessDefinition.model_validate({"states": value.states, "transitions": value.transitions})
        validate_metadata(load_sql_metadata(db, ontology_id), value.entity_type_name, value.state_property)
        actions = publish(db, value, user.id)
    except ProcessError as exc: raise HTTPException(exc.status, {"code": exc.code, "message": exc.message, "path": exc.path}) from exc
    return {**summary(value), "actions": [{"id": item.id, "transition_key": item.transition_key,
                                             "name": item.name, "version": item.version, "status": item.status} for item in actions]}
