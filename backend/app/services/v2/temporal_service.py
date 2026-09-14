"""Deterministic temporal normalization and FalkorDB instance construction."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import math
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


@dataclass
class FactoryNetIncrementalState:
    """Small checkpoint carried between FactoryNet replay batches.

    The one-shot builder historically kept these dictionaries as local
    variables.  A replay needs the same information to survive a batch
    boundary (and a process restart), otherwise ``NEXT_OBSERVATION`` would
    be broken and ordinal values would start at zero for every batch.
    """

    sequence_by_episode: dict[str, int] = field(default_factory=dict)
    previous_by_episode: dict[str, str] = field(default_factory=dict)
    linked_machines: list[str] = field(default_factory=list)
    linked_episodes: list[str] = field(default_factory=list)
    linked_phases: list[str] = field(default_factory=list)
    linked_conditions: list[str] = field(default_factory=list)
    linked_inspections: list[str] = field(default_factory=list)
    linked_inspection_edges: list[str] = field(default_factory=list)
    linked_channels: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "FactoryNetIncrementalState":
        value = value or {}
        return cls(
            sequence_by_episode={str(k): int(v) for k, v in (value.get("sequence_by_episode") or {}).items()},
            previous_by_episode={str(k): str(v) for k, v in (value.get("previous_by_episode") or {}).items()},
            linked_machines=[str(v) for v in (value.get("linked_machines") or [])],
            linked_episodes=[str(v) for v in (value.get("linked_episodes") or [])],
            linked_phases=[str(v) for v in (value.get("linked_phases") or [])],
            linked_conditions=[str(v) for v in (value.get("linked_conditions") or [])],
            linked_inspections=[str(v) for v in (value.get("linked_inspections") or [])],
            linked_inspection_edges=[str(v) for v in (value.get("linked_inspection_edges") or [])],
            linked_channels=[str(v) for v in (value.get("linked_channels") or [])],
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
                if not math.isfinite(number):
                    raise ValueError(f"invalid sequence value {value}")
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


def build_factorynet_instances_incremental(
    rows: list[dict[str, Any]],
    state: FactoryNetIncrementalState | None = None,
    *,
    sequence_column: str = "time_s",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], FactoryNetIncrementalState]:
    """Build one FactoryNet chunk while preserving cross-chunk continuity.

    ``_replay_event_seq`` is an optional source-wide ordinal assigned by the
    replay scheduler.  It keeps the displayed sequence stable regardless of
    batch size or playback speed.  The regular construction path does not set
    it and therefore retains its historical per-call sequence behavior.
    """
    state = state or FactoryNetIncrementalState()
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    linked_machines: set[str] = set(state.linked_machines)
    linked_episodes: set[str] = set(state.linked_episodes)
    linked_channels: set[str] = set(state.linked_channels)
    linked_phases: set[str] = set(state.linked_phases)
    linked_conditions: set[str] = set(state.linked_conditions)
    linked_inspections: set[str] = set(state.linked_inspections)
    linked_inspection_edges: set[str] = set(state.linked_inspection_edges)
    for index, row in enumerate(rows):
        machine = str(row.get("machine_type") or "CNC_Mill_3_Axis").strip()
        episode = str(row.get("episode_id") or "unknown_episode").strip()
        machine_id = f"FactoryNet:Machine:{machine}"
        episode_id = f"FactoryNet:Episode:{episode}"
        if machine_id not in linked_machines:
            linked_machines.add(machine_id)
            nodes[machine_id] = {"id": machine_id, "entity_type": "Machine", "properties": {"machine_type": machine, "dataset": "FactoryNet"}}
        if episode_id not in linked_episodes:
            linked_episodes.add(episode_id)
            nodes[episode_id] = {"id": episode_id, "entity_type": "Episode", "properties": {"episode_id": episode, "machine_type": machine}}
            edges.append({"source": machine_id, "target": episode_id, "type": "HAS_EPISODE", "properties": {"source": "FactoryNet"}})
        sequence = row.get("_replay_event_seq")
        if sequence in (None, ""):
            sequence = state.sequence_by_episode.get(episode, 0)
        else:
            try:
                sequence = int(sequence)
            except (TypeError, ValueError):
                sequence = state.sequence_by_episode.get(episode, 0)
        state.sequence_by_episode[episode] = max(state.sequence_by_episode.get(episode, 0), int(sequence) + 1)
        source_row = row.get("_source_row_index", index)
        observation_id = f"FactoryNet:Observation:{episode}:{source_row}"
        props = {str(key): value for key, value in row.items() if not str(key).startswith("_")}
        # ``normalize_temporal_rows`` has already validated the selected
        # ordinal column.  Prefer its canonical numeric value so a CSV value
        # such as ``"1.5"`` is stored/queryable as a number instead of a
        # lexical string; the original source column remains in ``props`` for
        # provenance and display.
        elapsed = row.get("ordinal_value")
        if elapsed in (None, ""):
            elapsed = row.get(sequence_column)
        try:
            elapsed = float(elapsed)
            if elapsed.is_integer():
                elapsed = int(elapsed)
        except (TypeError, ValueError):
            # This path is only reachable for callers that bypass the
            # normalizer.  Keep their original value rather than inventing a
            # temporal value; the normal construction/replay path rejects it.
            elapsed = row.get(sequence_column)
        props.update({"event_seq": sequence, "elapsed_seconds": elapsed, "time_kind": "ordinal", "source_row_index": source_row})
        nodes[observation_id] = {"id": observation_id, "entity_type": "Observation", "properties": props}
        edge_props = {"event_seq": sequence, "elapsed_seconds": elapsed, "time_kind": "ordinal", "source_row_index": source_row}
        edges.extend([
            {"source": episode_id, "target": observation_id, "type": "HAS_OBSERVATION", "properties": edge_props},
            {"source": observation_id, "target": machine_id, "type": "OBSERVED_ON", "properties": edge_props},
        ])
        previous = state.previous_by_episode.get(episode)
        if previous:
            edges.append({"source": previous, "target": observation_id, "type": "NEXT_OBSERVATION", "properties": edge_props})
        state.previous_by_episode[episode] = observation_id
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
        inspection_edge_key = f"{episode_id}:{inspection_id}"
        if inspection_edge_key not in linked_inspection_edges:
            linked_inspection_edges.add(inspection_edge_key)
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
    state.linked_machines = sorted(linked_machines)
    state.linked_episodes = sorted(linked_episodes)
    state.linked_channels = sorted(linked_channels)
    state.linked_phases = sorted(linked_phases)
    state.linked_conditions = sorted(linked_conditions)
    state.linked_inspections = sorted(linked_inspections)
    state.linked_inspection_edges = sorted(linked_inspection_edges)
    return list(nodes.values()), edges, state


def build_factorynet_instances(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Backward-compatible one-shot FactoryNet construction."""
    nodes, edges, _ = build_factorynet_instances_incremental(rows)
    return nodes, edges


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
