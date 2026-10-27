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

from fastapi import APIRouter, Depends, HTTPException, Query
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
    "schedule_gantt": {"id_field", "label_field", "resource_field", "start_field", "end_field"},
}
# Page template -> region id -> columns it spans (of 24).  Kept in sync with the frontend templates.
TEMPLATES: dict[str, dict[str, int]] = {
    "free": {},
    "scene_kpi_insight": {"scene": 15, "kpi": 9, "insight": 24},
    "split": {"left": 12, "right": 12},
    "kpi_top": {"kpi": 24, "main": 24},
}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StudyRunSource(Strict):
    id: str = Field(pattern=ID)
    kind: Literal["study_run"] = "study_run"
    label: str = ""
    study_id: str
    node: str = Field(min_length=1, max_length=60)
    time_field: str | None = Field(default=None, max_length=200)   # rows the app-wide time range applies to


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
    time_field: str | None = Field(default=None, max_length=200)   # rows the app-wide time range applies to
    order_by: str | None = None       # with limit: an intentional top N (e.g. the 20 latest), not a truncated set
    descending: bool = True


class AlertsSource(Strict):
    id: str = Field(pattern=ID)
    kind: Literal["alerts"]
    label: str = ""
    status: Literal["open", "acknowledged", "resolved"] | None = "open"
    subject_type: str | None = None
    limit: int = Field(default=workshop_sources.MAX_ALERTS, ge=1, le=workshop_sources.MAX_ALERTS)
    time_field: str | None = Field(default=None, max_length=200)   # rows the app-wide time range applies to


class Metric(Strict):
    key: str = Field(pattern=ID)
    op: Literal["count", "sum", "avg", "min", "max", "exact_distinct"] = "count"
    property: str | None = None

    @model_validator(mode="after")
    def needs_property(self):
        if (self.op == "count") == bool(self.property):
            raise ValueError(f"metric {self.key}: count takes no property, every other op needs one")
        return self


class Join(Strict):
    """Search Around by a shared key: each group picks up properties of the dimension object whose key matches."""
    object_type: str = Field(min_length=1, max_length=200)
    on: str                            # the group_by field holding the key
    key: str | None = None             # property of the dimension object; None: its object id
    select: list[str] = Field(min_length=1, max_length=20)


class Band(Strict):
    """Turn a number into a category: the first cut whose minimum the value reaches, highest first."""
    field: str
    into: str = Field(pattern=ID)
    cuts: list[tuple[float, str]] = Field(min_length=1, max_length=10)
    otherwise: str = ""


class AggregateSource(Strict):
    id: str = Field(pattern=ID)
    kind: Literal["aggregate"]
    label: str = ""
    object_type: str = Field(min_length=1, max_length=200)
    group_by: list[str] = Field(default_factory=list, max_length=5)
    metrics: list[Metric] = Field(min_length=1, max_length=10)
    filters: list[SourceFilter] = Field(default_factory=list, max_length=10)
    time_field: str | None = Field(default=None, max_length=200)
    join: Join | None = None
    bands: list[Band] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def references(self):
        if self.join and self.join.on not in self.group_by:
            raise ValueError("join.on must be one of group_by")
        return self


class QuerySource(Strict):
    """A shared ontology query.  version: a range such as "^1" (empty = the current major, fixed on save);
    params: a value, or {"variable": id} to follow an app variable."""
    id: str = Field(pattern=ID)
    kind: Literal["query"]
    label: str = ""
    query: str = Field(pattern=r"^[a-z][a-z0-9_]{1,62}$")
    version: str = ""
    params: dict[str, Any] = Field(default_factory=dict)


def query_variables(source) -> set[str]:
    params = source.params if hasattr(source, "params") else source.get("params") or {}
    return {value["variable"] for value in params.values() if isinstance(value, dict) and value.get("variable")}


DataSource = Annotated[StudyRunSource | ObjectSetSource | AlertsSource | AggregateSource | QuerySource, Field(discriminator="kind")]


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
    region: str | None = None         # region of the page template; None on a free page


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
    layout: list[LayoutItem] = Field(default_factory=list)   # coordinates are relative to the widget's region
    template: str = "free"


class AppSettings(Strict):
    time_range: Literal["all", "1d", "7d", "30d"] = "all"   # default of the app-wide time range control
    refresh_minutes: Literal[0, 1, 5, 15] = 0              # 0: refresh by hand


class AppDefinition(Strict):
    schema_version: Literal["workshop-app/v1"] = Field("workshop-app/v1", alias="schema")
    data_sources: list[DataSource] = Field(default_factory=list, max_length=10)
    variables: list[Variable] = Field(default_factory=list, max_length=40)
    pages: list[Page] = Field(min_length=1, max_length=20)
    settings: AppSettings = Field(default_factory=AppSettings)
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @model_validator(mode="after")
    def references(self):
        sources = {row.id for row in self.data_sources}
        variables = {row.id for row in self.variables}
        pages = [row.id for row in self.pages]
        if len(set(pages)) != len(pages) or len(sources) != len(self.data_sources) or len(variables) != len(self.variables):
            raise ValueError("Page, data source and variable ids must be unique")
        for source in self.data_sources:
            if source.kind == "query" and (unknown := query_variables(source) - variables):
                raise ValueError(f"data source {source.id}: unknown variables {sorted(unknown)}")
        for page in self.pages:
            ids = [row.id for row in page.widgets]
            if len(set(ids)) != len(ids):
                raise ValueError(f"Widget ids must be unique on page {page.id}")
            if {row.i for row in page.layout} - set(ids):
                raise ValueError(f"Layout on page {page.id} refers to an unknown widget")
            if page.template not in TEMPLATES:
                raise ValueError(f"page {page.id}: unknown template {page.template}")
            regions = TEMPLATES[page.template]
            spans = {row.id: regions.get(row.region or "", 24) for row in page.widgets}
            for item in page.layout:
                if item.x + item.w > spans[item.i]:
                    raise ValueError(f"page {page.id} widget {item.i}: layout is wider than its region")
            for widget in page.widgets:
                where = f"page {page.id} widget {widget.id}"
                if widget.type not in WIDGETS:
                    raise ValueError(f"{where}: unknown widget type {widget.type}")
                if missing := WIDGETS[widget.type] - set(widget.props):
                    raise ValueError(f"{where}: missing {sorted(missing)}")
                if widget.type == "schedule_gantt":
                    snap = widget.props.get("snap_minutes", 15)
                    start = widget.props.get("operating_start_minute", 0)
                    end = widget.props.get("operating_end_minute", 1440)
                    if not isinstance(snap, (int, float)) or isinstance(snap, bool) or not 1 <= snap <= 1440:
                        raise ValueError(f"{where}: snap_minutes must be between 1 and 1440")
                    if any(not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1440 for value in (start, end)):
                        raise ValueError(f"{where}: operating minutes must be between 0 and 1440")
                    behavior = widget.props.get("allocation_behavior", "allocate")
                    if behavior not in {"allocate", "no_overlaps", "time_only", "resource_only"}:
                        raise ValueError(f"{where}: unknown allocation_behavior {behavior}")
                    if widget.props.get("resource_cardinality", "one") not in {"one", "many"}:
                        raise ValueError(f"{where}: resource_cardinality must be one or many")
                    if widget.props.get("resource_drop_behavior", "replace") not in {"replace", "append"}:
                        raise ValueError(f"{where}: resource_drop_behavior must be replace or append")
                    resources = widget.props.get("resources", [])
                    if not isinstance(resources, list) or len(resources) > 200:
                        raise ValueError(f"{where}: resources must be a list of at most 200 entries")
                    resource_ids = [row.get("id") for row in resources if isinstance(row, dict)]
                    if len(resource_ids) != len(resources) or any(not value for value in resource_ids) or len(resource_ids) != len(set(resource_ids)):
                        raise ValueError(f"{where}: resource ids must be present and unique")
                    layers = widget.props.get("layers", [])
                    if not isinstance(layers, list) or len(layers) > 20:
                        raise ValueError(f"{where}: layers must be a list of at most 20 entries")
                    layer_ids = [row.get("id") for row in layers if isinstance(row, dict)]
                    if len(layer_ids) != len(layers) or any(not value for value in layer_ids) or len(layer_ids) != len(set(layer_ids)) or any(row.get("style", "standard") not in {"standard", "background", "event"} for row in layers):
                        raise ValueError(f"{where}: layer ids must be unique and styles must be standard, background, or event")
                    if widget.props.get("create_action_type_id") and any(not widget.props.get(key) for key in ("create_start_parameter", "create_end_parameter", "create_resource_parameter")):
                        raise ValueError(f"{where}: drag-create requires start, end, and resource parameter names")
                    recommendation = widget.props.get("recommendation_asset_id")
                    if recommendation is not None and (not isinstance(recommendation, str) or not recommendation.strip() or len(recommendation) > 200):
                        raise ValueError(f"{where}: recommendation_asset_id must be a non-empty string of at most 200 characters")
                    validation = widget.props.get("validation_asset_id")
                    if validation is not None and (not isinstance(validation, str) or not validation.strip() or len(validation) > 200):
                        raise ValueError(f"{where}: validation_asset_id must be a non-empty string of at most 200 characters")
                if regions and widget.region not in regions:
                    raise ValueError(f"{where}: region must be one of {sorted(regions)}")
                if not regions and widget.region:
                    raise ValueError(f"{where}: a free page has no regions")
                if widget.source and widget.source not in sources:
                    raise ValueError(f"{where}: unknown data source {widget.source}")
                used = {widget.props.get(key) for key in ("variable", "filter_variable", "scenario_variable")} - {None}
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
    from app.models.v2.ontology_query import OntologyQuery
    from app.services.v2 import ontology_queries
    metadata, pinned = None, {}
    for source in definition.data_sources:
        if source.kind == "query":
            query = db.query(OntologyQuery).filter_by(ontology_id=ontology_id, api_name=source.query).first()
            if query is None:
                raise HTTPException(422, {"code": "query_not_found", "message": f"Data source {source.id}: query {source.query} does not exist"})
            wanted = source.version or f"^{ontology_queries.parse(query.latest_version)[0]}"   # follow the current major by default
            try:
                version = ontology_queries.pick(db, query, wanted)
            except ontology_queries.QueryError as exc:
                raise HTTPException(422, {"code": exc.code, "message": f"Data source {source.id}: {exc.message}"}) from exc
            if unknown := set(source.params) - {p["name"] for p in version.definition.get("params", [])}:
                raise HTTPException(422, {"code": "unknown_parameter", "message": f"Data source {source.id}: unknown parameters {sorted(unknown)}"})
            pinned[source.id] = wanted
        if source.kind == "study_run" and not db.query(StudyDefinition).filter_by(id=source.study_id, ontology_id=ontology_id).first():
            raise HTTPException(422, {"code": "unknown_study", "message": f"Data source {source.id} points to a Study outside this ontology"})
        if source.kind == "object_set":
            metadata = metadata or load_sql_metadata(db, ontology_id)
            try:
                known = set(workshop_sources.object_type_properties(metadata, source.object_type))
            except ObjectQueryError as exc:
                raise HTTPException(422, {"code": "unknown_type", "message": f"Data source {source.id}: {exc.issue.message}"}) from exc
            if unknown := (set(source.select) | {row.field for row in source.filters} | ({source.time_field, source.order_by} - {None})) - known:
                raise HTTPException(422, {"code": "unknown_property", "message": f"Data source {source.id}: unknown properties {sorted(unknown)}"})
        if source.kind == "aggregate":
            metadata = metadata or load_sql_metadata(db, ontology_id)
            wanted = {source.object_type: set(source.group_by) | {row.property for row in source.metrics if row.property}
                      | {row.field for row in source.filters} | ({source.time_field} - {None})}
            if source.join:
                wanted[source.join.object_type] = set(source.join.select) | ({source.join.key} - {None})
            for object_type, fields in wanted.items():
                try:
                    known = set(workshop_sources.object_type_properties(metadata, object_type))
                except ObjectQueryError as exc:
                    raise HTTPException(422, {"code": "unknown_type", "message": f"Data source {source.id}: {exc.issue.message}"}) from exc
                if unknown := fields - known:
                    raise HTTPException(422, {"code": "unknown_property", "message": f"Data source {source.id}: unknown properties {sorted(unknown)} on {object_type}"})
    dumped = definition.model_dump(mode="json", by_alias=True)
    for source in dumped["data_sources"]:
        if source["id"] in pinned:
            source["version"] = pinned[source["id"]]
    return dumped


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


def _resolve(db, ontology_id: str, app_id: str, source: dict, run: str | None, time_range: str, user, access, values: dict | None = None) -> dict:
    """Resolve one source definition.  Study runs: a pinned run when given, else the latest completed run.
    Ontology sources apply the app-wide time range on the server; query sources take app variable values."""
    if source.get("kind") == "query":
        from app.models.v2.ontology_query import OntologyQuery
        from app.services.v2 import ontology_queries
        query = db.query(OntologyQuery).filter_by(ontology_id=ontology_id, api_name=source["query"]).first()
        if query is None:
            raise HTTPException(404, {"code": "query_not_found", "message": f"Query {source['query']} not found"})
        params = {}
        for name, value in (source.get("params") or {}).items():
            value = (values or {}).get(value["variable"]) if isinstance(value, dict) and value.get("variable") else value
            if value not in (None, ""):
                params[name] = value
        try:
            version = ontology_queries.pick(db, query, source.get("version") or "latest")
            return {"source_id": source["id"], "query": query.api_name, **ontology_queries.run(db, ontology_id, version, params, time_range, user, access)}
        except ontology_queries.QueryError as exc:
            raise HTTPException(exc.status, {"code": exc.code, "message": exc.message}) from exc
        except ObjectQueryError as exc:
            raise http_error(exc) from exc
    if source.get("kind") in ("object_set", "alerts", "aggregate"):
        try:
            if source["kind"] == "object_set":
                resolved = workshop_sources.resolve_object_set(db, ontology_id, app_id, source, user, access, time_range)
            elif source["kind"] == "aggregate":
                resolved = workshop_sources.resolve_aggregate(db, ontology_id, app_id, source, user, access, time_range)
            else:
                resolved = workshop_sources.resolve_alerts(db, ontology_id, source, user, time_range)
        except ObjectQueryError as exc:
            raise http_error(exc) from exc
        return {"source_id": source["id"], **resolved}
    query = db.query(ScenarioRun).filter_by(study_id=source["study_id"])
    target = query.filter_by(id=run).first() if run else \
        query.filter_by(status="completed").order_by(ScenarioRun.completed_at.desc()).first()
    if not target:
        raise HTTPException(404, {"code": "no_completed_run", "message": "The Study has no completed run yet"})
    node = ((target.result_manifest or {}).get("nodes") or {}).get(source["node"])
    artifacts = db.query(ScenarioRunArtifact).filter_by(run_id=target.id).all()
    return {"source_id": source["id"], "kind": "study_run", "study_id": source["study_id"], "node": source["node"], "run_id": target.id,
            "status": target.status, "scenario_id": target.scenario_id, "scenario_revision": target.revision,
            "completed_at": target.completed_at.isoformat() if target.completed_at else None,
            "outputs": (node or {}).get("outputs") if target.status == "completed" else None,
            "artifacts": [{"id": item.id, "artifact_type": item.artifact_type, "content_digest": item.content_digest}
                          for item in artifacts if item.artifact_type == f"node:{source['node']}"]}


TimeRange = Literal["all", "1d", "7d", "30d"]


@router.get("/{ontology_id}/workshop-apps/{app_id}/sources/{source_id}")
def resolve_source(ontology_id: str, app_id: str, source_id: str, run: str | None = None, version: int | None = None,
                   time_range: TimeRange = Query("all", alias="range"), values: str | None = Query(None, description="JSON object of app variable values"),
                   db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Resolve a data source of a saved version of the app."""
    access = _authorize(ontology_id, db, user)
    row = _app(db, ontology_id, app_id, user)
    found = db.query(WorkshopAppVersion).filter_by(app_id=row.id, version=version or row.head_version).one()
    source = next((item for item in found.definition.get("data_sources", []) if item["id"] == source_id), None)
    if not source:
        raise HTTPException(404, {"code": "source_not_found", "message": "Data source not found"})
    import json
    return _resolve(db, ontology_id, app_id, source, run, time_range, user, access, json.loads(values) if values else None)


class PreviewSource(Strict):
    source: DataSource
    run: str | None = None
    range: TimeRange = "all"
    values: dict[str, Any] = Field(default_factory=dict)   # app variable values for query parameters


@router.post("/{ontology_id}/workshop-apps/{app_id}/sources:preview")
def preview_source(ontology_id: str, app_id: str, body: PreviewSource, db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Resolve a source the editor has not published yet, after the same checks a saved app gets."""
    access = _authorize(ontology_id, db, user)
    _app(db, ontology_id, app_id, user)
    definition = AppDefinition.model_validate({"schema": "workshop-app/v1", "data_sources": [body.source.model_dump(mode="json")],
                                               "variables": [{"id": name} for name in query_variables(body.source)], "pages": [{"id": "preview", "title": "preview"}]})
    source = _definition(db, ontology_id, definition)["data_sources"][0]
    return _resolve(db, ontology_id, app_id, source, body.run, body.range, user, access, body.values)


class Autobind(Strict):
    object_type: str = Field(min_length=1, max_length=200)
    mode: Literal["rules", "llm"] = "rules"
    prefix: str = Field(default="auto_", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,20}$")   # keeps generated ids apart from an app's own


@router.post("/{ontology_id}/workshop-apps:autobind")
def autobind(ontology_id: str, body: Autobind, db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Propose a page for an object type: profile its properties, plan (rules or LLM), compile, validate.  Nothing is saved."""
    from app.services.v2 import workshop_autobind as auto
    access = _authorize(ontology_id, db, user)
    try:
        prof = auto.profile(db, ontology_id, body.object_type, user, access)
        model = None
        if body.mode == "llm":
            raw, model = auto.llm_plan(prof)
            plan, dropped = auto.check_plan(raw, prof)
        else:
            plan, dropped = auto.rule_plan(prof), []
        fragment = auto.compile_plan(plan, prof, body.prefix)
    except auto.AutobindError as exc:
        raise HTTPException(exc.status, {"code": exc.code, "message": exc.message}) from exc
    except ObjectQueryError as exc:
        raise http_error(exc) from exc
    # The proposal must pass the same checks as a saved app.
    definition = AppDefinition.model_validate({"schema": "workshop-app/v1", "data_sources": fragment["data_sources"], "variables": [], "pages": [fragment["page"]]})
    _definition(db, ontology_id, definition)
    return {"mode": body.mode, "model": model, "profile": prof, "plan": plan, "dropped": dropped, **fragment}
