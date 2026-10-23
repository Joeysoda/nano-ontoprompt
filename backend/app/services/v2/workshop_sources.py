"""Resolve Workshop data sources that read the Ontology directly.

An ``object_set`` source reads every object of one type (optionally filtered)
from a snapshot data view, so the widgets that count, sum or rank rows see a
complete set rather than one live page.  The snapshot is shared by every
source of the app and reused until it expires or the viewer asks for a new
one, which is what the app's refresh control does.  An ``alerts`` source reads
rule alerts through the same visibility filter as the Alert Center.

An ``aggregate`` source groups an object set on the server (count / sum / avg /
min / max per group), so a dashboard over tens of thousands of event objects
(orders, failures) never ships the objects themselves.  Each group can pick up
properties of a dimension object through a shared key (a Search Around by
property), and numeric results can be banded into categories.

The app-wide time range is applied here, on the server: rows whose time field
falls in the last N days, counted back from the latest time in the source
itself, so historical datasets still have a "last 7 days".
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.models.v2.query_view import QueryDataView
from app.schemas.v2.object_query import (
    AggregateObjectSetRequest, AggregationSpec, AndFilter, BaseObjectSet, ComparisonFilter, ExecutionContext, FilterObjectSet,
    LoadObjectSetRequest, OrderBy, PropertyRef, ReadOptions, TypeRef,
)
from app.services.v2.graph.falkordb_service import FalkorDBService
from app.services.v2.object_query.authorization import build_query_policy, policies_from_projection
from app.services.v2.object_query.core import FalkorReadAdapter, QueryCore, metadata_digest
from app.services.v2.object_query.data_views import build_live_view
from app.services.v2.object_query.errors import ObjectQueryError
from app.services.v2.object_query.metadata import load_sql_metadata

SNAPSHOT_SECONDS = 3600
MAX_OBJECTS = 10_000
MAX_ALERTS = 10_000
MAX_AGGREGATED_OBJECTS = 200_000
RANGE_DAYS = {"1d": 1, "7d": 7, "30d": 30}


def snapshot_key(app_id: str) -> str:
    return f"workshop:{app_id}"


def latest_snapshot(db, ontology_id: str, app_id: str | None, user, digest: str) -> QueryDataView | None:
    """The app's newest live snapshot; with app_id None, the newest of any app (one-click binding only reads)."""
    query = db.query(QueryDataView).filter_by(ontology_id=ontology_id, status="ready", created_by=user.id, metadata_digest=digest)
    query = query.filter_by(source_manifest_digest=snapshot_key(app_id)) if app_id else query.filter(QueryDataView.source_manifest_digest.like("workshop:%"))
    return (query
            .filter(QueryDataView.retention_until > datetime.now(timezone.utc))
            .order_by(QueryDataView.created_at.desc()).first())


def take_snapshot(db, ontology_id: str, app_id: str, user, graph: FalkorDBService | None = None) -> QueryDataView:
    graph = graph or FalkorDBService()
    if not graph.available:
        raise ObjectQueryError("graph_unavailable", "execution", "FalkorDB is unavailable")
    metadata = load_sql_metadata(db, ontology_id)
    return build_live_view(db, graph, metadata, ontology_id, user.id, snapshot_key(app_id), SNAPSHOT_SECONDS)


def object_type_properties(metadata, object_type: str) -> list[str]:
    item = metadata.resolve_type("object", object_type, "object_type")
    return [name for name in item.properties if not name.startswith("_")]


_SNAPSHOTS: OrderedDict = OrderedDict()   # graph_key -> raw graph read; snapshots are immutable
_SNAPSHOT_CACHE_SIZE = 2


class _CachedSnapshot:
    """Read a snapshot graph once per process (a few most recent views), not once per query."""

    def __init__(self, adapter: FalkorReadAdapter, graph_key: str):
        self.adapter, self.graph_key = adapter, graph_key

    def read(self, ontology_id):
        if self.graph_key not in _SNAPSHOTS:
            _SNAPSHOTS[self.graph_key] = self.adapter.read(ontology_id)
            while len(_SNAPSHOTS) > _SNAPSHOT_CACHE_SIZE:
                _SNAPSHOTS.popitem(last=False)
        _SNAPSHOTS.move_to_end(self.graph_key)
        return _SNAPSHOTS[self.graph_key]


class _ProjectOnce:
    """The viewer's policy, projecting a given graph read once per request instead of once per query."""

    def __init__(self, policy):
        self._policy, self._last = policy, None

    def apply_projection(self, data):
        if self._last is None or self._last[0] is not data:
            self._last = (data, self._policy.apply_projection(data))
        return self._last[1]

    def __getattr__(self, name):
        return getattr(self._policy, name)


class _Reader:
    """Query Core over the app's snapshot, with the viewer's permissions."""

    def __init__(self, db, ontology_id: str, app_id: str, user, access, fields, max_objects: int = MAX_AGGREGATED_OBJECTS, reuse_any: bool = False):
        self.graph = FalkorDBService()
        if not self.graph.available:
            raise ObjectQueryError("graph_unavailable", "execution", "FalkorDB is unavailable")
        self.db, self.ontology_id, self.user, self.access, self.max_objects = db, ontology_id, user, access, max_objects
        self.metadata = load_sql_metadata(db, ontology_id)
        digest = metadata_digest(self.metadata)
        self.view = (latest_snapshot(db, ontology_id, app_id, user, digest) or (reuse_any and latest_snapshot(db, ontology_id, None, user, digest))
                     or take_snapshot(db, ontology_id, app_id, user, self.graph))
        self.adapter = _CachedSnapshot(FalkorReadAdapter(self.graph._graph(self.view.graph_key), graph_ontology_id=self.view.graph_key, max_objects=max_objects),
                                       self.view.graph_key)
        # One policy over every field the source touches, so the snapshot is projected once per request.
        self.policy = _ProjectOnce(build_query_policy(db, ontology_id, str(user.id), user.role, metadata_revision_id=access.context.metadata_revision_id,
                                                      requested_fields=tuple(sorted(set(fields))), preloaded_policies=policies_from_projection(access)))
        revision_id = access.context.metadata_revision_id
        self.context = ExecutionContext(ontology_id=ontology_id, revision_policy="pinned" if revision_id else "latest", revision_id=revision_id,
                                        consistency="snapshot", metadata_digest=self.view.metadata_digest, data_view_id=self.view.id,
                                        projection_token=self.view.source_manifest_digest)

    def core(self) -> QueryCore:
        return QueryCore(self.metadata, self.adapter, self.policy)

    def data_type(self, object_type: str, field: str) -> str:
        return self.metadata.resolve_property(self.metadata.resolve_type("object", object_type, "object_type"), field, "time_field").data_type

    def materialize(self, object_type: str, select: list[str], filters: list[dict], limit: int, order_by: str | None = None,
                    descending: bool = True) -> tuple[list[dict], bool]:
        order = [OrderBy(property=PropertyRef(api_name=order_by), direction="desc" if descending else "asc")] if order_by else []
        request = LoadObjectSetRequest(expression=expression(object_type, filters), context=self.context,
                                       read=ReadOptions(select=[PropertyRef(api_name=name) for name in select], order_by=order, page_size=200,
                                                        materialization_limit=MAX_OBJECTS))
        # Page until `limit` rows are in hand; Query Core's materialize() fails rather than stopping early.
        core, records, token, more = self.core(), [], None, True
        while more and len(records) < limit:
            page = core.load(request.model_copy(update={"read": request.read.model_copy(update={"page_token": token})}))
            records += page.objects
            more, token = page.page.has_more, page.page.next_page_token
        return [{**record.properties, "id": record.object_id} for record in records[:limit]], more or len(records) > limit

    def aggregate(self, object_type: str, filters: list[dict], group_by: list[str], metrics: list[dict]) -> list[dict]:
        request = AggregateObjectSetRequest(expression=expression(object_type, filters), context=self.context,
                                            group_by=[PropertyRef(api_name=name) for name in group_by],
                                            aggregations=[AggregationSpec(op=row["op"], property=PropertyRef(api_name=row["property"]) if row.get("property") else None,
                                                                          alias=row["key"]) for row in metrics])
        return [{key: plain(value) for key, value in row.items()} for row in self.core().aggregate(request).groups]

    def time_filter(self, object_type: str, filters: list[dict], field: str | None, time_range: str) -> tuple[list[dict] | None, dict]:
        """Filters that keep the last N days of ``field``, ending at the latest value in the (filtered) source; None when nothing has a time."""
        days = RANGE_DAYS.get(time_range)
        if not field or not days:
            return filters, {"range": "all", "field": field}
        latest = self.aggregate(object_type, filters, [], [{"key": "latest", "op": "max", "property": field}])
        anchor = latest[0].get("latest") if latest else None
        if anchor is None:
            return None, {"range": time_range, "field": field, "anchor": None}
        cutoff = shift(anchor, self.data_type(object_type, field), days)
        return filters + [{"field": field, "op": "gt", "value": cutoff}], {"range": time_range, "field": field, "anchor": anchor, "after": cutoff}


def expression(object_type: str, filters: list[dict]):
    base = BaseObjectSet(kind="base", type_ref=TypeRef(api_name=object_type))
    if not filters:
        return base
    return FilterObjectSet(kind="filter", input=base, where=AndFilter(kind="and", items=[
        ComparisonFilter(kind="comparison", property=PropertyRef(api_name=row["field"]), op=row.get("op", "eq"), value=row["value"]) for row in filters]))


def plain(value):
    """JSON-friendly aggregate values."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def shift(anchor, data_type: str, days: int):
    """``anchor`` minus N days, in the property's own representation (numbers are epoch seconds)."""
    if data_type in ("timestamp", "datetime"):
        return (datetime.fromisoformat(str(anchor).replace("Z", "+00:00")) - timedelta(days=days)).isoformat()
    if data_type == "date":
        return (date.fromisoformat(str(anchor)[:10]) - timedelta(days=days)).isoformat()
    return float(anchor) - days * 86_400


def resolve_object_set(db, ontology_id: str, app_id: str, source: dict, user, access, time_range: str = "all") -> dict:
    # The scan budget covers the whole snapshot; at most MAX_OBJECTS objects are returned.
    select = source.get("select") or object_type_properties(load_sql_metadata(db, ontology_id), source["object_type"])
    reader = _Reader(db, ontology_id, app_id, user, access, [*select, *[row["field"] for row in source.get("filters") or []],
                                                             *[source[key] for key in ("time_field", "order_by") if source.get(key)]])
    filters, window = reader.time_filter(source["object_type"], source.get("filters") or [], source.get("time_field"), time_range)
    objects, truncated = reader.materialize(source["object_type"], select, filters, int(source.get("limit") or MAX_OBJECTS), source.get("order_by"),
                                            source.get("descending", True)) if filters is not None else ([], False)
    truncated = truncated and not source.get("order_by")   # an ordered top N is complete by design
    return {"kind": "object_set", "status": "completed", "data_view_id": reader.view.id, "completed_at": reader.view.created_at.isoformat(),
            "outputs": {"objects": objects, "count": len(objects), "object_type": source["object_type"], "truncated": truncated, "time": window}}


def band(value, cuts: list, otherwise: str):
    """The label of the first cut whose minimum the value reaches (cuts are [minimum, label], highest first)."""
    if value is None:
        return None
    return next((label for minimum, label in cuts if value >= minimum), otherwise)


def resolve_aggregate(db, ontology_id: str, app_id: str, source: dict, user, access, time_range: str = "all") -> dict:
    join = source.get("join") or {}
    reader = _Reader(db, ontology_id, app_id, user, access, [*source["group_by"], *[row["property"] for row in source["metrics"] if row.get("property")],
                                                             *[row["field"] for row in source.get("filters") or []], *([source["time_field"]] if source.get("time_field") else []),
                                                             *join.get("select", []), *([join["key"]] if join.get("key") else [])])
    filters, window = reader.time_filter(source["object_type"], source.get("filters") or [], source.get("time_field"), time_range)
    groups = reader.aggregate(source["object_type"], filters, source["group_by"], source["metrics"]) if filters is not None else []
    if join:
        # Search Around by a shared key: each group picks up properties of its dimension object.
        dimension, _ = reader.materialize(join["object_type"], list({*join["select"], *([join["key"]] if join.get("key") else [])}), [], MAX_OBJECTS)
        by_key = {str(row[join["key"]] if join.get("key") else row["id"]): row for row in dimension}
        for row in groups:
            match = by_key.get(str(row.get(join["on"])))
            row.update({name: match.get(name) for name in join["select"]} if match else {name: None for name in join["select"]})
    for rule in source.get("bands") or []:
        for row in groups:
            row[rule["into"]] = band(row.get(rule["field"]), rule["cuts"], rule["otherwise"])
    return {"kind": "aggregate", "status": "completed", "data_view_id": reader.view.id, "completed_at": reader.view.created_at.isoformat(),
            "outputs": {"groups": groups, "count": len(groups), "object_type": source["object_type"], "time": window}}


def resolve_alerts(db, ontology_id: str, source: dict, user, time_range: str = "all") -> dict:
    from app.models.v2.rule_runtime import RuleResource
    from app.routers.v2.rule_runtime import list_alerts
    limit = int(source.get("limit") or MAX_ALERTS)
    rows, total = [], None
    while len(rows) < limit and (total is None or len(rows) < total):   # the Alert Center API pages at 500
        page = list_alerts(ontology_id, status=source.get("status"), subject_type=source.get("subject_type"), limit=min(500, limit - len(rows)),
                           offset=len(rows), db=db, user=user)
        rows += page["alerts"]
        total = page["total"]
        if not page["alerts"]:
            break
    names = dict(db.query(RuleResource.id, RuleResource.display_name).filter_by(ontology_id=ontology_id).all())
    alerts = [{**row, "rule_name": names.get(row["rule_id"], ""), "subject_type": row["subject"]["object_type"],
               "subject_id": row["subject"]["object_id"]} for row in rows]
    field = source.get("time_field") or "window_end"
    window = {"range": "all", "field": field}
    if days := RANGE_DAYS.get(time_range):
        stamp = lambda row: datetime.fromisoformat(str(row[field]).replace("Z", "+00:00")) if row.get(field) else None  # noqa: E731
        times = [stamp(row) for row in alerts]
        anchor = max((time for time in times if time), default=None)
        alerts = [row for row, time in zip(alerts, times) if time and anchor and time > anchor - timedelta(days=days)]
        window = {"range": time_range, "field": field, "anchor": anchor.isoformat() if anchor else None,
                  "after": (anchor - timedelta(days=days)).isoformat() if anchor else None}
    return {"kind": "alerts", "status": "completed", "completed_at": datetime.now(timezone.utc).isoformat(),
            "outputs": {"alerts": alerts, "count": len(alerts), "total": total or 0, "truncated": (total or 0) > len(rows), "time": window}}
