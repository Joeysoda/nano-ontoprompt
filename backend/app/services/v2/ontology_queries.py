"""Ontology queries: publish, check compatibility, resolve versions, run.

A query's definition is one Workshop data source (object set, aggregate or
alerts) plus declared parameters; an equality parameter becomes a filter at
run time, the time range and limit are always available.  Its output schema
(shape + typed, labelled columns) is computed when a version is published and
is what deterministic binding and API/MCP clients rely on.
"""
from __future__ import annotations

import re
from copy import deepcopy

from app.models.entity import Entity
from app.models.v2.ontology_query import OntologyQuery, OntologyQueryVersion
from app.services.v2 import workshop_sources
from app.services.v2.object_query.metadata import load_sql_metadata
from app.services.v2.object_query.normalize import stable_hash

SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
NUMERIC = {"double", "float", "integer", "long", "decimal", "short"}
TIMES = {"timestamp", "datetime", "date"}
LAT, LNG = {"latitude", "lat"}, {"longitude", "lng", "lon"}
ALERT_COLUMNS = [("id", "string", "identifier", "告警"), ("rule_name", "string", "dimension", "规则"), ("severity", "string", "dimension", "严重度"),
                 ("status", "string", "dimension", "状态"), ("subject_type", "string", "dimension", "对象类型"), ("subject_id", "string", "dimension", "对象"),
                 ("window_end", "timestamp", "time", "时间")]


class QueryError(Exception):
    def __init__(self, code: str, message: str, status: int = 422, details: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.status, self.details = code, message, status, details or {}


# --- versions -------------------------------------------------------------------------------------------------------

def parse(version: str) -> tuple[int, int, int]:
    match = SEMVER.match(version or "")
    if not match:
        raise QueryError("invalid_version", f"Version {version!r} is not X.Y.Z")
    return tuple(int(part) for part in match.groups())


def satisfies(version: str, wanted: str) -> bool:
    """``^1`` = any 1.x.y (the default for consumers), ``1.2.3`` = exactly that, ``latest`` = anything."""
    if wanted in ("", "latest"):
        return True
    if wanted.startswith("^"):
        return parse(version)[0] == int(wanted[1:])
    return version == wanted


def pick(db, query: OntologyQuery, wanted: str) -> OntologyQueryVersion:
    rows = db.query(OntologyQueryVersion).filter_by(query_id=query.id).all()
    matching = [row for row in rows if satisfies(row.version, wanted)]
    if not matching:
        raise QueryError("version_not_found", f"No version of {query.api_name} matches {wanted}", 404)
    return max(matching, key=lambda row: (row.major, row.minor, row.patch))


# --- output schema ----------------------------------------------------------------------------------------------------

def _labels(db, ontology_id: str) -> dict[str, dict[str, str]]:
    out = {}
    for entity in db.query(Entity).filter_by(ontology_id=ontology_id).all():
        canonical = str(entity.canonical_id or "")
        api = canonical.split(":", 1)[1] if canonical.startswith("ontology:") else entity.name_en or entity.name_cn
        definitions = (entity.properties or {}).get("property_definitions", []) if isinstance(entity.properties, dict) else []
        out[api] = {row["id"]: row.get("name") or row["id"] for row in definitions if isinstance(row, dict) and row.get("id")}
    return out


def _role(name: str, data_type: str) -> str:
    if data_type in TIMES:
        return "time"
    if name.lower() in LAT:
        return "latitude"
    if name.lower() in LNG:
        return "longitude"
    return "measure" if data_type in NUMERIC else "dimension"


def output_schema(db, ontology_id: str, source: dict) -> dict:
    """Shape and columns of what the source returns: table, single (one row of metrics), 2d / 3d aggregation."""
    metadata, labels = load_sql_metadata(db, ontology_id), _labels(db, ontology_id)

    def column(object_type, name, role=None, label=None):
        data_type = metadata.resolve_property(metadata.resolve_type("object", object_type, "type"), name, "column").data_type
        return {"name": name, "type": data_type, "role": role or _role(name, data_type), "label": label or labels.get(object_type, {}).get(name, name)}

    kind = source["kind"]
    if kind == "alerts":
        return {"shape": "table", "path": "alerts", "columns": [{"name": n, "type": t, "role": r, "label": l} for n, t, r, l in ALERT_COLUMNS]}
    if kind == "object_set":
        names = source.get("select") or workshop_sources.object_type_properties(metadata, source["object_type"])
        return {"shape": "table", "path": "objects", "columns": [{"name": "id", "type": "string", "role": "identifier", "label": "编号"}]
                + [column(source["object_type"], name) for name in names]}
    columns = [column(source["object_type"], name, "dimension") for name in source["group_by"]]
    if join := source.get("join"):
        columns += [column(join["object_type"], name) for name in join["select"]]
        for col in columns[len(source["group_by"]):]:
            if col["role"] in ("dimension",) and col["name"].lower() in ("name", "display_name", "city", "title", "label"):
                col["role"] = "name"
    for band in source.get("bands") or []:
        columns.append({"name": band["into"], "type": "string", "role": "category", "label": band["into"]})
    for metric in source["metrics"]:
        base = labels.get(source["object_type"], {}).get(metric.get("property") or "", metric.get("property") or "")
        label = {"count": "数量", "sum": f"{base}合计", "avg": f"{base}平均", "min": f"{base}最小", "max": f"{base}最大", "exact_distinct": f"{base}去重数"}[metric["op"]]
        columns.append({"name": metric["key"], "type": "double", "role": "metric", "label": metric.get("label") or label})
    shape = {0: "single", 1: "2d", 2: "3d"}.get(len(source["group_by"]), "table")
    return {"shape": shape, "path": "groups", "columns": columns}


def compatibility(previous: OntologyQueryVersion | None, definition: dict, schema: dict) -> tuple[list[str], list[str]]:
    """(breaking changes, compatible additions) against the previous version, following semver for functions."""
    if previous is None:
        return [], []
    breaking, added = [], []
    old = {c["name"]: c for c in previous.output_schema["columns"]}
    new = {c["name"]: c for c in schema["columns"]}
    breaking += [f"删除了输出列 {name}" for name in old if name not in new]
    breaking += [f"输出列 {name} 的类型从 {old[name]['type']} 改成了 {new[name]['type']}" for name in old if name in new and new[name]["type"] != old[name]["type"]]
    if previous.output_schema["shape"] != schema["shape"]:
        breaking.append(f"结果形状从 {previous.output_schema['shape']} 改成了 {schema['shape']}")
    old_params = {p["name"]: p for p in previous.definition.get("params", [])}
    new_params = {p["name"]: p for p in definition.get("params", [])}
    breaking += [f"删除了参数 {name}" for name in old_params if name not in new_params]
    breaking += [f"新增了必填参数 {name}" for name, p in new_params.items() if name not in old_params and p.get("required")]
    added += [f"新增输出列 {name}" for name in new if name not in old]
    added += [f"新增可选参数 {name}" for name, p in new_params.items() if name not in old_params and not p.get("required")]
    return breaking, added


def next_version(previous: OntologyQueryVersion | None, bump: str, breaking: list[str], added: list[str]) -> str:
    if previous is None:
        return "1.0.0"
    if breaking and bump not in ("major", "auto"):
        raise QueryError("breaking_change", "这些改动不兼容，必须发布新的主版本", 409, {"breaking": breaking})
    major, minor, patch = previous.major, previous.minor, previous.patch
    level = bump if bump != "auto" else "major" if breaking else "minor" if added else "patch"
    return {"major": f"{major + 1}.0.0", "minor": f"{major}.{minor + 1}.0", "patch": f"{major}.{minor}.{patch + 1}"}[level]


def publish(db, query: OntologyQuery, definition: dict, schema: dict, bump: str, note: str, user) -> tuple[OntologyQueryVersion, list[str]]:
    previous = pick(db, query, "latest") if query.latest_version else None
    breaking, added = compatibility(previous, definition, schema)
    version = next_version(previous, bump, breaking, added)
    major, minor, patch = parse(version)
    row = OntologyQueryVersion(query_id=query.id, version=version, major=major, minor=minor, patch=patch, definition=definition,
                               output_schema=schema, definition_digest=stable_hash(definition), change_note=note, created_by=user.id)
    db.add(row)
    query.latest_version = version
    return row, breaking


# --- run --------------------------------------------------------------------------------------------------------------

def bind(definition: dict, params: dict) -> dict:
    """The source with parameter values applied as equality filters."""
    source = deepcopy(definition["source"])
    declared = {p["name"]: p for p in definition.get("params", [])}
    if unknown := set(params) - set(declared):
        raise QueryError("unknown_parameter", f"Unknown parameters {sorted(unknown)}")
    filters = list(source.get("filters") or [])
    for name, spec in declared.items():
        value = params.get(name, spec.get("default"))
        if value in (None, ""):
            if spec.get("required"):
                raise QueryError("missing_parameter", f"Parameter {name} is required")
            continue
        filters.append({"field": spec["field"], "op": "eq", "value": value})
    if source["kind"] == "alerts":
        for row in filters:   # alerts are filtered by subject type through their own options
            if row["field"] == "subject_type":
                source["subject_type"] = row["value"]
        return source
    source["filters"] = filters
    return source


def run(db, ontology_id: str, version: OntologyQueryVersion, params: dict, time_range: str, user, access, limit: int | None = None) -> dict:
    source = bind(version.definition, params or {})
    if limit and source["kind"] != "aggregate":
        source["limit"] = min(int(limit), int(source.get("limit") or limit))
    key = f"query:{version.query_id}"
    if source["kind"] == "object_set":
        resolved = workshop_sources.resolve_object_set(db, ontology_id, key, source, user, access, time_range, reuse_any=True)
    elif source["kind"] == "aggregate":
        resolved = workshop_sources.resolve_aggregate(db, ontology_id, key, source, user, access, time_range, reuse_any=True)
    else:
        resolved = workshop_sources.resolve_alerts(db, ontology_id, source, user, time_range)
    return {"version": version.version, "output_schema": version.output_schema, **resolved}
