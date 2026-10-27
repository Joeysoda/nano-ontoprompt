from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models.v2.derived_projection import DerivedProjection, DerivedProjectionRun
from app.schemas.v2.derived_projection import CreateProjection, ProjectionSpec, UpdateProjection
from app.services.v2 import derived_projections as service
from app.services.v2.object_query.authorization import require_ontology_access
from app.services.v2.object_query.normalize import stable_hash

router = APIRouter()


def _access(db, ontology_id, user):
    try:
        return require_ontology_access(db, ontology_id, str(user.id), user.role)
    except Exception as exc:
        raise HTTPException(404, "Ontology not found or inaccessible") from exc


def _row(db, ontology_id, projection_id, user):
    row = db.get(DerivedProjection, projection_id)
    if row is None or row.ontology_id != ontology_id or (user.role != "admin" and row.owner_id != user.id):
        raise HTTPException(404, {"code": "projection_not_found", "message": "Derived projection not found"})
    return row


def _summary(db, row):
    return {"id": row.id, "api_name": row.api_name, "name": row.name, "description": row.description,
            "status": row.status, "interval_minutes": row.interval_minutes, "etag": row.etag,
            "spec": row.spec, "spec_digest": row.spec_digest, "last_run_at": row.last_run_at.isoformat() if row.last_run_at else None,
            "schedule_error": row.schedule_error, "freshness": service.freshness(db, row)}


def _error(exc):
    raise HTTPException(exc.status, {"code": exc.code, "message": exc.message, "path": exc.path}) from exc


@router.get("/{ontology_id}/derived-projections")
def list_projections(ontology_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    _access(db, ontology_id, user)
    query = db.query(DerivedProjection).filter_by(ontology_id=ontology_id)
    if user.role != "admin":
        query = query.filter_by(owner_id=user.id)
    return {"projections": [_summary(db, row) for row in query.order_by(DerivedProjection.name).all()]}


@router.post("/{ontology_id}/derived-projections", status_code=201)
def create_projection(ontology_id: str, body: CreateProjection, db: Session = Depends(get_db), user=Depends(get_current_user)):
    _access(db, ontology_id, user)
    try:
        service.resolve(db, ontology_id, body.spec)
    except service.ProjectionError as exc:
        _error(exc)
    spec = body.spec.model_dump(mode="json")
    row = DerivedProjection(ontology_id=ontology_id, owner_id=user.id, api_name=body.api_name, name=body.name,
                            description=body.description, spec=spec, spec_digest=stable_hash(spec),
                            interval_minutes=body.interval_minutes, status="paused")
    db.add(row)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback(); raise HTTPException(409, {"code": "api_name_taken", "message": "Projection API name already exists"}) from exc
    return _summary(db, row)


@router.patch("/{ontology_id}/derived-projections/{projection_id}")
def update_projection(ontology_id: str, projection_id: str, body: UpdateProjection,
                      db: Session = Depends(get_db), user=Depends(get_current_user)):
    _access(db, ontology_id, user); row = _row(db, ontology_id, projection_id, user)
    if row.etag != body.expected_etag:
        raise HTTPException(409, {"code": "etag_mismatch", "message": "Projection changed; refresh and retry"})
    if row.status == "retired":
        raise HTTPException(409, {"code": "projection_retired", "message": "Retired projections are immutable"})
    if body.spec is not None:
        try:
            service.resolve(db, ontology_id, body.spec)
        except service.ProjectionError as exc:
            _error(exc)
        row.spec = body.spec.model_dump(mode="json"); row.spec_digest = stable_hash(row.spec)
    for field in ("name", "description", "status"):
        value = getattr(body, field)
        if value is not None:
            setattr(row, field, value)
    if "interval_minutes" in body.model_fields_set:
        row.interval_minutes = body.interval_minutes
    if body.status == "active" and row.interval_minutes is None:
        raise HTTPException(422, {"code": "schedule_required", "message": "Active projections need an interval"})
    row.etag += 1; row.updated_at = datetime.now(timezone.utc); db.commit()
    return _summary(db, row)


@router.post("/{ontology_id}/derived-projections/{projection_id}:run")
def run_projection(ontology_id: str, projection_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    access = _access(db, ontology_id, user); row = _row(db, ontology_id, projection_id, user)
    if row.status == "retired":
        raise HTTPException(409, {"code": "projection_retired", "message": "Retired projections cannot run"})
    result = service.run_projection(db, row, user, access)
    payload = {"id": result.id, "status": result.status, "output": result.output, "error": result.error,
               "data_view_id": result.data_view_id, "input_digest": result.input_digest,
               "dependency_digest": result.dependency_digest, "freshness": service.freshness(db, row)}
    if result.status == "failed":
        raise HTTPException(422, payload)
    return payload


@router.get("/{ontology_id}/derived-projections/{projection_id}/runs")
def list_runs(ontology_id: str, projection_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    _access(db, ontology_id, user); _row(db, ontology_id, projection_id, user)
    rows = db.query(DerivedProjectionRun).filter_by(projection_id=projection_id).order_by(DerivedProjectionRun.created_at.desc()).limit(100).all()
    return {"runs": [{"id": row.id, "status": row.status, "query_version": row.query_version,
                       "data_view_id": row.data_view_id, "spec_digest": row.spec_digest,
                       "input_digest": row.input_digest, "dependency_digest": row.dependency_digest,
                       "output": row.output, "error": row.error, "created_at": row.created_at.isoformat()} for row in rows]}
