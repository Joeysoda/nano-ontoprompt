"""Deterministic adapter for the official ICEWS 2023 event slice.

The adapter intentionally keeps ICEWS dates as calendar dates.  ICEWS ships a
day-level ``Event Date`` and does not provide a time-of-day or timezone.  The
normalised rows therefore expose ``time_kind=instant`` and
``time_precision=day`` without turning a date into a made-up midnight UTC
timestamp.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import date
from typing import Any, Iterable


ICEWS_SOURCE_ID = "icews_2023_demo"
ICEWS_DATASET_NAME = "ICEWS 官方事件 2023-01-01—2023-01-03"
ICEWS_DOI = "10.7910/DVN/28075"
ICEWS_FILE_ID = "7070776"
ICEWS_FILENAME = "20230106-icews-events.tab.zip"
ICEWS_DOWNLOAD_URL = f"https://dataverse.harvard.edu/api/access/datafile/{ICEWS_FILE_ID}"
ICEWS_SHA256 = "39adf9bb3f9b263763f5d46f224c578de0eda2ca5f6a1b843004b6aff29e62e5"
ICEWS_EXPECTED_ROWS = 3155

ICEWS_COLUMNS = (
    "Event ID", "Event Date", "Source Name", "Source Sectors",
    "Source Country", "Event Text", "CAMEO Code", "Intensity",
    "Target Name", "Target Sectors", "Target Country", "Story ID",
    "Sentence Number", "Publisher", "City", "District", "Province",
    "Country", "Latitude", "Longitude",
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_icews_tsv(data: bytes | str) -> list[dict[str, Any]]:
    """Parse an ICEWS tab-separated file without pandas.

    Keeping this parser small makes it usable by the installer, worker and
    unit tests, and avoids loading a future larger ICEWS export into a browser
    request.  Empty lines are ignored and all field names are retained.
    """
    text = data.decode("utf-8-sig", errors="replace") if isinstance(data, bytes) else data
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    if not reader.fieldnames:
        raise ValueError("ICEWS file has no header")
    missing = [column for column in ICEWS_COLUMNS if column not in reader.fieldnames]
    if missing:
        raise ValueError(f"ICEWS file is missing columns: {', '.join(missing)}")
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(reader):
        if any(str(value or "").strip() for value in row.values()):
            item = dict(row)
            item["_source_row_index"] = index
            rows.append(item)
    return rows


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _date(value: Any) -> str:
    text = _clean(value)
    if not text:
        raise ValueError("missing Event Date")
    # ``date.fromisoformat`` is deliberately strict: no timezone or time-of-
    # day is invented for this day-precision source.
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValueError(f"invalid Event Date: {text}") from exc


def _number(value: Any) -> float | None:
    text = _clean(value)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def normalize_icews_rows(rows: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate and normalise rows, returning valid rows and issue records."""
    normalized: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for index, raw in enumerate(rows):
        row = {str(key): value for key, value in dict(raw).items()}
        try:
            event_id = _clean(row.get("Event ID"))
            if not event_id:
                raise ValueError("missing Event ID")
            event_date = _date(row.get("Event Date"))
            source_name = _clean(row.get("Source Name"))
            target_name = _clean(row.get("Target Name"))
            if not source_name or not target_name:
                raise ValueError("missing Source Name or Target Name")
            cameo = _clean(row.get("CAMEO Code"))
            if not cameo or not re.fullmatch(r"\d+(?:\.\d+)?", cameo):
                raise ValueError(f"unknown CAMEO Code: {cameo or '<empty>'}")
        except ValueError as exc:
            issues.append({"row_index": index, "error": str(exc), "event_id": _clean(row.get("Event ID"))})
            continue

        normalized.append({
            **row,
            "event_id": event_id,
            "event_time": event_date,
            "time_kind": "instant",
            "time_precision": "day",
            "timezone": None,
            "source_name": source_name,
            "target_name": target_name,
            "source_country": _clean(row.get("Source Country")),
            "target_country": _clean(row.get("Target Country")),
            "event_type": _clean(row.get("Event Text")),
            "cameo_code": cameo,
            "intensity": _number(row.get("Intensity")),
            "source_row_index": row.get("_source_row_index", index),
        })
    return normalized, issues


def _slug(value: Any) -> str:
    return re.sub(r"\s+", " ", _clean(value)).casefold()


def actor_id(name: Any, country: Any = "") -> str:
    digest = hashlib.sha256(f"{_slug(name)}|{_slug(country)}".encode("utf-8")).hexdigest()[:20]
    return f"ICEWS:Actor:{digest}"


def country_id(country: Any) -> str:
    return f"ICEWS:Country:{hashlib.sha256(_slug(country).encode('utf-8')).hexdigest()[:16]}"


def location_id(row: dict[str, Any]) -> str:
    parts = [row.get(key, "") for key in ("City", "District", "Province", "Country", "Latitude", "Longitude")]
    digest = hashlib.sha256("|".join(_slug(value) for value in parts).encode("utf-8")).hexdigest()[:20]
    return f"ICEWS:Location:{digest}"


def build_icews_instances(rows: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build stable ICEWS ontology instances and fixed semantic relations."""
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[str, dict[str, Any]] = {}

    def add_node(node_id: str, entity_type: str, properties: dict[str, Any]) -> None:
        existing = nodes.get(node_id)
        if existing is None:
            nodes[node_id] = {"id": node_id, "entity_type": entity_type, "properties": properties}
        else:
            existing["properties"].update({k: v for k, v in properties.items() if v not in (None, "")})

    def add_edge(source: str, relation: str, target: str, row: dict[str, Any]) -> None:
        key = f"{source}:{relation}:{target}"
        edges.setdefault(key, {
            "id": key,
            "source": source,
            "target": target,
            "type": relation,
            "properties": {
                "event_time": row.get("event_time"),
                "time_kind": "instant",
                "time_precision": "day",
                "event_id": row.get("event_id"),
                "source_row_index": row.get("source_row_index"),
            },
        })

    for row in rows:
        event_id = str(row["event_id"])
        event_node = f"ICEWS:Event:{event_id}"
        source = actor_id(row.get("source_name"), row.get("source_country"))
        target = actor_id(row.get("target_name"), row.get("target_country"))
        category = f"ICEWS:CAMEO:{row.get('cameo_code')}"
        event_props = {
            "event_id": event_id,
            "event_time": row.get("event_time"),
            "time_kind": "instant",
            "time_precision": "day",
            "event_type": row.get("event_type"),
            "cameo_code": row.get("cameo_code"),
            "intensity": row.get("intensity"),
            "story_id": _clean(row.get("Story ID")),
            "sentence_number": _clean(row.get("Sentence Number")),
            "publisher": _clean(row.get("Publisher")),
            "source_country": _clean(row.get("source_country")),
            "target_country": _clean(row.get("target_country")),
            "source_name": _clean(row.get("source_name")),
            "target_name": _clean(row.get("target_name")),
            "source_row_index": row.get("source_row_index"),
        }
        add_node(event_node, "InteractionEvent", event_props)
        add_node(source, "Actor", {"name": row.get("source_name"), "country": row.get("source_country"), "role": "source"})
        add_node(target, "Actor", {"name": row.get("target_name"), "country": row.get("target_country"), "role": "target"})
        add_node(category, "EventCategory", {"cameo_code": row.get("cameo_code"), "label": row.get("event_type")})

        location = location_id(row)
        location_props = {
            "city": _clean(row.get("City")), "district": _clean(row.get("District")),
            "province": _clean(row.get("Province")), "country": _clean(row.get("Country")),
            "latitude": _clean(row.get("Latitude")), "longitude": _clean(row.get("Longitude")),
        }
        add_node(location, "Location", location_props)

        for actor, country in ((source, row.get("source_country")), (target, row.get("target_country"))):
            if _clean(country):
                cid = country_id(country)
                add_node(cid, "Country", {"name": _clean(country)})
                add_edge(actor, "ASSOCIATED_WITH", cid, row)
        add_edge(source, "INITIATED", event_node, row)
        add_edge(event_node, "TARGETED", target, row)
        add_edge(event_node, "CLASSIFIED_AS", category, row)
        add_edge(event_node, "OCCURRED_IN", location, row)

    return list(nodes.values()), list(edges.values())


def icews_summary(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(rows)
    actors = {actor_id(r.get("source_name"), r.get("source_country")) for r in rows}
    actors.update(actor_id(r.get("target_name"), r.get("target_country")) for r in rows)
    categories = {str(r.get("cameo_code")) for r in rows if r.get("cameo_code")}
    dates = sorted({str(r.get("event_time")) for r in rows if r.get("event_time")})
    return {
        "rows": len(rows), "events": len({str(r.get("event_id")) for r in rows}),
        "participants": len(actors), "categories": len(categories),
        "time_kind": "instant", "time_precision": "day",
        "time_from": dates[0] if dates else None, "time_to": dates[-1] if dates else None,
    }
