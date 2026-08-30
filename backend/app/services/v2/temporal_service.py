"""Deterministic temporal normalization and FalkorDB instance construction."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any


@dataclass
class TemporalConfig:
    time_kind: str = "ordinal"  # ordinal|instant|interval
    sequence_column: str | None = "event_seq"
    event_time_column: str | None = None
    valid_from_column: str | None = None
    valid_to_column: str | None = None
    timezone: str = "UTC"


def _parse_instant(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        # pandas/numpy exports may contain nanosecond precision while Python's
        # stdlib ``fromisoformat`` accepts at most microseconds.  Truncating
        # excess precision preserves the ordering and makes the source time
        # explicit rather than silently inventing a timestamp.
        text = re.sub(r"(\.\d{6})\d+", r"\1", text)
        # A few exported timestamps carry an empty fractional part (``...00.``).
        # Removing that delimiter is a lossless syntax cleanup, not a guessed
        # date or time value.
        text = re.sub(r"\.$", "", text)
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"invalid ISO timestamp: {text}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def normalize_temporal_rows(rows: list[dict[str, Any]], config: TemporalConfig) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Normalize temporal fields without inventing dates or sequence values."""
    if config.time_kind not in {"ordinal", "instant", "interval"}:
        raise ValueError("time_kind must be ordinal, instant or interval")
    normalized: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        item = dict(row)
        item["time_kind"] = config.time_kind
        try:
            if config.time_kind == "ordinal":
                source = config.sequence_column or "event_seq"
                value = item.get(source)
                if value in (None, ""):
                    raise ValueError(f"missing sequence column {source}")
                item["event_seq"] = int(value)
                item["event_time"] = None
            elif config.time_kind == "instant":
                source = config.event_time_column or "event_time"
                item["event_time"] = _parse_instant(item.get(source))
                if item["event_time"] is None:
                    raise ValueError(f"missing event time column {source}")
                item["event_seq"] = None
            else:
                from_value = _parse_instant(item.get(config.valid_from_column or "valid_from"))
                to_value = _parse_instant(item.get(config.valid_to_column or "valid_to"))
                if from_value is None:
                    raise ValueError("missing valid_from")
                if to_value is not None and to_value < from_value:
                    raise ValueError("valid_to precedes valid_from")
                item["valid_from"] = from_value
                item["valid_to"] = to_value
                item["event_seq"] = None
                item["event_time"] = None
        except ValueError as exc:
            issues.append({"row_index": index, "error": str(exc)})
            # Invalid temporal rows are reported and excluded from graph
            # construction; retaining them would silently create a guessed
            # observation with no valid temporal semantics.
            continue
        normalized.append(item)
    return normalized, issues


def build_observation_instances(
    rows: list[dict[str, Any]],
    *,
    entity_id_column: str,
    entity_type: str = "Equipment",
    observation_type: str = "SensorReading",
    reading_id_prefix: str = "reading",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Create stable entity/observation nodes and OBSERVED_ON edges."""
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        entity_value = row.get(entity_id_column)
        if entity_value in (None, ""):
            continue
        entity_id = str(entity_value)
        nodes.setdefault(entity_id, {"id": entity_id, "entity_type": entity_type, "properties": {"source_id": entity_id}})
        seq_or_time = row.get("event_seq") if row.get("event_seq") is not None else row.get("event_time") or index
        reading_id = f"{reading_id_prefix}:{entity_id}:{seq_or_time}"
        props = {k: v for k, v in row.items() if not k.startswith("_")}
        props["source_row_index"] = index
        nodes[reading_id] = {"id": reading_id, "entity_type": observation_type, "properties": props}
        edge_props = {k: row.get(k) for k in ("time_kind", "event_seq", "event_time", "valid_from", "valid_to") if row.get(k) is not None}
        edges.append({"source": reading_id, "target": entity_id, "type": "OBSERVED_ON", "properties": edge_props})
    return list(nodes.values()), edges


def build_bts_instances(rows: list[dict[str, Any]], *, building_id: str = "BTS:Site_B") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build a Brick-oriented graph from the BTS tabular projection.

    The raw BTS pickle contains one series per ``StreamID``.  The projection
    keeps that identity and the Brick class in each row, so the graph can be
    rendered without guessing a device relationship from a value.  Every
    observation is attached to its point and every point to the anonymous
    Site B building.
    """
    nodes: dict[str, dict[str, Any]] = {
        building_id: {
            "id": building_id,
            "entity_type": "Building",
            "properties": {"building_id": building_id, "name": "BTS Site B", "source": "DIEF_BTS"},
        }
    }
    edges: list[dict[str, Any]] = []
    point_ids: set[str] = set()
    for index, row in enumerate(rows):
        stream = str(row.get("stream_id") or row.get("StreamID") or "").strip()
        timestamp = row.get("event_time") or row.get("timestamp")
        if not stream or not timestamp:
            continue
        point_id = f"BTS:Point:{stream}"
        if point_id not in point_ids:
            point_ids.add(point_id)
            nodes[point_id] = {
                "id": point_id,
                "entity_type": str(row.get("brick_class") or "Point"),
                "properties": {
                    "stream_id": stream,
                    "point_name": row.get("point_name") or stream,
                    "brick_class": row.get("brick_class") or "Point",
                    "site_id": row.get("site_id") or "Site_B",
                },
            }
            edges.append({"source": point_id, "target": building_id, "type": "LOCATED_IN", "properties": {"source": "BTS Site_B.ttl"}})
        observation_id = f"BTS:Observation:{stream}:{timestamp}"
        props = {k: v for k, v in row.items() if not str(k).startswith("_")}
        props.update({"event_time": timestamp, "time_kind": "instant", "source_row_index": index})
        nodes[observation_id] = {"id": observation_id, "entity_type": "Observation", "properties": props}
        edges.append({"source": observation_id, "target": point_id, "type": "OBSERVED_ON", "properties": {"event_time": timestamp, "time_kind": "instant"}})
    return list(nodes.values()), edges


def summarize_temporal_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Return stable, JSON-safe catalog statistics for a temporal preview."""
    streams = {str(r.get("stream_id") or r.get("StreamID")) for r in rows if r.get("stream_id") or r.get("StreamID")}
    times = [str(r.get("event_time") or r.get("timestamp")) for r in rows if r.get("event_time") or r.get("timestamp")]
    return {
        "rows": len(rows),
        "streams": len(streams),
        "columns": sorted({str(k) for r in rows[:100] for k in r.keys()}),
        "time_kind": "instant",
        "time_from": min(times) if times else None,
        "time_to": max(times) if times else None,
    }
