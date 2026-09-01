"""Deterministic temporal normalization and FalkorDB instance construction."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
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
                number = float(value)
                item["ordinal_value"] = int(number) if number.is_integer() else number
                item["event_seq"] = int(number) if number.is_integer() else None
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
    series_id = f"Series:{entity_id_column}"
    previous_by_entity: dict[str, str] = {}
    for index, row in enumerate(rows):
        entity_value = row.get(entity_id_column)
        entity_id = str(entity_value) if entity_value not in (None, "") else series_id
        nodes.setdefault(entity_id, {"id": entity_id, "entity_type": entity_type, "properties": {"source_id": entity_id}})
        seq_or_time = row.get("event_seq") if row.get("event_seq") is not None else row.get("event_time") or index
        reading_id = f"{reading_id_prefix}:{entity_id}:{seq_or_time}"
        props = {k: v for k, v in row.items() if not k.startswith("_")}
        props["source_row_index"] = index
        nodes[reading_id] = {"id": reading_id, "entity_type": observation_type, "properties": props}
        edge_props = {k: row.get(k) for k in ("time_kind", "event_seq", "event_time", "valid_from", "valid_to") if row.get(k) is not None}
        edges.append({"source": reading_id, "target": entity_id, "type": "OBSERVED_ON", "properties": edge_props})
        previous = previous_by_entity.get(entity_id)
        if previous:
            edges.append({"source": previous, "target": reading_id, "type": "NEXT_OBSERVATION", "properties": {"time_kind": row.get("time_kind"), "event_seq": row.get("event_seq")}})
        previous_by_entity[entity_id] = reading_id
    return list(nodes.values()), edges


def build_factorynet_instances(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build a connected, stable graph for the FactoryNet CNC file.

    Scalar S-E-F-C signals remain properties on Observation nodes. Shared
    channel metadata nodes keep the graph interpretable without creating one
    node and edge for every numeric cell.
    """
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    episode_sequences: dict[str, int] = {}
    previous_by_episode: dict[str, str] = {}
    linked_channels: set[str] = set()
    linked_phases: set[str] = set()
    linked_conditions: set[str] = set()
    linked_inspections: set[str] = set()
    for index, row in enumerate(rows):
        machine = str(row.get("machine_type") or "CNC_Mill_3_Axis").strip()
        episode = str(row.get("episode_id") or "unknown_episode").strip()
        machine_id = f"FactoryNet:Machine:{machine}"
        episode_id = f"FactoryNet:Episode:{episode}"
        nodes.setdefault(machine_id, {"id": machine_id, "entity_type": "Machine", "properties": {"machine_type": machine, "dataset": "FactoryNet"}})
        nodes.setdefault(episode_id, {"id": episode_id, "entity_type": "Episode", "properties": {"episode_id": episode, "machine_type": machine}})
        if not any(edge["source"] == machine_id and edge["target"] == episode_id for edge in edges):
            edges.append({"source": machine_id, "target": episode_id, "type": "HAS_EPISODE", "properties": {"source": "FactoryNet"}})
        sequence = episode_sequences.get(episode, 0)
        episode_sequences[episode] = sequence + 1
        source_row = row.get("_source_row_index", index)
        observation_id = f"FactoryNet:Observation:{episode}:{source_row}"
        props = {str(key): value for key, value in row.items() if not str(key).startswith("_")}
        elapsed = row.get("time_s")
        props.update({"event_seq": sequence, "elapsed_seconds": elapsed, "time_kind": "ordinal", "source_row_index": source_row})
        nodes[observation_id] = {"id": observation_id, "entity_type": "Observation", "properties": props}
        edge_props = {"event_seq": sequence, "elapsed_seconds": elapsed, "time_kind": "ordinal", "source_row_index": source_row}
        edges.extend([
            {"source": episode_id, "target": observation_id, "type": "HAS_OBSERVATION", "properties": edge_props},
            {"source": observation_id, "target": machine_id, "type": "OBSERVED_ON", "properties": edge_props},
        ])
        previous = previous_by_episode.get(episode)
        if previous:
            edges.append({"source": previous, "target": observation_id, "type": "NEXT_OBSERVATION", "properties": edge_props})
        previous_by_episode[episode] = observation_id
        phase = str(row.get("ctx_process_phase") or "unknown").strip()
        phase_key = hashlib.sha1(phase.encode()).hexdigest()[:12]
        phase_id = f"FactoryNet:ProcessPhase:{phase_key}"
        if phase_id not in linked_phases:
            linked_phases.add(phase_id)
            nodes[phase_id] = {"id": phase_id, "entity_type": "ProcessPhase", "properties": {"name": phase}}
        edges.append({"source": observation_id, "target": phase_id, "type": "IN_PHASE", "properties": edge_props})
        condition = str(row.get("ctx_tool_condition") or "unknown").strip()
        condition_key = hashlib.sha1(condition.encode()).hexdigest()[:12]
        condition_id = f"FactoryNet:ToolCondition:{condition_key}"
        if condition_id not in linked_conditions:
            linked_conditions.add(condition_id)
            nodes[condition_id] = {"id": condition_id, "entity_type": "ToolCondition", "properties": {"value": condition}}
        edges.append({"source": observation_id, "target": condition_id, "type": "HAS_TOOL_CONDITION", "properties": edge_props})
        inspection = str(row.get("ctx_passed_visual_inspection") or "unknown").strip()
        inspection_key = hashlib.sha1(inspection.encode()).hexdigest()[:12]
        inspection_id = f"FactoryNet:InspectionResult:{inspection_key}"
        if inspection_id not in linked_inspections:
            linked_inspections.add(inspection_id)
            nodes[inspection_id] = {"id": inspection_id, "entity_type": "InspectionResult", "properties": {"value": inspection}}
        if not any(edge["source"] == episode_id and edge["target"] == inspection_id for edge in edges):
            edges.append({"source": episode_id, "target": inspection_id, "type": "HAS_INSPECTION", "properties": {"source": "FactoryNet"}})
        for column in row.keys():
            name = str(column)
            if not (name.startswith("setpoint_") or name.startswith("effort_") or name.startswith("feedback_")):
                continue
            channel_id = f"FactoryNet:SensorChannel:{name}"
            if channel_id in linked_channels:
                continue
            linked_channels.add(channel_id)
            group = name.split("_", 1)[0].upper()
            nodes[channel_id] = {"id": channel_id, "entity_type": "SensorChannel", "properties": {"name": name, "signal_group": group}}
            edges.append({"source": machine_id, "target": channel_id, "type": "EXPOSES_CHANNEL", "properties": {"signal_group": group}})
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
