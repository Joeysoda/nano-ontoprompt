"""One-click binding: turn an object type into a ready Workshop page.

1. ``profile`` reads the type's objects from a snapshot, as the viewer may see
   them, and gives each property a role: time, measure, flag (0/1), category,
   key (its values are ids of another object type: a dimension), identifier.
   A dimension with latitude/longitude properties can be drawn on a map.
2. A *plan* chooses what to show: KPIs, one scene (map or ranking per
   dimension), breakdowns per category, a table of the latest records.  Plans
   come from fixed rules (``rule_plan``) or from an LLM (``llm_plan``), which
   only chooses; ``check_plan`` drops every choice that names a field the
   profile does not offer for that use.
3. ``compile_plan`` deterministically turns a plan into data sources, widgets
   and a layout on the "KPI on top + main" template.  Both modes share it, so
   a model cannot produce a page that does not run.
"""
from __future__ import annotations

import json
import os
from collections import Counter

import httpx

from app.models.entity import Entity
from app.services.v2.workshop_sources import _Reader, object_type_properties

NUMERIC = {"double", "float", "integer", "long", "decimal", "short"}
TIMES = {"timestamp", "datetime", "date"}
LAT, LNG = ("latitude", "lat"), ("longitude", "lng", "lon")
MAX_CATEGORIES = 200   # a category has at most this many values and at most one per two objects
FORMATS = ("number", "money", "percent")


class AutobindError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


# --- 1. profile --------------------------------------------------------------------------------------------------

def _labels(db, ontology_id: str) -> dict[str, dict]:
    """Display names an ontology designer gave each type and property (falling back to the API names)."""
    out = {}
    for entity in db.query(Entity).filter_by(ontology_id=ontology_id).all():
        api = str(entity.canonical_id or "").split(":", 1)[1] if str(entity.canonical_id or "").startswith("ontology:") else entity.name_en or entity.name_cn
        definitions = (entity.properties or {}).get("property_definitions", []) if isinstance(entity.properties, dict) else []
        out[api] = {"label": entity.name_cn if entity.name_cn and entity.name_cn != api else api,
                    "properties": {row["id"]: row.get("name") or row.get("label") or row["id"] for row in definitions if isinstance(row, dict) and row.get("id")}}
    return out


def profile(db, ontology_id: str, object_type: str, user, access) -> dict:
    reader = _Reader(db, ontology_id, f"autobind:{ontology_id}", user, access, object_type_properties(_metadata(db, ontology_id), object_type), reuse_any=True)
    graph = reader.policy.apply_projection(reader.adapter.read(reader.view.graph_key))
    ids_by_type: dict[str, set] = {}
    rows_by_type: dict[str, list[dict]] = {}
    for (kind, object_id), props in graph.objects.items():
        ids_by_type.setdefault(kind, set()).add(str(object_id))
        rows_by_type.setdefault(kind, []).append(props)
    rows = rows_by_type.get(object_type, [])
    if not rows:
        raise AutobindError("no_objects", f"{object_type} has no objects you can see")
    labels = _labels(db, ontology_id)
    names = labels.get(object_type, {"label": object_type, "properties": {}})
    fields = []
    for name in object_type_properties(reader.metadata, object_type):
        data_type = reader.metadata.resolve_property(reader.metadata.resolve_type("object", object_type, "type"), name, "field").data_type
        values = [row.get(name) for row in rows if row.get(name) not in (None, "")]
        distinct = len({json.dumps(value, sort_keys=True, default=str) for value in values})
        field = {"name": name, "label": names["properties"].get(name, name), "data_type": data_type, "filled": round(len(values) / len(rows), 3),
                 "distinct": distinct, "samples": [str(value)[:40] for value in list(dict.fromkeys(map(str, values)))[:3]]}
        field["role"], field["dimension"] = _role(name, data_type, values, distinct, len(rows), ids_by_type, object_type)
        fields.append(field)
    dimensions = []
    for field in fields:
        if field["role"] != "key":
            continue
        target = field["dimension"]
        props = object_type_properties(reader.metadata, target)
        lat = next((p for p in props if p.lower() in LAT), None)
        lng = next((p for p in props if p.lower() in LNG), None)
        text = [p for p in props if reader.metadata.resolve_property(reader.metadata.resolve_type("object", target, "type"), p, "f").data_type == "string"]
        name_field = next((p for p in text if p.lower() in ("name", "city", "title", "label")), text[0] if text else None)
        dimensions.append({"field": field["name"], "object_type": target, "label": labels.get(target, {}).get("label", target),
                           "name_field": name_field, "lat": lat, "lng": lng, "count": len(ids_by_type.get(target, ()))})
    return {"object_type": object_type, "label": names["label"], "count": len(rows), "fields": fields, "dimensions": dimensions,
            "time_field": next((f["name"] for f in fields if f["role"] == "time"), None)}


def _metadata(db, ontology_id):
    from app.services.v2.object_query.metadata import load_sql_metadata
    return load_sql_metadata(db, ontology_id)


def _role(name, data_type, values, distinct, total, ids_by_type, own_type) -> tuple[str, str | None]:
    if data_type in TIMES:
        return "time", None
    if data_type in NUMERIC:
        numbers = {float(v) for v in values if isinstance(v, (int, float))}
        return ("flag", None) if numbers and numbers <= {0.0, 1.0} else ("measure", None)
    if data_type == "boolean":
        return "flag", None
    if data_type != "string" or not values:
        return "other", None
    sample = [str(v) for v in values[:500]]
    for kind, ids in ids_by_type.items():   # values that are ids of another type: a dimension key
        if kind != own_type and sum(v in ids for v in sample) >= 0.9 * len(sample):
            return "key", kind
    if 2 <= distinct <= MAX_CATEGORIES and distinct <= total / 2:
        return "category", None
    return "identifier", None


# --- 2. plans ----------------------------------------------------------------------------------------------------

def _metric_label(metric: dict, prof: dict) -> str:
    field = next((f for f in prof["fields"] if f["name"] == metric.get("field")), None)
    if metric["op"] == "count":
        return f"{prof['label']}数"
    return f"{field['label'] if field else metric.get('field')}{'合计' if metric['op'] == 'sum' else '比例' if field and field['role'] == 'flag' else '平均'}"


def rule_plan(prof: dict) -> dict:
    """Fixed rules: count + every measure (sum) + every flag (rate); the first dimension as the scene; categories as breakdowns."""
    by_role = lambda role: [f for f in prof["fields"] if f["role"] == role]  # noqa: E731
    kpis = [{"op": "count", "field": None, "format": "number"}]
    kpis += [{"op": "sum", "field": f["name"], "format": "number"} for f in by_role("measure")][:3]
    kpis += [{"op": "avg", "field": f["name"], "format": "percent"} for f in by_role("flag")][:2]
    main = kpis[1] if len(kpis) > 1 and kpis[1]["op"] == "sum" else kpis[0]
    scene = None
    if prof["dimensions"]:
        dim = next((d for d in prof["dimensions"] if d["lat"] and d["lng"]), prof["dimensions"][0])
        scene = {"kind": "map" if dim["lat"] and dim["lng"] else "rank", "dimension": dim["field"], "metric": main}
    breakdowns = []
    for field in sorted(by_role("category"), key=lambda f: f["distinct"])[:4]:
        breakdowns.append({"kind": "donut" if field["distinct"] <= 6 else "rank", "field": field["name"], "metric": {"op": "count", "field": None, "format": "number"}})
    table = [f["name"] for f in prof["fields"] if f["role"] in ("time", "category", "measure", "flag", "key")][:6]
    plan = {"page_title": f"{prof['label']}总览", "kpis": kpis, "scene": scene, "breakdowns": breakdowns, "table": table}
    _titles(plan, prof)
    return plan


def _titles(plan: dict, prof: dict):
    """Default titles from display names, for items that have none."""
    label = lambda name: next((f["label"] for f in prof["fields"] if f["name"] == name), name)  # noqa: E731
    metrics = plan["kpis"] + ([plan["scene"]["metric"]] if plan.get("scene") else []) + [item["metric"] for item in plan["breakdowns"]]
    for metric in metrics:
        metric.setdefault("label", _metric_label(metric, prof))
    if plan.get("scene"):
        scene = plan["scene"]
        dim = next(d for d in prof["dimensions"] if d["field"] == scene["dimension"])
        scene.setdefault("title", f"按{dim['label']}的{_metric_label(scene['metric'], prof)}")
    for item in plan["breakdowns"]:
        item.setdefault("title", f"按{label(item['field'])}的{_metric_label(item['metric'], prof)}")
    plan.setdefault("table_title", f"最新{prof['label']}" if prof["time_field"] else f"{prof['label']}明细")   # "latest" only when there is a time


METRIC_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["op", "field", "format", "label"],
                 "properties": {"op": {"type": "string", "enum": ["count", "sum", "avg"]}, "field": {"type": ["string", "null"]},
                                "format": {"type": "string", "enum": list(FORMATS)}, "label": {"type": "string"}}}
PLAN_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["page_title", "kpis", "scene", "breakdowns", "table", "table_title", "reasoning"],
               "properties": {
                   "page_title": {"type": "string"}, "reasoning": {"type": "string"},
                   "kpis": {"type": "array", "items": METRIC_SCHEMA},
                   "scene": {"anyOf": [{"type": "null"}, {"type": "object", "additionalProperties": False, "required": ["kind", "dimension", "metric", "title"],
                                                         "properties": {"kind": {"type": "string", "enum": ["map", "rank"]}, "dimension": {"type": "string"},
                                                                        "metric": METRIC_SCHEMA, "title": {"type": "string"}}}]},
                   "breakdowns": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["kind", "field", "metric", "title"],
                                                            "properties": {"kind": {"type": "string", "enum": ["donut", "rank"]}, "field": {"type": "string"},
                                                                           "metric": METRIC_SCHEMA, "title": {"type": "string"}}}},
                   "table": {"type": "array", "items": {"type": "string"}}, "table_title": {"type": "string"}}}
SYSTEM = """You design one dashboard page for business users of an ontology platform, in Simplified Chinese.
You get a profile of one object type: each property with its data type, role (time, measure, flag = 0/1, category, key = id of a dimension object, identifier), number of distinct values and samples, and the dimensions reachable through keys (with map coordinates if any).
Choose: 2-5 KPIs (count, or sum/avg of a measure; avg of a flag is a rate, format percent), at most one scene (a map of a dimension with coordinates, or a ranking of a dimension), 1-4 breakdowns by category or key fields (donut for few values, rank for many), and up to 6 table columns.
Only use property names that appear in the profile, only sum/avg measures or flags, only break down by category or key fields. Use money format only for amounts of money.
Write short, specific Chinese titles a business user understands (no raw property names). Explain your choices in one or two sentences in `reasoning`."""


def llm_plan(prof: dict, *, client: httpx.Client | None = None) -> tuple[dict, str]:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise AutobindError("llm_unavailable", "OPENAI_API_KEY is not configured", 503)
    model = os.environ.get("AUTOBIND_MODEL") or os.environ.get("EXPLANATION_MODEL") or "gpt-4.1"
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    body = {"model": model, "temperature": 0.2, "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": json.dumps(prof, ensure_ascii=False)}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "dashboard_plan", "strict": True, "schema": PLAN_SCHEMA}}}
    http = client or httpx.Client(timeout=120)
    try:
        response = http.post(f"{base}/chat/completions", json=body, headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError as exc:
        raise AutobindError("llm_unavailable", f"Model call failed: {type(exc).__name__}", 502) from exc
    if response.status_code != 200:
        raise AutobindError("llm_error", f"Model returned HTTP {response.status_code}", 502)
    payload = response.json()
    return json.loads(payload["choices"][0]["message"]["content"]), payload.get("model", model)


def check_plan(plan: dict, prof: dict) -> tuple[dict, list[str]]:
    """Keep only choices the profile supports; report every dropped one."""
    fields = {f["name"]: f for f in prof["fields"]}
    dims = {d["field"]: d for d in prof["dimensions"]}
    dropped = []

    def metric_ok(metric, where):
        if metric["op"] == "count":
            metric["field"] = None
            return True
        field = fields.get(metric.get("field") or "")
        if field and field["role"] in ("measure", "flag"):
            return True
        dropped.append(f"{where}: {metric['op']}({metric.get('field')}) 不是可汇总的数值字段")
        return False

    clean = {"page_title": plan.get("page_title") or f"{prof['label']}总览", "reasoning": plan.get("reasoning", "")}
    if plan.get("table_title"):
        clean["table_title"] = plan["table_title"]
    clean["kpis"] = [m for i, m in enumerate(plan.get("kpis") or []) if metric_ok(m, f"指标 {i + 1}")][:6] or [{"op": "count", "field": None, "format": "number"}]
    scene = plan.get("scene")
    if scene and (scene.get("dimension") not in dims or (scene["kind"] == "map" and not (dims[scene["dimension"]]["lat"] and dims[scene["dimension"]]["lng"]))):
        dropped.append(f"场景: {scene.get('kind')}({scene.get('dimension')}) 不是可用的维度" + ("或没有坐标" if scene.get("kind") == "map" else ""))
        scene = None
    clean["scene"] = scene if scene and metric_ok(scene["metric"], "场景") else None
    clean["breakdowns"] = []
    for i, item in enumerate(plan.get("breakdowns") or []):
        field = fields.get(item.get("field") or "")
        if not field or field["role"] not in ("category", "key"):
            dropped.append(f"分组 {i + 1}: {item.get('field')} 不是类别或维度字段")
        elif metric_ok(item["metric"], f"分组 {i + 1}"):
            clean["breakdowns"].append(item)
    clean["table"] = [name for name in plan.get("table") or [] if name in fields][:6]
    dropped += [f"表格: {name} 不存在" for name in plan.get("table") or [] if name not in fields]
    _titles(clean, prof)
    return clean, dropped


# --- 3. compile ----------------------------------------------------------------------------------------------------

def _place(taken: list[dict], w: int, h: int, cols: int = 24) -> dict:
    bottom = max([0] + [item["y"] + item["h"] for item in taken])
    for y in range(bottom + 1):
        for x in range(cols - w + 1):
            if not any(x < i["x"] + i["w"] and i["x"] < x + w and y < i["y"] + i["h"] and i["y"] < y + h for i in taken):
                return {"x": x, "y": y, "w": w, "h": h}
    return {"x": 0, "y": bottom, "w": w, "h": h}


def compile_plan(plan: dict, prof: dict, prefix: str) -> dict:
    """Data sources, widgets and layout for one page on the 'KPI on top + main' template."""
    t, time_field = prof["object_type"], prof["time_field"]
    label = lambda name: next((f["label"] for f in prof["fields"] if f["name"] == name), name)  # noqa: E731
    sources, widgets, layout = [], [], []

    def metric(m, key):
        return {"key": key, "op": m["op"], **({"property": m["field"]} if m["op"] != "count" else {})}

    def add(widget, region, w, h):
        widgets.append({**widget, "region": region, "path": widget.get("path", "groups"), "on_select": None})
        layout.append({"i": widget["id"], **_place([i for i, wd in zip(layout, widgets) if wd["region"] == region], w, h)})

    sources.append({"id": f"{prefix}totals", "kind": "aggregate", "label": f"{prof['label']}（合计）", "object_type": t, "group_by": [], "time_field": time_field,
                    "metrics": [metric(m, f"k{i}") for i, m in enumerate(plan["kpis"])]})
    add({"id": f"{prefix}kpis", "type": "kpi_strip", "title": "", "source": f"{prefix}totals",
         "props": {"items": [{"label": m["label"], "path": f"groups.0.k{i}", "format": m["format"]} for i, m in enumerate(plan["kpis"])]}}, "kpi", 24, 3)
    if scene := plan.get("scene"):
        dim = next(d for d in prof["dimensions"] if d["field"] == scene["dimension"])
        select = [p for p in (dim["name_field"], dim["lat"], dim["lng"]) if p]
        sources.append({"id": f"{prefix}by_{dim['field']}", "kind": "aggregate", "label": f"按{dim['label']}汇总", "object_type": t, "group_by": [dim["field"]],
                        "time_field": time_field, "metrics": [metric(scene["metric"], "value")],
                        "join": {"object_type": dim["object_type"], "on": dim["field"], "select": select} if select else None})
        name = dim["name_field"] or dim["field"]
        if scene["kind"] == "map":
            add({"id": f"{prefix}map", "type": "geo_map", "title": scene["title"], "source": f"{prefix}by_{dim['field']}",
                 "props": {"lat_field": dim["lat"], "lng_field": dim["lng"], "label_field": name, "size_field": "value", "key_field": dim["field"]}}, "main", 14, 12)
        else:
            add({"id": f"{prefix}scene", "type": "rank_bars", "title": scene["title"], "source": f"{prefix}by_{dim['field']}",
                 "props": {"label_field": name, "value_field": "value", "max_rows": 10, "format": scene["metric"]["format"]}}, "main", 14, 10)
    for i, item in enumerate(plan["breakdowns"]):
        source = f"{prefix}by_{item['field']}_{i}"
        dim = next((d for d in prof["dimensions"] if d["field"] == item["field"]), None)
        join = {"object_type": dim["object_type"], "on": item["field"], "select": [dim["name_field"]]} if dim and dim["name_field"] else None
        sources.append({"id": source, "kind": "aggregate", "label": f"按{label(item['field'])}汇总", "object_type": t, "group_by": [item["field"]],
                        "time_field": time_field, "metrics": [metric(item["metric"], "value")], "join": join})
        category = dim["name_field"] if join else item["field"]
        if item["kind"] == "donut":
            add({"id": f"{prefix}b{i}", "type": "donut_chart", "title": item["title"], "source": source,
                 "props": {"category_field": category, "value_field": "value", "center_label": item["metric"]["label"]}}, "main", 10, 7)
        else:
            add({"id": f"{prefix}b{i}", "type": "rank_bars", "title": item["title"], "source": source,
                 "props": {"label_field": category, "value_field": "value", "max_rows": 10, "format": item["metric"]["format"]}}, "main", 10, 7)
    if plan["table"]:
        sources.append({"id": f"{prefix}latest", "kind": "object_set", "label": plan["table_title"], "object_type": t, "select": plan["table"],
                        "time_field": time_field, "limit": 20, **({"order_by": time_field} if time_field else {})})
        add({"id": f"{prefix}table", "type": "table", "title": plan["table_title"], "source": f"{prefix}latest", "path": "objects",
             "props": {"columns": [{"label": label(n), "field": n, "format": "date" if n == time_field else "text"} for n in plan["table"]]}}, "main", 24, 8)
    return {"data_sources": sources, "page": {"id": f"{prefix}page", "title": plan["page_title"], "template": "kpi_top", "widgets": widgets, "layout": layout}}
