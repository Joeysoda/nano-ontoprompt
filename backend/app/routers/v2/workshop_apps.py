"""User-built applications: pages of bound widgets, saved as immutable versions.

The definition is data, not code.  Widgets bind to a data source by path,
share selections through variables and react to clicks through declared
events.  Derived values come from Functions that produce the source, never
from expressions evaluated in the page.

A data source is a Study run (a Function's outputs), an object set read from
a snapshot of the Ontology, or the rule alerts the viewer may see.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models.v2.scenario import ScenarioRun, ScenarioRunArtifact
from app.models.v2.scenario_study import StudyDefinition
from app.models.v2.workshop_app import WorkshopApp, WorkshopAppVersion
from app.services.v2.object_query.authorization import require_ontology_access
from app.routers.v2.object_sets import http_error
from app.services.v2 import workshop_sources
from app.services.v2.object_query.errors import ObjectQueryError
from app.services.v2.object_query.metadata import load_sql_metadata
from app.services.v2.object_query.normalize import stable_hash

router = APIRouter()
ID = r"^[A-Za-z][A-Za-z0-9_-]{0,59}$"
# Widget type -> props it cannot work without.  Kept in sync with the frontend registry.
WIDGETS: dict[str, set[str]] = {
    "kpi_strip": {"items"}, "table": {"columns"}, "bar_chart": {"x_field", "series"}, "coverage_grid": {"label_field", "values_field"},
    "facet_list": {"field"}, "select": {"value_field", "label_field", "variable"}, "stat_row": {"items"}, "banner": {"template"},
    "action_list": {"label_field"}, "action_detail": {"variable"}, "rerun_button": {"steps"},
    "donut_chart": {"category_field"}, "rank_bars": {"label_field"}, "alert_list": {"title"}, "geo_map": {"lat_field", "lng_field"},
}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StudyRunSource(Strict):
    id: str = Field(pattern=ID)
    kind: Literal["study_run"] = "study_run"
    label: str = ""
    study_id: str
    node: str = Field(min_length=1, max_length=60)


class SourceFilter(Strict):
    field: str = Field(min_length=1, max_length=200)
    op: Literal["eq", "ne", "gt", "gte", "lt", "lte", "in"] = "eq"
    value: Any


class ObjectSetSource(Strict):
    id: str = Field(pattern=ID)
    kind: Literal["object_set"]
    label: str = ""
    object_type: str = Field(min_length=1, max_length=200)
    select: list[str] = Field(default_factory=list, max_length=200)   # empty: every property of the type
    filters: list[SourceFilter] = Field(default_factory=list, max_length=10)
    limit: int = Field(default=workshop_sources.MAX_OBJECTS, ge=1, le=workshop_sources.MAX_OBJECTS)


class AlertsSource(Strict):
    id: str = Field(pattern=ID)
    kind: Literal["alerts"]
    label: str = ""
    status: Literal["open", "acknowledged", "resolved"] | None = "open"
    subject_type: str | None = None
    limit: int = Field(default=workshop_sources.MAX_ALERTS, ge=1, le=workshop_sources.MAX_ALERTS)


DataSource = Annotated[StudyRunSource | ObjectSetSource | AlertsSource, Field(discriminator="kind")]


class Variable(Strict):
    id: str = Field(pattern=ID)
    label: str = ""
    default: Any = None


class Event(Strict):
    set_variable: str | None = None
    field: str | None = None          # row field whose value is assigned
    navigate: str | None = None       # page id


class Widget(Strict):
    id: str = Field(pattern=ID)
    type: str
    title: str = ""
    source: str | None = None
    path: str = ""
    props: dict[str, Any] = Field(default_factory=dict)
    on_select: Event | None = None


class LayoutItem(Strict):
    i: str
    x: int = Field(ge=0, le=24)
    y: int = Field(ge=0, le=500)
    w: int = Field(ge=1, le=24)
    h: int = Field(ge=1, le=60)


class Page(Strict):
    id: str = Field(pattern=ID)
    title: str = Field(min_length=1, max_length=80)
    widgets: list[Widget] = Field(default_factory=list, max_length=40)
    layout: list[LayoutItem] = Field(default_factory=list)


class AppDefinition(Strict):
    schema_version: Literal["workshop-app/v1"] = Field("workshop-app/v1", alias="schema")
    data_sources: list[DataSource] = Field(default_factory=list, max_length=10)
    variables: list[Variable] = Field(default_factory=list, max_length=40)
    pages: list[Page] = Field(min_length=1, max_length=20)
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @model_validator(mode="after")
    def references(self):
        sources = {row.id for row in self.data_sources}
        variables = {row.id for row in self.variables}
        pages = [row.id for row in self.pages]
        if len(set(pages)) != len(pages) or len(sources) != len(self.data_sources) or len(variables) != len(self.variables):
            raise ValueError("Page, data source and variable ids must be unique")
        for page in self.pages:
            ids = [row.id for row in page.widgets]
            if len(set(ids)) != len(ids):
                raise ValueError(f"Widget ids must be unique on page {page.id}")
            if {row.i for row in page.layout} - set(ids):
                raise ValueError(f"Layout on page {page.id} refers to an unknown widget")
            for widget in page.widgets:
                where = f"page {page.id} widget {widget.id}"
                if widget.type not in WIDGETS:
                    raise ValueError(f"{where}: unknown widget type {widget.type}")
                if missing := WIDGETS[widget.type] - set(widget.props):
                    raise ValueError(f"{where}: missing {sorted(missing)}")
                if widget.source and widget.source not in sources:
                    raise ValueError(f"{where}: unknown data source {widget.source}")
                used = {widget.props.get(key) for key in ("variable", "filter_variable")} - {None}
                if widget.on_select:
                    used |= {widget.on_select.set_variable} - {None}
                    if widget.on_select.navigate and widget.on_select.navigate not in pages:
                        raise ValueError(f"{where}: navigates to unknown page {widget.on_select.navigate}")
                if unknown := used - variables:
                    raise ValueError(f"{where}: unknown variables {sorted(unknown)}")
        return self


class CreateApp(Strict):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    definition: AppDefinition


class SaveVersion(Strict):
    expected_version: int = Field(ge=1)
    definition: AppDefinition
    comment: str = Field(default="", max_length=2000)


def _authorize(ontology_id: str, db: Session, user):
    try:
        return require_ontology_access(db, ontology_id, str(user.id), user.role)
    except ObjectQueryError as exc:
        raise HTTPException(status_code=404, detail="Ontology not found or inaccessible") from exc


def _app(db, ontology_id, app_id, user) -> WorkshopApp:
    row = db.get(WorkshopApp, app_id)
    if row is None or row.ontology_id != ontology_id or (user.role != "admin" and row.owner_id != user.id):
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "App not found"})
    return row


def _definition(db, ontology_id, definition: AppDefinition) -> dict:
    metadata = None
    for source in definition.data_sources:
        if source.kind == "study_run" and not db.query(StudyDefinition).filter_by(id=source.study_id, ontology_id=ontology_id).first():
            raise HTTPException(422, {"code": "unknown_study", "message": f"Data source {source.id} points to a Study outside this ontology"})
        if source.kind == "object_set":
            metadata = metadata or load_sql_metadata(db, ontology_id)
            try:
                known = set(workshop_sources.object_type_properties(metadata, source.object_type))
            except ObjectQueryError as exc:
                raise HTTPException(422, {"code": "unknown_type", "message": f"Data source {source.id}: {exc.issue.message}"}) from exc
            if unknown := (set(source.select) | {row.field for row in source.filters}) - known:
                raise HTTPException(422, {"code": "unknown_property", "message": f"Data source {source.id}: unknown properties {sorted(unknown)}"})
    return definition.model_dump(mode="json", by_alias=True)


def _version(v: WorkshopAppVersion) -> dict:
    return {"version": v.version, "definition": v.definition, "definition_digest": v.definition_digest, "comment": v.comment,
            "created_by": v.created_by, "created_at": v.created_at.isoformat()}


def _summary(row: WorkshopApp) -> dict:
    return {"id": row.id, "name": row.name, "description": row.description, "head_version": row.head_version,
            "owner_id": row.owner_id, "updated_at": row.updated_at.isoformat()}


@router.get("/{ontology_id}/workshop-apps")
def list_apps(ontology_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    _authorize(ontology_id, db, user)
    query = db.query(WorkshopApp).filter_by(ontology_id=ontology_id)
    if user.role != "admin":
        query = query.filter_by(owner_id=user.id)
    return {"apps": [_summary(row) for row in query.order_by(WorkshopApp.updated_at.desc()).all()]}


@router.post("/{ontology_id}/workshop-apps", status_code=201)
def create_app(ontology_id: str, body: CreateApp, db: Session = Depends(get_db), user=Depends(get_current_user)):
    _authorize(ontology_id, db, user)
    definition = _definition(db, ontology_id, body.definition)
    row = WorkshopApp(ontology_id=ontology_id, owner_id=user.id, name=body.name.strip(), description=body.description)
    db.add(row); db.flush()
    version = WorkshopAppVersion(app_id=row.id, version=1, definition=definition, definition_digest=stable_hash(definition), created_by=user.id)
    db.add(version); db.commit()
    return {**_summary(row), "head": _version(version)}


@router.post("/{ontology_id}/workshop-apps/{app_id}/versions", status_code=201)
def save_version(ontology_id: str, app_id: str, body: SaveVersion, db: Session = Depends(get_db), user=Depends(get_current_user)):
    _authorize(ontology_id, db, user)
    row = _app(db, ontology_id, app_id, user)
    if body.expected_version != row.head_version:
        raise HTTPException(409, {"code": "version_conflict", "message": "App changed; reload before saving", "head_version": row.head_version})
    definition = _definition(db, ontology_id, body.definition)
    digest = stable_hash(definition)
    head = db.query(WorkshopAppVersion).filter_by(app_id=row.id, version=row.head_version).one()
    if head.definition_digest == digest:
        return {**_summary(row), "head": _version(head), "unchanged": True}
    version = WorkshopAppVersion(app_id=row.id, version=row.head_version + 1, definition=definition, definition_digest=digest,
                                 comment=body.comment, created_by=user.id)
    db.add(version)
    row.head_version, row.updated_at = version.version, datetime.now(timezone.utc)
    db.commit()
    return {**_summary(row), "head": _version(version), "unchanged": False}


@router.get("/{ontology_id}/workshop-apps/{app_id}")
def get_app(ontology_id: str, app_id: str, version: int | None = None, db: Session = Depends(get_db), user=Depends(get_current_user)):
    _authorize(ontology_id, db, user)
    row = _app(db, ontology_id, app_id, user)
    found = db.query(WorkshopAppVersion).filter_by(app_id=row.id, version=version or row.head_version).first()
    if not found:
        raise HTTPException(404, {"code": "version_not_found", "message": "Version not found"})
    return {**_summary(row), "head": _version(found)}


@router.post("/{ontology_id}/workshop-apps/{app_id}/snapshot", status_code=201)
def refresh_snapshot(ontology_id: str, app_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Take a new Ontology snapshot for this app; its object-set sources read it from now on."""
    _authorize(ontology_id, db, user)
    _app(db, ontology_id, app_id, user)
    try:
        view = workshop_sources.take_snapshot(db, ontology_id, app_id, user)
    except ObjectQueryError as exc:
        raise http_error(exc) from exc
    return {"data_view_id": view.id, "created_at": view.created_at.isoformat(), "object_count": view.object_count}


@router.get("/{ontology_id}/workshop-apps/{app_id}/sources/{source_id}")
def resolve_source(ontology_id: str, app_id: str, source_id: str, run: str | None = None, version: int | None = None,
                   db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Resolve a data source.  Study runs: a pinned run when given, else the latest completed run."""
    access = _authorize(ontology_id, db, user)
    row = _app(db, ontology_id, app_id, user)
    found = db.query(WorkshopAppVersion).filter_by(app_id=row.id, version=version or row.head_version).one()
    source = next((item for item in found.definition.get("data_sources", []) if item["id"] == source_id), None)
    if not source:
        raise HTTPException(404, {"code": "source_not_found", "message": "Data source not found"})
    if source.get("kind") in ("object_set", "alerts"):
        try:
            resolved = workshop_sources.resolve_object_set(db, ontology_id, app_id, source, user, access) if source["kind"] == "object_set" \
                else workshop_sources.resolve_alerts(db, ontology_id, source, user)
        except ObjectQueryError as exc:
            raise http_error(exc) from exc
        return {"source_id": source_id, **resolved}
    query = db.query(ScenarioRun).filter_by(study_id=source["study_id"])
    target = query.filter_by(id=run).first() if run else \
        query.filter_by(status="completed").order_by(ScenarioRun.completed_at.desc()).first()
    if not target:
        raise HTTPException(404, {"code": "no_completed_run", "message": "The Study has no completed run yet"})
    node = ((target.result_manifest or {}).get("nodes") or {}).get(source["node"])
    artifacts = db.query(ScenarioRunArtifact).filter_by(run_id=target.id).all()
    return {"source_id": source_id, "kind": "study_run", "study_id": source["study_id"], "node": source["node"], "run_id": target.id,
            "status": target.status, "scenario_id": target.scenario_id, "scenario_revision": target.revision,
            "completed_at": target.completed_at.isoformat() if target.completed_at else None,
            "outputs": (node or {}).get("outputs") if target.status == "completed" else None,
            "artifacts": [{"id": item.id, "artifact_type": item.artifact_type, "content_digest": item.content_digest}
                          for item in artifacts if item.artifact_type == f"node:{source['node']}"]}
