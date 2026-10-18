"""Saved graph resources: create, version (optimistic), list and read history."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, StringConstraints
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models.v2.graph_view import GraphView, GraphViewVersion
from app.routers.v2.object_sets import http_error
from app.schemas.v2.object_query import ObjectSetExpr, StrictModel
from app.schemas.v2.object_set import ObjectSetDefinition
from app.services.v2.object_query.authorization import require_ontology_access
from app.services.v2.object_query.errors import ObjectQueryError
from app.services.v2.object_query.metadata import load_sql_metadata
from app.services.v2.object_query.normalize import stable_hash
from app.services.v2.object_query.resources import ResourceService

router = APIRouter()
Color = Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")]


class Layer(StrictModel):
    color: Color
    visible: bool = True


class GraphDefinition(StrictModel):
    # Several typed Object Sets: Search Around adds a set rather than mixing types.
    sets: list[ObjectSetExpr] = Field(min_length=1, max_length=20)
    data_view_id: str | None = Field(default=None, max_length=200)
    layers: dict[str, Layer] = Field(default_factory=dict, max_length=100)
    positions: dict[str, tuple[float, float]] = Field(default_factory=dict, max_length=1000)
    time_cursor: str | None = Field(default=None, max_length=40)


class CreateGraphView(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    definition: GraphDefinition


class SaveVersion(StrictModel):
    expected_version: int = Field(ge=1)
    definition: GraphDefinition
    comment: str = Field(default="", max_length=2000)


def _authorize(ontology_id: str, db: Session, user):
    try:
        return require_ontology_access(db, ontology_id, str(user.id), user.role)
    except ObjectQueryError as exc:
        raise HTTPException(status_code=404, detail="Ontology not found or inaccessible") from exc


def _validated(db, ontology_id, user, definition: GraphDefinition) -> dict:
    """Reject unknown types/links now, so a saved graph can always be reopened."""
    service = ResourceService(db, ontology_id, user, load_sql_metadata(db, ontology_id))
    for expression in definition.sets:
        service.prepare(ObjectSetDefinition(expression=expression))
    return definition.model_dump(mode="json")


def _view(db, ontology_id, graph_id, user) -> GraphView:
    row = db.get(GraphView, graph_id)
    if row is None or row.ontology_id != ontology_id or (user.role != "admin" and row.owner_id != user.id):
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Graph view not found"})
    return row


def _version_dict(v: GraphViewVersion) -> dict:
    return {"version": v.version, "definition": v.definition, "definition_digest": v.definition_digest,
            "data_view_id": v.data_view_id, "comment": v.comment, "created_by": v.created_by, "created_at": v.created_at.isoformat()}


def _summary(row: GraphView) -> dict:
    return {"id": row.id, "name": row.name, "description": row.description, "head_version": row.head_version,
            "owner_id": row.owner_id, "updated_at": row.updated_at.isoformat()}


@router.get("/{ontology_id}/graph-views")
def list_graph_views(ontology_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    _authorize(ontology_id, db, user)
    query = db.query(GraphView).filter_by(ontology_id=ontology_id)
    if user.role != "admin":
        query = query.filter_by(owner_id=user.id)
    return {"graph_views": [_summary(r) for r in query.order_by(GraphView.updated_at.desc()).all()]}


@router.post("/{ontology_id}/graph-views", status_code=201)
def create_graph_view(ontology_id: str, body: CreateGraphView, db: Session = Depends(get_db), user=Depends(get_current_user)):
    _authorize(ontology_id, db, user)
    try:
        definition = _validated(db, ontology_id, user, body.definition)
    except ObjectQueryError as exc:
        raise http_error(exc) from exc
    row = GraphView(ontology_id=ontology_id, owner_id=user.id, name=body.name.strip(), description=body.description)
    db.add(row); db.flush()
    db.add(GraphViewVersion(graph_view_id=row.id, version=1, definition=definition, definition_digest=stable_hash(definition),
                            data_view_id=body.definition.data_view_id, created_by=user.id))
    db.commit()
    return {**_summary(row), "head": _version_dict(db.query(GraphViewVersion).filter_by(graph_view_id=row.id, version=1).one())}


@router.post("/{ontology_id}/graph-views/{graph_id}/versions", status_code=201)
def save_version(ontology_id: str, graph_id: str, body: SaveVersion, db: Session = Depends(get_db), user=Depends(get_current_user)):
    _authorize(ontology_id, db, user)
    row = _view(db, ontology_id, graph_id, user)
    if body.expected_version != row.head_version:
        raise HTTPException(status_code=409, detail={"code": "version_conflict", "message": "Graph changed; reload before saving",
                                                     "head_version": row.head_version})
    try:
        definition = _validated(db, ontology_id, user, body.definition)
    except ObjectQueryError as exc:
        raise http_error(exc) from exc
    digest = stable_hash(definition)
    head = db.query(GraphViewVersion).filter_by(graph_view_id=row.id, version=row.head_version).one()
    if head.definition_digest == digest:
        return {**_summary(row), "head": _version_dict(head), "unchanged": True}
    version = GraphViewVersion(graph_view_id=row.id, version=row.head_version + 1, definition=definition, definition_digest=digest,
                               data_view_id=body.definition.data_view_id, comment=body.comment, created_by=user.id)
    db.add(version)
    row.head_version, row.updated_at = version.version, datetime.now(timezone.utc)
    db.commit()
    return {**_summary(row), "head": _version_dict(version), "unchanged": False}


@router.get("/{ontology_id}/graph-views/{graph_id}")
def get_graph_view(ontology_id: str, graph_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    _authorize(ontology_id, db, user)
    row = _view(db, ontology_id, graph_id, user)
    versions = db.query(GraphViewVersion).filter_by(graph_view_id=row.id).order_by(GraphViewVersion.version.desc()).all()
    return {**_summary(row), "head": _version_dict(versions[0]), "history": [
        {k: v for k, v in _version_dict(item).items() if k != "definition"} for item in versions]}


@router.get("/{ontology_id}/graph-views/{graph_id}/versions/{version}")
def get_graph_version(ontology_id: str, graph_id: str, version: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    _authorize(ontology_id, db, user)
    row = _view(db, ontology_id, graph_id, user)
    item = db.query(GraphViewVersion).filter_by(graph_view_id=row.id, version=version).first()
    if item is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Graph version not found"})
    return _version_dict(item)
