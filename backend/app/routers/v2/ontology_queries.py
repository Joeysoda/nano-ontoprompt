"""Ontology queries over HTTP: library, versions (with compatibility checks), runs, and MCP scopes."""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models.v2.ontology_query import McpScope, OntologyQuery, OntologyQueryVersion
from app.models.v2.workshop_app import WorkshopApp, WorkshopAppVersion
from app.routers.v2.object_sets import http_error
from app.routers.v2.workshop_apps import ID, AggregateSource, AlertsSource, AppDefinition, ObjectSetSource, _authorize, _definition
from app.services.v2 import ontology_queries as service
from app.services.v2.object_query.errors import ObjectQueryError
from app.services.v2.object_query.metadata import load_sql_metadata
from typing import Annotated

router = APIRouter()
API_NAME = r"^[a-z][a-z0-9_]{1,62}$"
QuerySourceDef = Annotated[ObjectSetSource | AggregateSource | AlertsSource, Field(discriminator="kind")]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Param(Strict):
    """An equality filter a caller may set; time range and limit are always available."""
    name: str = Field(pattern=ID)
    field: str = Field(min_length=1, max_length=200)
    label: str = ""
    required: bool = False
    default: Any = None


class Definition(Strict):
    source: QuerySourceDef
    params: list[Param] = Field(default_factory=list, max_length=10)

    @model_validator(mode="before")
    @classmethod
    def source_id(cls, data):
        # A query's source reuses the Workshop source schema; its id only matters inside an app.
        if isinstance(data, dict) and isinstance(data.get("source"), dict) and "id" not in data["source"]:
            data = {**data, "source": {"id": "q", **data["source"]}}
        return data

    @model_validator(mode="after")
    def unique_params(self):
        if len({p.name for p in self.params}) != len(self.params):
            raise ValueError("Parameter names must be unique")
        return self


class CreateQuery(Strict):
    api_name: str = Field(pattern=API_NAME)
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    definition: Definition
    note: str = ""


class Publish(Strict):
    definition: Definition
    bump: Literal["auto", "major", "minor", "patch"] = "auto"
    note: str = ""


class Run(Strict):
    version: str = ""          # "^1", "1.2.0" or "" for the latest
    params: dict[str, Any] = Field(default_factory=dict)
    range: Literal["all", "1d", "7d", "30d"] = "all"
    limit: int | None = Field(default=None, ge=1, le=10_000)


class ScopeQuery(Strict):
    api_name: str = Field(pattern=API_NAME)
    range: str = ""            # defaults to the query's current major, e.g. "^1"


class CreateScope(Strict):
    name: str = Field(min_length=1, max_length=200)
    queries: list[ScopeQuery] = Field(min_length=1, max_length=50)


def _error(exc: service.QueryError):
    raise HTTPException(exc.status, {"code": exc.code, "message": exc.message, **exc.details}) from exc


def _query(db, ontology_id: str, api_name: str) -> OntologyQuery:
    row = db.query(OntologyQuery).filter_by(ontology_id=ontology_id, api_name=api_name).first()
    if row is None:
        raise HTTPException(404, {"code": "query_not_found", "message": f"Query {api_name} not found"})
    return row


def _checked(db, ontology_id: str, definition: Definition) -> tuple[dict, dict]:
    """Validate the source like a saved app does, check parameter fields, compute the output schema."""
    source = {**definition.source.model_dump(mode="json"), "id": "q"}
    _definition(db, ontology_id, AppDefinition.model_validate({"schema": "workshop-app/v1", "data_sources": [source], "variables": [],
                                                               "pages": [{"id": "q", "title": "q"}]}))
    if source["kind"] != "alerts":
        from app.services.v2.workshop_sources import object_type_properties
        known = set(object_type_properties(load_sql_metadata(db, ontology_id), source["object_type"]))
        if unknown := {p.field for p in definition.params} - known:
            raise HTTPException(422, {"code": "unknown_property", "message": f"Parameters use unknown properties {sorted(unknown)}"})
    elif {p.field for p in definition.params} - {"subject_type"}:
        raise HTTPException(422, {"code": "unsupported_parameter", "message": "Alert queries can only be parameterized by subject_type"})
    stored = {"source": source, "params": [p.model_dump(mode="json") for p in definition.params]}
    return stored, service.output_schema(db, ontology_id, source)


def references(db, ontology_id: str, api_name: str) -> list[dict]:
    """Apps whose current version reads this query, with the version range they follow."""
    out = []
    for app in db.query(WorkshopApp).filter_by(ontology_id=ontology_id).all():
        head = db.query(WorkshopAppVersion).filter_by(app_id=app.id, version=app.head_version).first()
        for source in (head.definition.get("data_sources", []) if head else []):
            if source.get("kind") == "query" and source.get("query") == api_name:
                out.append({"app_id": app.id, "app_name": app.name, "source_id": source["id"], "version": source.get("version") or ""})
    return out


def _summary(db, row: OntologyQuery) -> dict:
    latest = service.pick(db, row, "latest")
    return {"api_name": row.api_name, "name": row.name, "description": row.description, "latest_version": row.latest_version,
            "kind": latest.definition["source"]["kind"], "object_type": latest.definition["source"].get("object_type"),
            "params": latest.definition.get("params", []), "output_schema": latest.output_schema, "updated_at": row.updated_at.isoformat()}


@router.get("/{ontology_id}/queries")
def list_queries(ontology_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    _authorize(ontology_id, db, user)
    rows = db.query(OntologyQuery).filter_by(ontology_id=ontology_id).order_by(OntologyQuery.name).all()
    return {"queries": [{**_summary(db, row), "references": len(references(db, ontology_id, row.api_name))} for row in rows]}


@router.post("/{ontology_id}/queries", status_code=201)
def create_query(ontology_id: str, body: CreateQuery, db: Session = Depends(get_db), user=Depends(get_current_user)):
    _authorize(ontology_id, db, user)
    if db.query(OntologyQuery).filter_by(ontology_id=ontology_id, api_name=body.api_name).first():
        raise HTTPException(409, {"code": "api_name_taken", "message": f"{body.api_name} already exists"})
    definition, schema = _checked(db, ontology_id, body.definition)
    row = OntologyQuery(ontology_id=ontology_id, owner_id=user.id, api_name=body.api_name, name=body.name, description=body.description, latest_version="")
    db.add(row); db.flush()
    service.publish(db, row, definition, schema, "auto", body.note or "首次发布", user)
    db.commit()
    return _summary(db, row)


@router.get("/{ontology_id}/queries/{api_name}")
def get_query(ontology_id: str, api_name: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    _authorize(ontology_id, db, user)
    row = _query(db, ontology_id, api_name)
    versions = db.query(OntologyQueryVersion).filter_by(query_id=row.id).all()
    versions.sort(key=lambda v: (v.major, v.minor, v.patch), reverse=True)
    return {**_summary(db, row), "definition": service.pick(db, row, "latest").definition, "references": references(db, ontology_id, api_name),
            "versions": [{"version": v.version, "change_note": v.change_note, "created_at": v.created_at.isoformat()} for v in versions]}


@router.post("/{ontology_id}/queries/{api_name}:check")
def check_query(ontology_id: str, api_name: str, body: Publish, db: Session = Depends(get_db), user=Depends(get_current_user)):
    """What publishing would do: next version, breaking changes, and the apps that follow this query."""
    _authorize(ontology_id, db, user)
    row = _query(db, ontology_id, api_name)
    definition, schema = _checked(db, ontology_id, body.definition)
    previous = service.pick(db, row, "latest")
    breaking, added = service.compatibility(previous, definition, schema)
    try:
        version = service.next_version(previous, body.bump, breaking, added)
    except service.QueryError as exc:
        version = None
        breaking = exc.details.get("breaking", breaking)
    refs = references(db, ontology_id, api_name)
    follows = [r for r in refs if version and service.satisfies(version, r["version"] or f"^{previous.major}")]
    return {"next_version": version, "breaking": breaking, "added": added, "output_schema": schema, "references": refs, "will_update": follows}


@router.post("/{ontology_id}/queries/{api_name}/versions", status_code=201)
def publish_version(ontology_id: str, api_name: str, body: Publish, db: Session = Depends(get_db), user=Depends(get_current_user)):
    _authorize(ontology_id, db, user)
    row = _query(db, ontology_id, api_name)
    definition, schema = _checked(db, ontology_id, body.definition)
    try:
        version, breaking = service.publish(db, row, definition, schema, body.bump, body.note, user)
    except service.QueryError as exc:
        _error(exc)
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {**_summary(db, row), "published": version.version, "breaking": breaking}


@router.post("/{ontology_id}/queries/{api_name}:run")
def run_query(ontology_id: str, api_name: str, body: Run, db: Session = Depends(get_db), user=Depends(get_current_user)):
    """The query as an API: results are filtered by the caller's permissions."""
    access = _authorize(ontology_id, db, user)
    row = _query(db, ontology_id, api_name)
    try:
        return service.run(db, ontology_id, service.pick(db, row, body.version or "latest"), body.params, body.range, user, access, body.limit)
    except service.QueryError as exc:
        _error(exc)
    except ObjectQueryError as exc:
        raise http_error(exc) from exc


# --- MCP scopes -----------------------------------------------------------------------------------------------------------

def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@router.post("/{ontology_id}/mcp-scopes", status_code=201)
def create_scope(ontology_id: str, body: CreateScope, db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Open chosen queries to MCP clients.  The token is shown once and acts as the creating user."""
    _authorize(ontology_id, db, user)
    queries = []
    for item in body.queries:
        row = _query(db, ontology_id, item.api_name)
        wanted = item.range or f"^{service.parse(row.latest_version)[0]}"
        try:
            service.pick(db, row, wanted)
        except service.QueryError as exc:
            _error(exc)
        queries.append({"api_name": item.api_name, "range": wanted})
    token = "omcp_" + secrets.token_urlsafe(32)
    scope = McpScope(ontology_id=ontology_id, owner_id=user.id, name=body.name, queries=queries, token_hash=token_hash(token), token_prefix=token[:12])
    db.add(scope); db.commit()
    return {"id": scope.id, "name": scope.name, "queries": queries, "token": token, "endpoint": "/api/v2/mcp/"}


@router.get("/{ontology_id}/mcp-scopes")
def list_scopes(ontology_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    _authorize(ontology_id, db, user)
    rows = db.query(McpScope).filter_by(ontology_id=ontology_id, owner_id=user.id).all()
    return {"scopes": [{"id": s.id, "name": s.name, "queries": s.queries, "token_prefix": s.token_prefix, "revoked": s.revoked_at is not None} for s in rows]}


@router.delete("/{ontology_id}/mcp-scopes/{scope_id}", status_code=204)
def revoke_scope(ontology_id: str, scope_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    _authorize(ontology_id, db, user)
    scope = db.get(McpScope, scope_id)
    if scope is None or scope.ontology_id != ontology_id or scope.owner_id != user.id:
        raise HTTPException(404, {"code": "scope_not_found", "message": "Scope not found"})
    scope.revoked_at = datetime.now(timezone.utc)
    db.commit()
