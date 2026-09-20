"""FactoryNet temporal data-arrival replay service.

This module keeps the replay protocol deterministic and deliberately small:
the source is read once to build an index, but graph writes happen only when a
batch is committed by the worker.  A replay therefore shows the same graph a
real stream would have produced while remaining restartable and idempotent.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.models.entity_instance import EntityInstance
from app.models.ontology import OntologyProject
from app.models.v2.construction import ConstructionRun, EvidenceRef
from app.models.v2.dataset import Dataset, DatasetVersion
from app.models.v2.temporal_replay import TemporalReplay, TemporalReplayBatch
from app.services.storage_service import get_storage_service
from app.services.v2.graph.falkordb_service import FalkorDBService
from app.services.v2.temporal_service import (
    FactoryNetIncrementalState,
    TemporalConfig,
    build_factorynet_instances_incremental,
    normalize_temporal_rows,
)


FACTORYNET_SOURCE_ID = "factorynet_cnc"
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
ACTIVE_STATUSES = {"queued", "running", "pausing"}
MAX_BATCH_ROWS = 200
_worker_lock = threading.RLock()
_active_workers: set[str] = set()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_float(value: Any, *, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是数值") from exc
    if result != result or result in (float("inf"), float("-inf")):
        raise ValueError(f"{field} 不能是 NaN 或无穷大")
    return result


def _row_key(row: dict[str, Any], fallback: int) -> str:
    return str(row.get("_source_row_index", fallback))


def _episode(row: dict[str, Any]) -> str:
    return str(row.get("episode_id") or row.get("_series_id") or "unknown_episode").strip()


def _source_time(row: dict[str, Any], fallback: int, column: str = "time_s") -> float:
    value = row.get(column)
    if value in (None, ""):
        # FactoryNet is expected to have time_s.  The fallback is only used by
        # tests/fixtures that deliberately model a row-order stream; the
        # caller still records the resulting warning in the batch issues.
        return float(fallback)
    return _as_float(value, field=column)


def _schedule_time(row: dict[str, Any], fallback: int, column: str = "time_s") -> float:
    """Return a sortable time while preserving invalid rows for validation.

    The scheduler must be able to persist a batch containing an invalid source
    value so the worker can report the exact row as a temporal issue. The
    fallback is only used for ordering and bucket placement, never written as
    the row's temporal value.
    """
    try:
        return _source_time(row, fallback, column)
    except ValueError:
        return float(fallback)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def _assign_replay_sequences(
    rows: list[dict[str, Any]],
    sequence_offsets: dict[str, int] | None = None,
    *,
    time_column: str = "time_s",
) -> list[dict[str, Any]]:
    """Attach stable per-episode ordinal ranks without changing source time."""
    offsets = {str(k): int(v) for k, v in (sequence_offsets or {}).items()}
    counters = dict(offsets)
    assigned: list[dict[str, Any]] = []
    for index, row in sorted(enumerate(rows), key=lambda pair: (_schedule_time(pair[1], pair[0], time_column), _episode(pair[1]), int(pair[1].get("_source_row_index", pair[0])))):
        item = dict(row)
        episode = _episode(item)
        item["_replay_event_seq"] = counters.get(episode, 0)
        counters[episode] = counters.get(episode, 0) + 1
        assigned.append(item)
    return assigned


def build_factorynet_replay_batches(
    rows: list[dict[str, Any]],
    *,
    series_ids: Iterable[str] | None = None,
    start_time: float | None = None,
    end_time: float | None = None,
    window_seconds: float = 1.0,
    max_rows_per_batch: int = MAX_BATCH_ROWS,
    max_records: int | None = None,
    sequence_offsets: dict[str, int] | None = None,
    batch_offset: int = 0,
    time_column: str = "time_s",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Create a deterministic schedule from FactoryNet rows.

    Rows sharing a source-time bucket are kept together unless the bucket is
    larger than ``max_rows_per_batch``.  In that case it is split into chunks
    with the same time bounds, which preserves the meaning of the timeline.
    """
    if window_seconds <= 0:
        raise ValueError("window_seconds 必须大于 0")
    max_rows_per_batch = max(1, min(int(max_rows_per_batch), 5000))
    all_candidates: list[tuple[int, dict[str, Any], float]] = []
    requested = {str(value) for value in (series_ids or []) if str(value).strip()}
    for index, row in enumerate(rows):
        item = dict(row)
        if requested and _episode(item) not in requested:
            continue
        try:
            source_time = _source_time(item, index, time_column)
        except ValueError:
            # Keep the row in the schedule; normalization will report the
            # exact issue rather than silently dropping source evidence.
            source_time = float(index)
        if start_time is not None and source_time < float(start_time):
            continue
        if end_time is not None and source_time > float(end_time):
            continue
        all_candidates.append((index, item, source_time))
    all_candidates.sort(key=lambda value: (value[2], _episode(value[1]), int(value[1].get("_source_row_index", value[0]))))
    selected = [row for _, row, _ in all_candidates]
    if not selected:
        return [], {
            "source_rows": len(rows), "selected_rows": 0, "start_time": start_time,
            "end_time": end_time, "series_ids": sorted(requested), "batches": 0,
            "time_column": time_column,
        }
    assigned = _assign_replay_sequences(selected, sequence_offsets, time_column=time_column)
    if max_records is not None:
        maximum = max(1, min(int(max_records), len(assigned)))
        if len(assigned) > maximum:
            # Time-uniform sampling per episode keeps a multi-process replay
            # representative while retaining the first and last observation
            # of every selected process whenever the limit permits it.
            groups: dict[str, list[dict[str, Any]]] = {}
            for row in assigned:
                groups.setdefault(_episode(row), []).append(row)
            if maximum < len(groups):
                # A request smaller than the number of series cannot retain
                # one row per series.  Pick the first series in stable source
                # order rather than exceeding the explicit user limit.
                quotas = {key: 1 for key in sorted(groups)[:maximum]}
            else:
                quotas = {key: max(1, int(round(maximum * len(value) / len(assigned)))) for key, value in groups.items()}
                while sum(quotas.values()) > maximum:
                    candidate = max(quotas, key=lambda key: (quotas[key], len(groups[key])))
                    if quotas[candidate] <= 1:
                        break
                    quotas[candidate] -= 1
                while sum(quotas.values()) < maximum:
                    candidate = max(groups, key=lambda key: (len(groups[key]) - quotas.get(key, 0), len(groups[key])))
                    if quotas.get(candidate, 0) >= len(groups[candidate]):
                        break
                    quotas[candidate] = quotas.get(candidate, 0) + 1
            sampled: list[dict[str, Any]] = []
            for key, group in groups.items():
                # When the explicit limit is smaller than the number of
                # series, ``quotas`` intentionally contains only the
                # stable subset we selected.  Do not index missing keys
                # (and, importantly, never exceed the requested limit).
                if key not in quotas:
                    continue
                quota = min(len(group), quotas[key])
                if quota >= len(group):
                    sampled.extend(group)
                elif quota == 1:
                    sampled.append(group[0])
                else:
                    for index in range(quota):
                        sampled.append(group[round(index * (len(group) - 1) / (quota - 1))])
            assigned = sorted(sampled, key=lambda row: (_schedule_time(row, 0, time_column), _episode(row), int(row.get("_source_row_index", 0))))
    selected = assigned
    # Rejoin the source time with the sequence-bearing rows after sorting.
    times = [_schedule_time(row, index, time_column) for index, row in enumerate(assigned)]
    grouped: dict[int, list[tuple[dict[str, Any], float]]] = {}
    origin = float(start_time if start_time is not None else min(times))
    for row, source_time in zip(assigned, times):
        bucket = int((source_time - origin) // float(window_seconds)) if source_time >= origin else 0
        grouped.setdefault(bucket, []).append((row, source_time))

    batches: list[dict[str, Any]] = []
    for bucket in sorted(grouped):
        values = grouped[bucket]
        for chunk_start in range(0, len(values), max_rows_per_batch):
            chunk = values[chunk_start:chunk_start + max_rows_per_batch]
            chunk_rows = [row for row, _ in chunk]
            chunk_times = [source_time for _, source_time in chunk]
            batch_no = batch_offset + len(batches)
            payload = _canonical_json(chunk_rows)
            batches.append({
                "batch_no": batch_no,
                "time_from": min(chunk_times),
                "time_to": max(chunk_times),
                "source_row_ids": [_row_key(row, index) for index, row in enumerate(chunk_rows)],
                "rows": chunk_rows,
                "source_rows": len(chunk_rows),
                "latest_rows": [{key: value for key, value in row.items() if not str(key).startswith("_")} for row in chunk_rows[:20]],
                "payload_hash": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            })
    episode_ids = sorted({_episode(row) for row in selected})
    return batches, {
        "source_rows": len(rows),
        "selected_rows": len(selected),
        "start_time": min(times),
        "end_time": max(times),
        "series_ids": episode_ids,
        "batches": len(batches),
        "window_seconds": float(window_seconds),
        "time_column": time_column,
    }


def _serialize_batch(batch: TemporalReplayBatch) -> dict[str, Any]:
    return {
        "id": batch.id,
        "replay_id": batch.replay_id,
        "batch_no": batch.batch_no,
        "time_from": batch.time_from,
        "time_to": batch.time_to,
        "status": batch.status,
        "source_row_ids": batch.source_row_ids or [],
        "source_rows": batch.source_rows,
        "normalized_rows": batch.normalized_rows,
        "nodes_written": batch.nodes_written,
        "edges_written": batch.edges_written,
        "issue_count": batch.issue_count,
        "latest_rows": batch.latest_rows or [],
        "payload_hash": batch.payload_hash,
        "error": batch.error,
        "created_at": batch.created_at.isoformat() if batch.created_at else None,
        "started_at": batch.started_at.isoformat() if batch.started_at else None,
        "completed_at": batch.completed_at.isoformat() if batch.completed_at else None,
        "updated_at": batch.updated_at.isoformat() if batch.updated_at else None,
    }


def serialize_replay(replay: TemporalReplay, *, latest_batch: TemporalReplayBatch | None = None) -> dict[str, Any]:
    progress = dict(replay.metrics or {})
    progress.update({
        "current_batch": replay.current_batch_index,
        "total_batches": replay.total_batches,
        "pct": round(((replay.current_batch_index + 1) / replay.total_batches) * 100, 2) if replay.total_batches and replay.current_batch_index >= 0 else 0,
    })
    return {
        "id": replay.id,
        "replay_id": replay.id,
        "ontology_id": replay.ontology_id,
        "dataset_id": replay.dataset_id,
        "dataset_version_id": replay.dataset_version_id,
        "source_id": replay.source_id,
        "status": replay.status,
        "source_mode": getattr(replay, "source_mode", "file_replay"),
        "schema_revision_id": getattr(replay, "schema_revision_id", None),
        "graph_namespace": getattr(replay, "graph_namespace", None),
        "time_kind": replay.time_kind,
        "entity_column": replay.entity_column,
        "time_column": replay.time_column,
        "series_ids": replay.series_ids or [],
        "start_time": replay.start_time,
        "end_time": replay.end_time,
        "window_seconds": replay.window_seconds,
        "speed": replay.speed,
        "current_time": replay.current_time,
        "current_batch_index": replay.current_batch_index,
        "total_batches": replay.total_batches,
        "current_event_index": getattr(replay, "current_event_index", -1),
        "total_events": getattr(replay, "total_events", replay.selected_rows),
        "committed_events": getattr(replay, "committed_events", replay.normalized_rows),
        "watermark_ordinal": float(replay.watermark_ordinal) if getattr(replay, "watermark_ordinal", None) is not None else None,
        "watermark_sequence": getattr(replay, "watermark_sequence", None),
        "event_interval_ms": getattr(replay, "event_interval_ms", 1000),
        "published_snapshot_id": getattr(replay, "published_snapshot_id", None),
        "published_at": replay.published_at.isoformat() if getattr(replay, "published_at", None) else None,
        "source_rows": replay.source_rows,
        "selected_rows": replay.selected_rows,
        "normalized_rows": replay.normalized_rows,
        "metrics": replay.metrics or {},
        "progress": progress,
        "config": replay.config or {},
        "error": replay.error,
        "pause_requested": bool(replay.pause_requested),
        "step_requested": bool(replay.step_requested),
        "cancel_requested": bool(replay.cancel_requested),
        "latest_batch": _serialize_batch(latest_batch) if latest_batch else None,
        "latest_rows": (latest_batch.latest_rows or []) if latest_batch else [],
        "created_at": replay.created_at.isoformat() if replay.created_at else None,
        "started_at": replay.started_at.isoformat() if replay.started_at else None,
        "completed_at": replay.completed_at.isoformat() if replay.completed_at else None,
        "updated_at": replay.updated_at.isoformat() if replay.updated_at else None,
    }


def _load_factorynet_rows(db: Session, replay: TemporalReplay) -> list[dict[str, Any]]:
    from app.routers.v2.temporal import parse_temporal_bytes

    if not replay.dataset_version_id:
        raise ValueError("replay 没有数据版本")
    version = db.query(DatasetVersion).filter(DatasetVersion.id == replay.dataset_version_id).first()
    if not version or not version.storage_uri:
        raise ValueError("FactoryNet 数据版本没有可读取对象")
    return parse_temporal_bytes(get_storage_service().get_object(version.storage_uri))


def _ensure_construction_run(db: Session, replay: TemporalReplay) -> ConstructionRun:
    config = dict(replay.config or {})
    run_id = config.get("construction_run_id")
    run = db.query(ConstructionRun).filter(ConstructionRun.id == run_id).first() if run_id else None
    if run:
        return run
    run = ConstructionRun(
        id=str(uuid.uuid4()), ontology_id=replay.ontology_id, dataset_id=replay.dataset_id,
        mode="temporal_replay", status="running", config={"replay_id": replay.id},
        progress={"stage": "时序模拟", "completed": 0, "total": replay.selected_rows}, metrics={},
    )
    db.add(run)
    config["construction_run_id"] = run.id
    replay.config = config
    db.flush()
    return run


def _sync_construction_run(
    db: Session,
    replay: TemporalReplay,
    *,
    status: str | None = None,
    error: str | None = None,
) -> None:
    """Mirror replay progress into the shared ConstructionRun record.

    The replay is the authoritative checkpoint, but the ordinary task list
    should still show a useful status and counters instead of an orphaned
    forever-running ConstructionRun.
    """
    run_id = (replay.config or {}).get("construction_run_id")
    if not run_id:
        return
    run = db.query(ConstructionRun).filter(ConstructionRun.id == run_id).first()
    if not run:
        return
    metrics = dict(replay.metrics or {})
    run.status = status or run.status
    run.error = error
    run.metrics = metrics
    run.progress = {
        "stage": "时序模拟",
        "completed": int(replay.normalized_rows or 0),
        "total": int(replay.selected_rows or 0),
        "current_batch": int(replay.current_batch_index),
        "total_batches": int(replay.total_batches),
    }
    if status == "completed":
        run.completed_at = _now()
    elif status == "running" and run.started_at is None:
        run.started_at = _now()


def _materialize_rows(db: Session, replay: TemporalReplay, rows: list[dict[str, Any]]) -> None:
    """Mirror the current FactoryNet rows into Nano's inspector table."""
    entity_ids = {name: f"schema:{replay.ontology_id}:{name}" for name in ("Machine", "Episode", "Observation")}
    for index, row in enumerate(rows):
        episode = _episode(row)
        machine = str(row.get("machine_type") or "CNC_Mill_3_Axis")
        source_row = _row_key(row, index)
        for entity_type, identity, data in (
            ("Machine", machine, {"machine_type": machine}),
            ("Episode", episode, {"episode_id": episode, "machine_type": machine}),
            ("Observation", f"{episode}:{source_row}", dict(row)),
        ):
            instance_id = f"temporal:{replay.ontology_id}:{entity_type}:" + hashlib.sha256(f"{replay.ontology_id}:{entity_type}:{identity}".encode()).hexdigest()[:20]
            item = db.get(EntityInstance, instance_id)
            if item is None:
                item = EntityInstance(id=instance_id, ontology_id=replay.ontology_id, entity_id=entity_ids[entity_type], row_identity=identity[:200], row_data=data)
                db.add(item)
            else:
                item.entity_id = entity_ids[entity_type]
                item.row_identity = identity[:200]
                item.row_data = data


def _add_batch_evidence(db: Session, replay: TemporalReplay, batch: TemporalReplayBatch, rows: list[dict[str, Any]]) -> None:
    run = _ensure_construction_run(db, replay)
    source_version = str((replay.config or {}).get("source_checksum") or replay.dataset_version_id or "factorynet")
    for index, row in enumerate(rows):
        assertion_id = f"temporal:{replay.ontology_id}:Observation:" + hashlib.sha256(f"{_episode(row)}:{_row_key(row, index)}".encode()).hexdigest()[:20]
        evidence_id = hashlib.sha256(f"{replay.id}:{batch.batch_no}:{assertion_id}".encode()).hexdigest()
        if db.get(EvidenceRef, evidence_id):
            continue
        text = _canonical_json({key: value for key, value in row.items() if not str(key).startswith("_")})[:8000]
        db.add(EvidenceRef(
            id=evidence_id, construction_run_id=run.id, ontology_id=replay.ontology_id,
            assertion_id=assertion_id, assertion_kind="node", source_dataset_id=replay.dataset_id,
            source_version=source_version, source_file=(replay.config or {}).get("source_file"),
            source_row_id=_row_key(row, index), extractor="rule", confidence=1.0,
            confidence_method="deterministic_temporal_replay", evidence_text=text,
            content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        ))


def _batch_rows(rows: list[dict[str, Any]], batch: TemporalReplayBatch) -> list[dict[str, Any]]:
    keys = {str(value) for value in (batch.source_row_ids or [])}
    return [row for index, row in enumerate(rows) if _row_key(row, index) in keys]


def _apply_field_mapping(row: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Add user-selected target attributes without destroying source fields.

    FactoryNet's structural columns (``episode_id``, ``time_s`` and the
    ``ctx_*`` fields) remain available to the deterministic builder.  A
    mapping therefore enriches the row with the chosen target property while
    retaining the original value for source inspection and EvidenceRef.
    """
    item = dict(row)
    field_mapping = config.get("field_mapping") or {}
    columns = field_mapping.get("columns") if isinstance(field_mapping, dict) else None
    if not isinstance(columns, dict):
        return item
    for source, spec in columns.items():
        if not isinstance(spec, dict):
            continue
        target = str(spec.get("target") or "").strip()
        if not target or target.startswith("_") or source not in row:
            continue
        item[target] = row.get(source)
    return item


def _apply_relation_mapping(edges: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    """Apply the relation editor as a safe, deterministic allow-list.

    The FactoryNet adapter owns the source/target topology.  Users can turn
    off a relation or give it an alias, but cannot create a guessed endpoint
    from a free-text label.  The fixed schema remains the compatibility
    vocabulary; aliases are recorded on the edge for the current run.
    """
    field_mapping = config.get("field_mapping") or {}
    configured = field_mapping.get("relations") if isinstance(field_mapping, dict) else None
    if not isinstance(configured, list) or not configured:
        return edges
    names = [str(value).strip() for value in configured]
    if not any(names):
        return []
    default_types = [
        "HAS_EPISODE", "HAS_OBSERVATION", "OBSERVED_ON", "NEXT_OBSERVATION",
        "IN_PHASE", "HAS_TOOL_CONDITION", "EXPOSES_CHANNEL", "HAS_INSPECTION",
    ]
    aliases = {
        default_types[index]: names[index]
        for index in range(min(len(default_types), len(names)))
        if names[index]
    }
    selected: list[dict[str, Any]] = []
    for edge in edges:
        original = str(edge.get("type") or "")
        if original not in aliases:
            continue
        item = dict(edge)
        item["type"] = aliases[original]
        props = dict(item.get("properties") or {})
        props["schema_relation"] = original
        if aliases[original] != original:
            props["relation_alias"] = aliases[original]
        item["properties"] = props
        selected.append(item)
    return selected


def _write_batch(db: Session, replay: TemporalReplay, batch: TemporalReplayBatch, rows: list[dict[str, Any]]) -> None:
    source_rows = [_apply_field_mapping(row, replay.config or {}) for row in _batch_rows(rows, batch)]
    time_column = replay.time_column or "time_s"
    source_rows = sorted(source_rows, key=lambda row: (_schedule_time(row, 0, time_column), _episode(row), int(row.get("_source_row_index", 0))))
    normalized, issues = normalize_temporal_rows(source_rows, TemporalConfig(time_kind="ordinal", sequence_column=time_column))
    state = FactoryNetIncrementalState.from_dict(replay.state or {})
    nodes, edges, state = build_factorynet_instances_incremental(normalized, state, sequence_column=time_column)
    edges = _apply_relation_mapping(edges, replay.config or {})
    falkor = FalkorDBService()
    if not falkor.available:
        raise RuntimeError("FalkorDB unavailable")
    for node in nodes:
        node.setdefault("properties", {})["_replay_id"] = replay.id
    for edge in edges:
        edge.setdefault("properties", {})["_replay_id"] = replay.id
    # The schema is deterministic and is created before the first graph write.
    from app.tasks.v2.temporal_construction import _ensure_factorynet_schema
    _ensure_factorynet_schema(db, replay.ontology_id)
    _materialize_rows(db, replay, normalized)
    _add_batch_evidence(db, replay, batch, normalized)
    falkor.upsert_instances(replay.ontology_id, nodes)
    falkor.upsert_relations(replay.ontology_id, edges)
    batch.normalized_rows = len(normalized)
    batch.nodes_written = len(nodes)
    batch.edges_written = len(edges)
    batch.issue_count = len(issues)
    batch.latest_rows = [{key: value for key, value in row.items() if not str(key).startswith("_")} for row in source_rows[:20]]
    replay.state = state.to_dict()
    replay.normalized_rows = int(replay.normalized_rows or 0) + len(normalized)
    metrics = dict(replay.metrics or {})
    metrics.update({
        "source_rows": replay.source_rows,
        "selected_rows": replay.selected_rows,
        "normalized_rows": replay.normalized_rows,
        "nodes_written": int(metrics.get("nodes_written", 0)) + len(nodes),
        "edges_written": int(metrics.get("edges_written", 0)) + len(edges),
        "issues": int(metrics.get("issues", 0)) + len(issues),
        "committed_batches": int(metrics.get("committed_batches", 0)) + 1,
        "last_batch_issues": issues[:20],
    })
    replay.metrics = metrics
    _sync_construction_run(db, replay, status="running")
    if not normalized:
        raise ValueError(f"NO_VALID_TEMPORAL_ROWS: 当前批次没有有效 {time_column}")


def _mark_terminal(db: Session, replay: TemporalReplay, status: str, error: str | None = None) -> None:
    replay.status = status
    replay.error = error
    replay.updated_at = _now()
    if status in TERMINAL_STATUSES:
        replay.completed_at = _now()
    _sync_construction_run(db, replay, status=("completed" if status == "completed" else status), error=error)
    db.commit()


def run_temporal_replay(replay_id: str) -> dict[str, Any]:
    """Process committed batches until pause, cancel, failure, or completion."""
    with _worker_lock:
        if replay_id in _active_workers:
            return {"replay_id": replay_id, "status": "already_running"}
        _active_workers.add(replay_id)
    from app.database import SessionLocal

    db = SessionLocal()
    current: TemporalReplayBatch | None = None
    try:
        replay = db.query(TemporalReplay).filter(TemporalReplay.id == replay_id).first()
        if not replay or replay.status in TERMINAL_STATUSES:
            return {"replay_id": replay_id, "status": replay.status if replay else "missing"}
        replay.status = "running"
        replay.started_at = replay.started_at or _now()
        replay.updated_at = _now()
        _sync_construction_run(db, replay, status="running")
        db.commit()
        rows = _load_factorynet_rows(db, replay)
        batches = db.query(TemporalReplayBatch).filter(TemporalReplayBatch.replay_id == replay.id).order_by(TemporalReplayBatch.batch_no).all()
        for batch in batches:
            current = batch
            db.refresh(replay)
            if batch.status == "completed":
                continue
            if replay.cancel_requested:
                _mark_terminal(db, replay, "cancelled", "用户取消时序模拟")
                return serialize_replay(replay, latest_batch=current)
            # A pause issued immediately after starting may arrive before the
            # worker commits its first batch.  Honour it here as well so the
            # user can reliably use "开始 → 暂停" without an unexpected first
            # batch slipping through.
            if replay.pause_requested:
                replay.status = "paused"; replay.updated_at = _now(); _sync_construction_run(db, replay, status="paused"); db.commit()
                return serialize_replay(replay, latest_batch=current)
            batch.status = "running"; batch.started_at = _now(); batch.updated_at = _now(); db.commit()
            try:
                _write_batch(db, replay, batch, rows)
                batch.status = "completed"; batch.completed_at = _now(); batch.updated_at = _now()
                replay.current_batch_index = batch.batch_no
                replay.current_time = batch.time_to
                replay.updated_at = _now()
                _sync_construction_run(db, replay, status="running")
                db.commit()
            except Exception as exc:
                db.rollback()
                batch = db.query(TemporalReplayBatch).filter(TemporalReplayBatch.id == batch.id).first() or batch
                batch.status = "failed"; batch.error = str(exc)[:2000]; batch.updated_at = _now()
                replay = db.query(TemporalReplay).filter(TemporalReplay.id == replay_id).first()
                if replay:
                    replay.status = "failed"; replay.error = str(exc)[:2000]; replay.updated_at = _now(); replay.completed_at = _now()
                    _sync_construction_run(db, replay, status="failed", error=str(exc)[:2000])
                db.commit()
                return {"replay_id": replay_id, "status": "failed", "error": str(exc)}
            db.refresh(replay)
            if replay.step_requested:
                replay.step_requested = False
                replay.pause_requested = True
                replay.status = "paused"
                replay.updated_at = _now()
                _sync_construction_run(db, replay, status="paused")
                db.commit()
                return serialize_replay(replay, latest_batch=batch)
            if replay.pause_requested:
                replay.status = "paused"; replay.updated_at = _now(); _sync_construction_run(db, replay, status="paused"); db.commit()
                return serialize_replay(replay, latest_batch=batch)
            if batch.time_to is not None and batch.time_from is not None:
                delay = max(0.05, min(5.0, max(0.0, float(batch.time_to) - float(batch.time_from)) / max(float(replay.speed or 1), 0.01)))
                time.sleep(delay)
        replay = db.query(TemporalReplay).filter(TemporalReplay.id == replay_id).first()
        if replay and replay.status not in TERMINAL_STATUSES:
            replay.status = "completed"; replay.current_batch_index = replay.total_batches - 1; replay.updated_at = _now(); replay.completed_at = _now(); _sync_construction_run(db, replay, status="completed"); db.commit()
            latest = db.query(TemporalReplayBatch).filter(TemporalReplayBatch.replay_id == replay.id).order_by(TemporalReplayBatch.batch_no.desc()).first()
            return serialize_replay(replay, latest_batch=latest)
        return {"replay_id": replay_id, "status": replay.status if replay else "missing"}
    finally:
        db.close()
        with _worker_lock:
            _active_workers.discard(replay_id)


def dispatch_replay(replay_id: str) -> str:
    """Dispatch through Celery when enabled, otherwise use a local daemon."""
    import os
    if os.getenv("CELERY_ENABLED", "").lower() in {"1", "true", "yes"}:
        try:
            from app.tasks.v2.temporal_replay import run_temporal_replay_task
            run_temporal_replay_task.delay(replay_id)
            return "celery"
        except Exception:
            pass
    thread = threading.Thread(target=run_temporal_replay, args=(replay_id,), name=f"temporal-replay-{replay_id[:8]}", daemon=True)
    thread.start()
    return "thread"


def update_replay_control(db: Session, replay: TemporalReplay, action: str, speed: float | None = None) -> tuple[TemporalReplay, bool]:
    if speed is not None:
        speed = _as_float(speed, field="speed")
        if speed <= 0 or speed > 100:
            raise ValueError("speed 必须在 (0, 100] 范围内")
        replay.speed = speed
    dispatch = False
    if action == "start":
        if replay.status in TERMINAL_STATUSES:
            raise ValueError("终态 replay 不能重新开始，请新建模拟")
        replay.cancel_requested = False; replay.pause_requested = False; replay.step_requested = False; replay.status = "queued"; dispatch = True
    elif action == "pause":
        if replay.status in TERMINAL_STATUSES:
            return replay, False
        replay.pause_requested = True
        replay.status = "pausing" if replay.status == "running" else "paused"
    elif action == "resume":
        if replay.status in TERMINAL_STATUSES:
            raise ValueError("终态 replay 不能继续")
        replay.pause_requested = False; replay.step_requested = False; replay.cancel_requested = False; replay.status = "queued"; dispatch = True
    elif action == "step":
        if replay.status in TERMINAL_STATUSES:
            raise ValueError("终态 replay 不能单步")
        replay.pause_requested = False; replay.step_requested = True; replay.cancel_requested = False; replay.status = "queued"; dispatch = True
    elif action == "cancel":
        if replay.status in TERMINAL_STATUSES:
            return replay, False
        replay.cancel_requested = True; replay.pause_requested = False; replay.step_requested = False; replay.status = "cancelled" if replay.status != "running" else "pausing"
    else:
        raise ValueError("action 必须是 start、pause、resume、step 或 cancel")
    replay.updated_at = _now()
    _sync_construction_run(db, replay, status=replay.status)
    db.commit(); db.refresh(replay)
    return replay, dispatch
