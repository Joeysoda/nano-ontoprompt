"""Event-at-a-time FactoryNet temporal stream service.

The older :mod:`temporal_replay_service` is intentionally left in place for
compatibility with the first demonstrator.  This module is the durable stream
implementation used by the new ``动态演化`` page.  PostgreSQL records events
and temporal facts; FalkorDB receives an idempotent, per-run projection when it
is available.  No event invokes a language model and no event writes the
published ontology schema tables.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.ontology import OntologyProject
from app.models.ontology_revision import OntologyRevision
from app.models.v2.construction import ConstructionRun, EvidenceRef
from app.models.v2.dataset import Dataset, DatasetVersion
from app.models.v2.temporal_replay import (
    DataModelSnapshot,
    TemporalFact,
    TemporalReplay,
    TemporalReplayBatch,
    TemporalStreamEvent,
)
from app.services.storage_service import get_storage_service
from app.services.v2.datasets.factorynet_installer import FACTORYNET_SOURCE_ID, find_factorynet_dataset
from app.services.v2.graph.falkordb_service import FalkorDBService
from app.services.v2.temporal_service import (
    FactoryNetIncrementalState,
    TemporalConfig,
    build_factorynet_instances_incremental,
    normalize_temporal_rows,
)


STREAM_ACTIVE_STATUSES = {"created", "queued", "running", "pausing", "paused"}
STREAM_TERMINAL_STATUSES = {"completed", "failed", "cancelled", "published"}
STREAM_CONTROL_STATUSES = STREAM_ACTIVE_STATUSES | {"completed"}
DEFAULT_SPEED = 1.0
MAX_SPEED = 20.0
MAX_GRAPH_NODES = 500
MAX_GRAPH_EDGES = 2000

_worker_lock = threading.RLock()
_active_stream_workers: set[str] = set()


class StreamError(ValueError):
    """A user-facing stream error with a stable machine-readable code."""

    def __init__(self, message: str, code: str = "STREAM_INVALID", **extra: Any):
        super().__init__(message)
        self.code = code
        self.extra = extra


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _finite_number(value: Any, field: str = "ordinal") -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise StreamError(f"{field} 必须是数值", "INVALID_EVENT") from exc
    if not math.isfinite(number):
        raise StreamError(f"{field} 不能是 NaN 或无穷大", "INVALID_EVENT")
    return number


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _event_key(replay_id: str, event_id: str) -> str:
    return f"{replay_id}:{event_id}"


def _episode(row: dict[str, Any]) -> str:
    return str(row.get("episode_id") or row.get("_series_id") or "unknown_episode").strip()


def _source_time(row: dict[str, Any], index: int, column: str = "time_s") -> float:
    value = row.get(column)
    if value in (None, ""):
        raise StreamError(f"缺少 {column}", "INVALID_EVENT", row_index=index)
    return _finite_number(value, column)


def _row_id(row: dict[str, Any], index: int) -> str:
    value = row.get("_source_row_index", row.get("source_row_id", index))
    return str(value)


def _source_order(row: dict[str, Any], fallback: int = 0) -> tuple[int, int | str]:
    """Return a total-order key for numeric or string source row IDs."""
    value = row.get("_source_row_index", row.get("source_row_id", fallback))
    try:
        return (0, int(value))
    except (TypeError, ValueError):
        return (1, str(value))


def _serialize_event(event: TemporalStreamEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "event_id": event.event_key,
        "event_key": event.event_key,
        "replay_id": event.replay_id,
        "episode_id": event.episode_id,
        "entity_key": event.entity_key,
        "ordinal": float(event.ordinal) if event.ordinal is not None else None,
        "source_sequence": event.source_sequence,
        "source_row_id": event.source_row_id,
        "payload": event.payload or {},
        "source_ref": event.source_ref or {},
        "payload_hash": event.payload_hash,
        "status": event.status,
        "error": event.error,
        "ingested_at": event.ingested_at.isoformat() if event.ingested_at else None,
        "committed_at": event.committed_at.isoformat() if event.committed_at else None,
    }


def _serialize_fact(fact: TemporalFact) -> dict[str, Any]:
    return {
        "id": fact.id,
        "replay_id": fact.replay_id,
        "subject_id": fact.subject_id,
        "predicate": fact.predicate,
        "object_id": fact.object_id,
        "object_value": fact.object_value,
        "state_key": fact.state_key or None,
        "valid_from_ordinal": float(fact.valid_from_ordinal) if fact.valid_from_ordinal is not None else None,
        "valid_to_ordinal": float(fact.valid_to_ordinal) if fact.valid_to_ordinal is not None else None,
        "source_event_id": fact.source_event_id,
        "evidence_ref_id": fact.evidence_ref_id,
        "status": fact.status,
        "ingested_at": fact.ingested_at.isoformat() if fact.ingested_at else None,
        "expired_at": fact.expired_at.isoformat() if fact.expired_at else None,
    }


def _serialize_snapshot(snapshot: DataModelSnapshot) -> dict[str, Any]:
    return {
        "id": snapshot.id,
        "ontology_id": snapshot.ontology_id,
        "schema_revision_id": snapshot.schema_revision_id,
        "replay_id": snapshot.replay_id,
        "dataset_version_id": snapshot.dataset_version_id,
        "graph_namespace": snapshot.graph_namespace,
        "through_ordinal": float(snapshot.through_ordinal) if snapshot.through_ordinal is not None else None,
        "event_count": snapshot.event_count,
        "node_count": snapshot.node_count,
        "edge_count": snapshot.edge_count,
        "fact_count": snapshot.fact_count,
        "evidence_count": snapshot.evidence_count,
        "snapshot_hash": snapshot.snapshot_hash,
        "status": snapshot.status,
        "created_at": snapshot.created_at.isoformat() if snapshot.created_at else None,
        "published_at": snapshot.published_at.isoformat() if snapshot.published_at else None,
    }


def serialize_stream(replay: TemporalReplay, *, db: Session | None = None) -> dict[str, Any]:
    """Return a JSON-safe stream status payload shared by every endpoint."""
    live_mode = replay.source_mode == "simulated_live"
    metrics = dict(replay.metrics or {})
    total = int(replay.total_events or replay.selected_rows or 0)
    committed = int(replay.committed_events or replay.normalized_rows or 0)
    if live_mode:
        # A simulated live source deliberately does not expose its future
        # horizon.  ``total_events`` is the number received so far, not a
        # prediction of the file length; the public payload uses the explicit
        # received_events field instead of a progress percentage.
        metrics.setdefault("events_received", committed)
        metrics.pop("events_total", None)
        metrics.pop("queue", None)
        progress: dict[str, Any] = {"stage": _stage(replay), "completed": committed}
        public_total: int | None = None
    else:
        metrics.setdefault("events_total", total)
        metrics.setdefault("events_committed", committed)
        metrics.setdefault("queue", max(0, total - committed))
        progress = {"pct": round(committed / total * 100, 2) if total else 0.0, "completed": committed, "total": total, "stage": _stage(replay)}
        public_total = total
    latest_event = None
    last_received_at = None
    if db is not None:
        latest_event_row = db.query(TemporalStreamEvent).filter(
            TemporalStreamEvent.replay_id == replay.id,
            TemporalStreamEvent.status == "committed",
        ).order_by(TemporalStreamEvent.source_sequence.desc()).first()
        if latest_event_row:
            latest_event = _serialize_event(latest_event_row)
            last_received_at = latest_event_row.committed_at.isoformat() if latest_event_row.committed_at else None
    config = dict(replay.config or {})
    public_config = config
    if live_mode:
        # Keep the internal selection cursor available to the worker, but do
        # not leak a user-selected end boundary (or a source-file horizon) in
        # the blind-stream response.  The UI can only expose ordinals that
        # have already arrived.
        public_config = dict(config)
        selection = dict(public_config.get("selection") or {})
        selection.pop("start_ordinal", None)
        selection.pop("end_ordinal", None)
        public_config["selection"] = selection
    payload: dict[str, Any] = {
        "id": replay.id,
        "run_id": replay.id,
        "replay_id": replay.id,
        "ontology_id": replay.ontology_id,
        "dataset_id": replay.dataset_id,
        "dataset_version_id": replay.dataset_version_id,
        "source_id": replay.source_id,
        "source_mode": replay.source_mode,
        "status": replay.status,
        "schema_revision_id": replay.schema_revision_id,
        "graph_namespace": replay.graph_namespace,
        "time_kind": replay.time_kind,
        "entity_column": replay.entity_column,
        "time_column": replay.time_column,
        "episode_ids": replay.series_ids or [],
        "series_ids": replay.series_ids or [],
        "start_ordinal": None if live_mode else replay.start_time,
        "end_ordinal": None if live_mode else replay.end_time,
        "start_time": None if live_mode else replay.start_time,
        "end_time": None if live_mode else replay.end_time,
        "current_ordinal": replay.current_time,
        "current_time": replay.current_time,
        "current_event_index": replay.current_event_index,
        "current_batch_index": replay.current_batch_index,
        "total_events": public_total,
        "committed_events": committed,
        "total_batches": None if live_mode else replay.total_batches,
        "source_rows": None if live_mode else replay.source_rows,
        "selected_rows": None if live_mode else replay.selected_rows,
        "normalized_rows": replay.normalized_rows,
        "watermark_ordinal": float(replay.watermark_ordinal) if replay.watermark_ordinal is not None else None,
        "watermark_sequence": replay.watermark_sequence,
        "event_interval_ms": replay.event_interval_ms,
        "speed": replay.speed,
        "progress": progress,
        "metrics": metrics,
        "config": public_config,
        "horizon_known": not live_mode,
        "received_events": committed,
        "first_received_ordinal": _float_or_none(config.get("first_received_ordinal")) if live_mode else _float_or_none(replay.start_time),
        "last_received_at": last_received_at,
        "source_exhausted": bool(config.get("source_exhausted", False)) if live_mode else None,
        "state": replay.state or {},
        "pause_requested": bool(replay.pause_requested),
        "step_requested": bool(replay.step_requested),
        "cancel_requested": bool(replay.cancel_requested),
        "published_snapshot_id": replay.published_snapshot_id,
        "published_at": replay.published_at.isoformat() if replay.published_at else None,
        "error": replay.error,
        "created_at": replay.created_at.isoformat() if replay.created_at else None,
        "started_at": replay.started_at.isoformat() if replay.started_at else None,
        "completed_at": replay.completed_at.isoformat() if replay.completed_at else None,
        "updated_at": replay.updated_at.isoformat() if replay.updated_at else None,
    }
    payload["latest_event"] = latest_event
    return payload


def _stage(replay: TemporalReplay) -> str:
    if replay.status == "created":
        return "ready"
    if replay.status in {"queued", "running", "pausing", "paused"}:
        return "event_projection"
    if replay.status == "completed":
        return "awaiting_publish"
    if replay.status == "published":
        return "published"
    return replay.status


def _load_rows(db: Session, version: DatasetVersion) -> list[dict[str, Any]]:
    if not version.storage_uri:
        raise StreamError("FactoryNet 数据版本没有可读取对象", "SOURCE_NOT_READY")
    try:
        raw = get_storage_service().get_object(version.storage_uri)
    except Exception as exc:
        raise StreamError(f"FactoryNet 源文件无法读取：{exc}", "STORAGE_OBJECT_MISSING") from exc
    from app.routers.v2.temporal import parse_temporal_bytes
    rows = parse_temporal_bytes(raw)
    for index, row in enumerate(rows):
        row.setdefault("_source_row_index", index)
    return rows


def _resolve_source(db: Session, dataset_id: str | None, version_id: str | None) -> tuple[Dataset, DatasetVersion, list[dict[str, Any]]]:
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first() if dataset_id else find_factorynet_dataset(db)
    if not dataset:
        raise StreamError("请先安装 FactoryNet CNC 官方样例", "SOURCE_NOT_INSTALLED")
    manifest = dict(dataset.schema_json or {})
    if manifest.get("source_id") not in {None, FACTORYNET_SOURCE_ID} and dataset.name != "FactoryNet CNC 铣削时序数据":
        raise StreamError("该数据版本不是 FactoryNet CNC 官方样例", "WRONG_SOURCE")
    version = db.query(DatasetVersion).filter(DatasetVersion.id == version_id).first() if version_id else None
    if version is None and dataset.latest_version_id:
        version = db.query(DatasetVersion).filter(DatasetVersion.id == dataset.latest_version_id).first()
    if version is None:
        version = db.query(DatasetVersion).filter(DatasetVersion.dataset_id == dataset.id).order_by(DatasetVersion.version_no.desc()).first()
    if not version:
        raise StreamError("FactoryNet 数据版本不存在", "SOURCE_NOT_READY")
    return dataset, version, _load_rows(db, version)


def _ensure_factorynet_schema(db: Session, ontology_id: str) -> None:
    """Ensure the deterministic FactoryNet vocabulary exists.

    Existing temporal ontologies already have an approved schema.  Do not
    rewrite their property definitions merely because a new stream run is
    opened; only an empty/partial legacy project is seeded.
    """
    from app.models.entity import Entity
    from app.tasks.v2.temporal_construction import _ensure_factorynet_schema as ensure_schema
    required = {"Machine", "Episode", "Observation", "ProcessPhase", "ToolCondition", "SensorChannel", "InspectionResult"}
    present = {
        str(item.name_en or item.canonical_id or "")
        for item in db.query(Entity).filter(Entity.ontology_id == ontology_id).all()
    }
    if not required.issubset(present):
        ensure_schema(db, ontology_id)


def ensure_schema_revision(db: Session, project: OntologyProject) -> OntologyRevision:
    """Pin a lightweight immutable schema revision for a stream run.

    Some of the earliest FactoryNet demo ontologies were created before the
    revision service ran and therefore have no ``current_revision_id``.  A
    stream still needs a concrete schema boundary for publish-time conflict
    detection, so create revision 1 locally when necessary.  The snapshot is
    retained in PostgreSQL; later editor/revision flows may attach an object
    storage URI without changing the pinned content.
    """
    if project.current_revision_id:
        current = db.query(OntologyRevision).filter(
            OntologyRevision.id == project.current_revision_id,
            OntologyRevision.ontology_id == project.id,
        ).first()
        if current:
            return current
    current = db.query(OntologyRevision).filter(
        OntologyRevision.ontology_id == project.id,
        OntologyRevision.is_current.is_(True),
    ).order_by(OntologyRevision.revision_no.desc()).first()
    if current:
        project.current_revision_id = current.id
        return current
    from app.services.v2.revision_service import snapshot_ontology
    snapshot = snapshot_ontology(db, project.id)
    digest = hashlib.sha256(_json(snapshot).encode("utf-8")).hexdigest()
    latest = db.query(OntologyRevision.revision_no).filter(
        OntologyRevision.ontology_id == project.id,
    ).order_by(OntologyRevision.revision_no.desc()).first()
    revision_no = int(latest[0]) + 1 if latest else 1
    revision = OntologyRevision(
        id=str(uuid.uuid4()),
        ontology_id=project.id,
        revision_no=revision_no,
        graph_namespace=f"ontology:{project.id}:r{revision_no}",
        snapshot_json=snapshot,
        snapshot_hash=digest,
        summary={
            "entity_count": len(snapshot.get("entities", [])),
            "relation_count": len(snapshot.get("relations", [])),
            "logic_count": len(snapshot.get("logic_rules", [])),
            "instance_count": int(snapshot.get("instance_count", 0)),
            "stream_schema_bootstrap": True,
        },
        status="current",
        is_current=True,
        created_at=_now(),
    )
    db.add(revision)
    project.current_revision_id = revision.id
    project.version = f"r{revision_no}"
    db.flush()
    return revision


def _select_rows(
    rows: list[dict[str, Any]],
    *,
    episode_ids: Iterable[str] | None = None,
    start_ordinal: float | None = None,
    end_ordinal: float | None = None,
    time_column: str = "time_s",
    max_records: int | None = None,
) -> list[tuple[dict[str, Any], float, str]]:
    wanted = {str(value).strip() for value in (episode_ids or []) if str(value).strip()}
    selected: list[tuple[dict[str, Any], float, str]] = []
    for index, original in enumerate(rows):
        row = dict(original)
        episode = _episode(row)
        if wanted and episode not in wanted:
            continue
        try:
            ordinal = _source_time(row, index, time_column)
        except StreamError as exc:
            # Do not silently discard a source row.  A dynamic run must either
            # index every selected observation or fail with the exact source
            # row and field so the caller can repair the input.
            raise StreamError(f"源记录 {index} 的 {time_column} 无效：{exc}", "INVALID_EVENT", row_index=index) from exc
        if start_ordinal is not None and ordinal < start_ordinal:
            continue
        if end_ordinal is not None and ordinal > end_ordinal:
            continue
        selected.append((row, ordinal, _row_id(row, index)))
    selected.sort(key=lambda item: (item[1], _episode(item[0]), _source_order(item[0])))
    if max_records is not None:
        selected = selected[: max(1, min(int(max_records), len(selected)))]
    return selected


def create_stream_run(
    db: Session,
    ontology_id: str,
    *,
    dataset_id: str | None = None,
    dataset_version_id: str | None = None,
    episode_ids: list[str] | None = None,
    start_ordinal: float | None = None,
    end_ordinal: float | None = None,
    speed: float = DEFAULT_SPEED,
    time_column: str = "time_s",
    entity_column: str = "episode_id",
    source_mode: str = "file_replay",
    config: dict[str, Any] | None = None,
    created_by: str | None = None,
) -> TemporalReplay:
    """Create a queued event index; no instance graph is written yet."""
    project = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    if not project:
        raise StreamError("本体不存在", "ONTOLOGY_NOT_FOUND")
    if str(getattr(project, "data_class", "regular")) != "temporal":
        raise StreamError("动态演化只能用于时序本体", "DATA_CLASS_MISMATCH")
    if source_mode not in {"file_replay", "push", "simulated_live"}:
        raise StreamError("source_mode 必须是 file_replay、push 或 simulated_live", "INVALID_SOURCE_MODE")
    speed = _finite_number(speed, "speed")
    if speed <= 0 or speed > MAX_SPEED:
        raise StreamError(f"speed 必须在 0 和 {MAX_SPEED} 之间", "INVALID_SPEED")
    if source_mode == "push":
        if not dataset_id and not dataset_version_id:
            dataset = None
            version = None
            rows: list[dict[str, Any]] = []
        else:
            dataset, version, rows = _resolve_source(db, dataset_id, dataset_version_id)
    else:
        dataset, version, rows = _resolve_source(db, dataset_id, dataset_version_id)
    available_episodes = {_episode(row) for row in rows}
    requested_episode_ids = {str(value).strip() for value in (episode_ids or []) if str(value).strip()}
    unknown = sorted(requested_episode_ids - available_episodes)
    # A push stream may introduce an episode that is not present in the
    # optional FactoryNet catalog.  File replay selections remain strict, but
    # rejecting a push-only episode here would make the documented event
    # contract unusable for a fresh sensor/source integration.
    if unknown and source_mode != "push":
        raise StreamError(f"不存在的 episode_id：{unknown[0]}", "UNKNOWN_EPISODE", episode_id=unknown[0])
    # A file replay is intentionally scoped to one complete episode unless the
    # caller explicitly selects episodes.  This keeps the first dynamic demo
    # small enough to inspect while still preserving every event in that
    # episode (the optional max_records limit is applied afterwards).
    effective_episode_ids = [str(value).strip() for value in (episode_ids or []) if str(value).strip()]
    if source_mode in {"file_replay", "simulated_live"} and not effective_episode_ids:
        candidates = _select_rows(
            rows,
            episode_ids=None,
            start_ordinal=start_ordinal,
            end_ordinal=end_ordinal,
            time_column=time_column,
            max_records=None,
        )
        episode_order: list[str] = []
        for candidate, _ordinal, _source_row_id in candidates:
            episode = _episode(candidate)
            if episode not in episode_order:
                episode_order.append(episode)
        if episode_order:
            effective_episode_ids = [episode_order[0]]
    selected = _select_rows(
        rows,
        episode_ids=effective_episode_ids,
        start_ordinal=start_ordinal,
        end_ordinal=end_ordinal,
        time_column=time_column,
        max_records=(config or {}).get("max_records"),
    )
    if source_mode in {"file_replay", "simulated_live"} and not selected:
        raise StreamError("所选 episode 或 Ordinal 范围没有可用记录", "NO_EVENTS")
    selected_episodes = sorted({ _episode(row) for row, _, _ in selected })
    # The live demo needs the first episode to be known, but must not reveal or
    # persist its future start/end horizon.  The source cursor will discover
    # the first actual Ordinal only when the first event arrives.
    if source_mode == "simulated_live":
        selected_episodes = [effective_episode_ids[0]] if effective_episode_ids else selected_episodes[:1]
    if source_mode != "simulated_live" and start_ordinal is None and selected:
        start_ordinal = selected[0][1]
    if source_mode != "simulated_live" and end_ordinal is None and selected:
        end_ordinal = selected[-1][1]
    replay_id = str(uuid.uuid4())
    namespace = f"stream_{uuid.uuid4().hex}"
    cfg = dict(config or {})
    explicit_interval = cfg.get("explicit_event_interval_ms") or cfg.get("event_interval_ms")
    try:
        explicit_interval = max(1, min(int(explicit_interval), 60000)) if explicit_interval is not None else None
    except (TypeError, ValueError):
        explicit_interval = None
    cfg.update({
        "source_id": FACTORYNET_SOURCE_ID,
        "source_file": (dict(dataset.schema_json or {}).get("filename") if dataset else None) or "FactoryNet CNC",
        "source_checksum": version.checksum if version else None,
        "current_state_dimensions": cfg.get("current_state_dimensions") or ["phase", "tool_condition", "inspection"],
        "event_order": "ordinal,source_sequence",
        "selection": {
            "episode_ids": selected_episodes,
            "start_ordinal": start_ordinal if source_mode != "simulated_live" else (config or {}).get("start_ordinal", start_ordinal),
            "end_ordinal": end_ordinal if source_mode != "simulated_live" else (config or {}).get("end_ordinal", end_ordinal),
            "time_column": time_column,
        },
    })
    if source_mode == "simulated_live":
        cfg.update({
            "horizon_known": False,
            "source_cursor": 0,
            "source_exhausted": False,
            "first_received_ordinal": None,
        })
    live_mode = source_mode == "simulated_live"
    replay = TemporalReplay(
        id=replay_id,
        ontology_id=ontology_id,
        dataset_id=dataset.id if dataset else dataset_id,
        dataset_version_id=version.id if version else dataset_version_id,
        source_id=FACTORYNET_SOURCE_ID,
        source_mode=source_mode,
        schema_revision_id=project.current_revision_id,
        graph_namespace=namespace,
        status="created",
        time_kind="ordinal",
        entity_column=entity_column,
        time_column=time_column,
        series_ids=selected_episodes or [str(v) for v in (episode_ids or [])],
        start_time=start_ordinal,
        end_time=end_ordinal,
        window_seconds=1.0,
        speed=speed,
        event_interval_ms=explicit_interval or max(1, int(round(1000 / speed))),
        current_time=None,
        current_batch_index=-1,
        total_batches=0 if live_mode else len(selected),
        source_rows=0 if live_mode else len(rows),
        selected_rows=0 if live_mode else len(selected),
        normalized_rows=0,
        total_events=0 if live_mode else len(selected),
        committed_events=0,
        current_event_index=-1,
        metrics={"events_received": 0, **({} if live_mode else {"events_total": len(selected), "events_committed": 0, "queue": len(selected)}), "nodes_written": 0, "edges_written": 0, "facts_written": 0, "state_transitions": 0, "model_calls": 0},
        config=cfg,
        state={"builder": {}, "episodes": {}, "latest_event": None},
        error=None,
        pause_requested=False,
        step_requested=False,
        cancel_requested=False,
    )
    db.add(replay)
    db.flush()
    if not live_mode:
        for source_sequence, (row, ordinal, source_row_id) in enumerate(selected):
            episode = _episode(row)
            event_id = f"factorynet:{episode}:{source_row_id}"
            payload = {key: value for key, value in row.items() if not str(key).startswith("_")}
            db.add(TemporalStreamEvent(
                id=str(uuid.uuid4()),
                replay_id=replay.id,
                event_key=event_id,
                episode_id=episode,
                entity_key=str(row.get("entity_key") or row.get("machine_type") or episode),
                ordinal=Decimal(str(ordinal)),
                source_sequence=source_sequence,
                source_row_id=source_row_id,
                payload=payload,
                source_ref={"dataset_id": replay.dataset_id, "dataset_version_id": replay.dataset_version_id, "source_row_id": source_row_id, "source_file": cfg.get("source_file")},
                payload_hash=_hash(payload),
                status="queued",
            ))
    _ensure_factorynet_schema(db, ontology_id)
    ensure_schema_revision(db, project)
    replay.schema_revision_id = project.current_revision_id
    db.commit()
    db.refresh(replay)
    return replay


def _ensure_construction_run(db: Session, replay: TemporalReplay) -> ConstructionRun:
    run_id = (replay.config or {}).get("construction_run_id")
    run = db.query(ConstructionRun).filter(ConstructionRun.id == run_id).first() if run_id else None
    if run:
        return run
    run = ConstructionRun(
        id=str(uuid.uuid4()), ontology_id=replay.ontology_id, dataset_id=replay.dataset_id,
        mode="temporal_stream", status="running", revision_id=replay.schema_revision_id,
        config={"replay_id": replay.id, "graph_namespace": replay.graph_namespace},
        progress={"stage": "动态事件投影", "completed": replay.committed_events, "total": replay.total_events}, metrics={},
    )
    db.add(run)
    replay.config = {**(replay.config or {}), "construction_run_id": run.id}
    db.flush()
    return run


def _evidence_for_event(db: Session, replay: TemporalReplay, event: TemporalStreamEvent, payload: dict[str, Any]) -> EvidenceRef:
    run = _ensure_construction_run(db, replay)
    evidence_id = f"stream-evidence:{replay.id}:{event.id}"
    existing = db.query(EvidenceRef).filter(EvidenceRef.id == evidence_id).first()
    if existing:
        return existing
    text = _json(payload)[:8000]
    evidence = EvidenceRef(
        id=evidence_id, construction_run_id=run.id, ontology_id=replay.ontology_id,
        assertion_id=f"event:{event.event_key}", assertion_kind="node",
        source_dataset_id=replay.dataset_id,
        source_version=str((replay.config or {}).get("source_checksum") or replay.dataset_version_id or ""),
        source_file=(replay.config or {}).get("source_file"), source_row_id=event.source_row_id,
        revision_id=replay.schema_revision_id, extractor="rule", confidence=1.0,
        confidence_method="factorynet_temporal_stream", evidence_text=text,
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )
    db.add(evidence)
    db.flush()
    return evidence


def _fact_id(replay_id: str, subject: str, predicate: str, obj: str | None, state_key: str, valid_from: Any) -> str:
    return "fact:" + hashlib.sha256(_json([replay_id, subject, predicate, obj, state_key, str(valid_from)]).encode("utf-8")).hexdigest()


def _close_state_fact(db: Session, replay: TemporalReplay, subject_id: str, state_key: str, ordinal: float, graph: FalkorDBService | None) -> TemporalFact | None:
    existing = db.query(TemporalFact).filter(
        TemporalFact.replay_id == replay.id,
        TemporalFact.subject_id == subject_id,
        TemporalFact.state_key == state_key,
        TemporalFact.valid_to_ordinal.is_(None),
        TemporalFact.status == "active",
    ).order_by(TemporalFact.valid_from_ordinal.desc()).first()
    if not existing:
        return None
    existing.valid_to_ordinal = Decimal(str(ordinal))
    existing.expired_at = _now()
    existing.status = "expired"
    if graph and getattr(graph, "available", False):
        try:
            graph.expire_relation_by_fact_id(replay.ontology_id, existing.id, ordinal, graph_namespace=replay.graph_namespace)
        except TypeError:
            try:
                graph.expire_relation_by_fact_id(replay.ontology_id, existing.id, ordinal)
            except Exception:
                pass
        except Exception:
            pass
    return existing


def _add_fact(
    db: Session,
    replay: TemporalReplay,
    *,
    subject_id: str,
    predicate: str,
    object_id: str | None,
    object_value: Any = None,
    state_key: str = "",
    ordinal: float,
    event: TemporalStreamEvent,
    evidence: EvidenceRef,
    graph: FalkorDBService | None,
) -> TemporalFact:
    fact_key = _fact_id(replay.id, subject_id, predicate, object_id, state_key, ordinal if state_key == "" else event.id)
    existing = db.get(TemporalFact, fact_key)
    if existing:
        return existing
    fact = TemporalFact(
        id=fact_key, replay_id=replay.id, subject_id=subject_id, predicate=predicate,
        object_id=object_id, object_value=object_value if object_value is not None else None,
        state_key=state_key, valid_from_ordinal=Decimal(str(ordinal)), valid_to_ordinal=None,
        source_event_id=event.id, evidence_ref_id=evidence.id, status="active",
    )
    db.add(fact)
    db.flush()
    return fact


def _graph_upsert_instances(graph: Any, ontology_id: str, nodes: list[dict[str, Any]], namespace: str) -> int:
    if not graph or not getattr(graph, "available", False):
        return 0
    try:
        return int(graph.upsert_instances(ontology_id, nodes, graph_namespace=namespace) or 0)
    except TypeError:
        return int(graph.upsert_instances(ontology_id, nodes) or 0)


def _graph_upsert_relations(graph: Any, ontology_id: str, edges: list[dict[str, Any]], namespace: str) -> int:
    if not graph or not getattr(graph, "available", False):
        return 0
    try:
        return int(graph.upsert_relations(ontology_id, edges, graph_namespace=namespace) or 0)
    except TypeError:
        return int(graph.upsert_relations(ontology_id, edges) or 0)


def _state_object(row: dict[str, Any], kind: str) -> tuple[str, str] | None:
    values = {
        "phase": ("ctx_process_phase", "FactoryNet:ProcessPhase:"),
        "tool_condition": ("ctx_tool_condition", "FactoryNet:ToolCondition:"),
        "inspection": ("ctx_passed_visual_inspection", "FactoryNet:InspectionResult:"),
    }
    source, prefix = values[kind]
    value = row.get(source)
    if value in (None, ""):
        return None
    text = str(value).strip()
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
    return prefix + digest, text


def process_one_event(db: Session, replay: TemporalReplay, event: TemporalStreamEvent, *, commit: bool = True) -> dict[str, Any]:
    """Project exactly one observation and migrate its current-state facts."""
    if event.replay_id != replay.id:
        raise StreamError("事件不属于当前动态运行", "EVENT_RUN_MISMATCH")
    if event.status == "committed":
        return {"event": _serialize_event(event), "idempotent": True, "facts": []}
    ordinal = _finite_number(event.ordinal)
    watermark = _float_or_none(replay.watermark_ordinal)
    sequence = int(event.source_sequence or 0)
    if watermark is not None:
        current_pair = (watermark, int(replay.watermark_sequence or -1))
        if (ordinal, sequence) <= current_pair:
            raise StreamError("事件早于或等于当前水位，不能在本版本重算", "LATE_EVENT_NOT_SUPPORTED", watermark_ordinal=watermark, watermark_sequence=replay.watermark_sequence)
    payload = dict(event.payload or {})
    payload.setdefault("episode_id", event.episode_id)
    payload.setdefault("_source_row_index", event.source_row_id or sequence)
    time_column = replay.time_column or "time_s"
    payload[time_column] = payload.get(time_column, ordinal)
    normalized, issues = normalize_temporal_rows([payload], TemporalConfig(time_kind="ordinal", sequence_column=time_column))
    if issues or not normalized:
        message = issues[0].get("error") if issues else "记录没有有效 Ordinal"
        event.status = "failed"
        event.error = str(message)
        replay.status = "failed"
        replay.error = str(message)
        if commit:
            db.commit()
        raise StreamError(str(message), "INVALID_EVENT")
    row = normalized[0]
    state_payload = dict(replay.state or {})
    builder = FactoryNetIncrementalState.from_dict(state_payload.get("builder") or {})
    row["_replay_event_seq"] = builder.sequence_by_episode.get(event.episode_id, 0)
    nodes, structural_edges, builder = build_factorynet_instances_incremental([row], builder, sequence_column=time_column)
    graph = FalkorDBService()
    evidence = _evidence_for_event(db, replay, event, payload)
    namespace = replay.graph_namespace or f"stream_{replay.id.replace('-', '')}"
    graph_nodes: list[dict[str, Any]] = []
    for node in nodes:
        props = dict(node.get("properties") or {})
        props.update({"_replay_id": replay.id, "_event_id": event.id, "event_ordinal": ordinal, "_graph_namespace": namespace})
        graph_nodes.append({**node, "properties": props})
    graph_edges: list[dict[str, Any]] = []
    facts: list[TemporalFact] = []
    for edge in structural_edges:
        props = dict(edge.get("properties") or {})
        props.update({"_replay_id": replay.id, "_event_id": event.id, "event_ordinal": ordinal, "valid_from_ordinal": ordinal, "_graph_namespace": namespace})
        fact = _add_fact(db, replay, subject_id=str(edge["source"]), predicate=str(edge.get("type") or "RELATED"), object_id=str(edge["target"]), ordinal=ordinal, event=event, evidence=evidence, graph=graph)
        props["_fact_id"] = fact.id
        graph_edges.append({**edge, "properties": props})
        facts.append(fact)

    episode_node = f"FactoryNet:Episode:{event.episode_id}"
    observation_node = f"FactoryNet:Observation:{event.episode_id}:{event.source_row_id or sequence}"
    state_map = dict(state_payload.get("episodes") or {})
    episode_state = dict(state_map.get(event.episode_id) or {})
    # LATEST_OBSERVATION is a new fact for each submitted event.
    previous_latest = _close_state_fact(db, replay, episode_node, "latest_observation", ordinal, graph)
    latest = _add_fact(db, replay, subject_id=episode_node, predicate="LATEST_OBSERVATION", object_id=observation_node, state_key="latest_observation", ordinal=ordinal, event=event, evidence=evidence, graph=graph)
    latest_props = {"_fact_id": latest.id, "_event_id": event.id, "event_ordinal": ordinal, "valid_from_ordinal": ordinal, "_replay_id": replay.id, "_graph_namespace": namespace}
    graph_edges.append({"source": episode_node, "target": observation_node, "type": "LATEST_OBSERVATION", "properties": latest_props})
    episode_state["latest_observation"] = {"value": observation_node, "fact_id": latest.id, "ordinal": ordinal}
    if previous_latest:
        episode_state["latest_previous_fact_id"] = previous_latest.id

    transitions = 0
    transition_config = {"phase": ("CURRENT_PHASE", "phase"), "tool_condition": ("CURRENT_TOOL_CONDITION", "tool_condition"), "inspection": ("CURRENT_INSPECTION", "inspection")}
    for state_key, (predicate, label) in transition_config.items():
        value_pair = _state_object(row, state_key)
        if value_pair is None:
            continue
        object_id, display_value = value_pair
        prior = dict(episode_state.get(state_key) or {})
        if prior.get("object_id") == object_id:
            # The fact remains valid; only the latest event evidence changes.
            continue
        _close_state_fact(db, replay, episode_node, state_key, ordinal, graph)
        current = _add_fact(db, replay, subject_id=episode_node, predicate=predicate, object_id=object_id, object_value={"value": display_value}, state_key=state_key, ordinal=ordinal, event=event, evidence=evidence, graph=graph)
        graph_edges.append({"source": episode_node, "target": object_id, "type": predicate, "properties": {"_fact_id": current.id, "_event_id": event.id, "event_ordinal": ordinal, "valid_from_ordinal": ordinal, "_replay_id": replay.id, "_graph_namespace": namespace, "value": display_value}})
        episode_state[state_key] = {"object_id": object_id, "value": display_value, "fact_id": current.id, "ordinal": ordinal}
        transitions += 1

    graph_nodes_written = _graph_upsert_instances(graph, replay.ontology_id, graph_nodes, namespace)
    graph_edges_written = _graph_upsert_relations(graph, replay.ontology_id, graph_edges, namespace)
    state_payload["builder"] = builder.to_dict()
    state_map[event.episode_id] = episode_state
    state_payload["episodes"] = state_map
    state_payload["latest_event"] = {"id": event.id, "event_key": event.event_key, "episode_id": event.episode_id, "ordinal": ordinal, "source_sequence": sequence, "payload": payload}
    replay.state = state_payload
    if replay.source_mode == "simulated_live":
        live_config = dict(replay.config or {})
        if live_config.get("first_received_ordinal") is None:
            live_config["first_received_ordinal"] = ordinal
        replay.config = live_config
    replay.current_time = ordinal
    replay.watermark_ordinal = Decimal(str(ordinal))
    replay.watermark_sequence = sequence
    replay.current_event_index = sequence
    replay.current_batch_index = sequence
    replay.committed_events = int(replay.committed_events or 0) + 1
    replay.normalized_rows = int(replay.normalized_rows or 0) + 1
    metrics = dict(replay.metrics or {})
    metrics.update({"events_total": replay.total_events, "events_committed": replay.committed_events, "events_received": replay.committed_events, "queue": max(0, replay.total_events - replay.committed_events), "nodes_written": int(metrics.get("nodes_written", 0)) + graph_nodes_written, "edges_written": int(metrics.get("edges_written", 0)) + graph_edges_written, "facts_written": int(metrics.get("facts_written", 0)) + len(facts) + 1 + transitions, "state_transitions": int(metrics.get("state_transitions", 0)) + transitions, "last_event_id": event.id, "last_event_ordinal": ordinal, "last_payload_hash": event.payload_hash})
    replay.metrics = metrics
    event.status = "committed"
    event.committed_at = _now()
    event.error = None
    # Keep the legacy batch checkpoint truthful when this event came from the
    # compatibility replay endpoint.  The dynamic worker remains event based,
    # but old clients can still inspect which batch is complete.
    source_ref = dict(event.source_ref or {})
    batch_value = source_ref.get("batch_no")
    if batch_value not in (None, ""):
        try:
            batch_no = int(batch_value)
        except (TypeError, ValueError):
            batch_no = None
        if batch_no is not None:
            batch = db.query(TemporalReplayBatch).filter(
                TemporalReplayBatch.replay_id == replay.id,
                TemporalReplayBatch.batch_no == batch_no,
            ).first()
            if batch:
                batch.status = "running"
                batch.normalized_rows = int(batch.normalized_rows or 0) + 1
                batch.nodes_written = int(batch.nodes_written or 0) + graph_nodes_written
                batch.edges_written = int(batch.edges_written or 0) + graph_edges_written
                batch.started_at = batch.started_at or _now()
                batch_event_rows = db.query(TemporalStreamEvent).filter(
                    TemporalStreamEvent.replay_id == replay.id,
                ).all()
                batch_events = [
                    item for item in batch_event_rows
                    if int((item.source_ref or {}).get("batch_no", -1)) == batch_no
                ]
                if batch_events and all(item.status == "committed" for item in batch_events):
                    batch.status = "completed"
                    batch.completed_at = _now()
                replay.current_batch_index = batch_no
                metrics["committed_batches"] = db.query(TemporalReplayBatch).filter(
                    TemporalReplayBatch.replay_id == replay.id,
                    TemporalReplayBatch.status == "completed",
                ).count()
    replay.updated_at = _now()
    if commit:
        db.commit()
        db.refresh(replay)
    return {"event": _serialize_event(event), "facts": [_serialize_fact(item) for item in facts], "nodes_written": graph_nodes_written, "edges_written": graph_edges_written, "state_transitions": transitions}


def _next_event(db: Session, replay_id: str) -> TemporalStreamEvent | None:
    return db.query(TemporalStreamEvent).filter(
        TemporalStreamEvent.replay_id == replay_id,
        TemporalStreamEvent.status == "queued",
    ).order_by(
        TemporalStreamEvent.ordinal.asc(),
        TemporalStreamEvent.source_sequence.asc(),
        TemporalStreamEvent.id.asc(),
    ).first()


def _next_simulated_live_event(db: Session, replay: TemporalReplay) -> TemporalStreamEvent | None:
    """Materialize only the next source row for the blind live demo.

    The source file is read by the producer, but future rows are never written
    to the event table or returned by the API.  A JSON cursor is sufficient for
    this deterministic local demo and survives worker restarts without adding a
    migration just for a source-specific offset.
    """
    if replay.source_mode != "simulated_live":
        return None
    config = dict(replay.config or {})
    if config.get("source_exhausted"):
        return None
    _dataset, version, rows = _resolve_source(db, replay.dataset_id, replay.dataset_version_id)
    selection = dict(config.get("selection") or {})
    episode_ids = [str(item) for item in (selection.get("episode_ids") or replay.series_ids or []) if str(item).strip()]
    candidates = _select_rows(
        rows,
        episode_ids=episode_ids,
        start_ordinal=_float_or_none(selection.get("start_ordinal")),
        end_ordinal=_float_or_none(selection.get("end_ordinal")),
        time_column=replay.time_column or "time_s",
        max_records=None,
    )
    cursor = max(0, int(config.get("source_cursor") or 0))
    if cursor >= len(candidates):
        config["source_exhausted"] = True
        replay.config = config
        replay.updated_at = _now()
        return None
    row, ordinal, source_row_id = candidates[cursor]
    episode = _episode(row)
    payload = {key: value for key, value in row.items() if not str(key).startswith("_")}
    event = TemporalStreamEvent(
        id=str(uuid.uuid4()),
        replay_id=replay.id,
        event_key=f"factorynet:{episode}:{source_row_id}",
        episode_id=episode,
        entity_key=str(row.get("entity_key") or row.get("machine_type") or episode),
        ordinal=Decimal(str(ordinal)),
        source_sequence=cursor,
        source_row_id=source_row_id,
        payload=payload,
        source_ref={
            "dataset_id": replay.dataset_id,
            "dataset_version_id": replay.dataset_version_id,
            "source_row_id": source_row_id,
            "source_file": config.get("source_file"),
            "source_mode": "simulated_live",
        },
        payload_hash=_hash(payload),
        status="queued",
    )
    db.add(event)
    db.flush()
    # The cursor is advanced in the same transaction as the event and facts by
    # run_temporal_stream after process_one_event succeeds.
    config["source_cursor_pending"] = cursor + 1
    replay.config = config
    replay.total_events = int(replay.total_events or 0) + 1
    replay.selected_rows = int(replay.selected_rows or 0) + 1
    replay.total_batches = int(replay.total_batches or 0) + 1
    if replay.start_time is None:
        replay.start_time = ordinal
    replay.updated_at = _now()
    return event


def run_temporal_stream(replay_id: str) -> dict[str, Any]:
    """Resume a stream and commit one event per loop iteration."""
    with _worker_lock:
        if replay_id in _active_stream_workers:
            return {"run_id": replay_id, "status": "already_running"}
        _active_stream_workers.add(replay_id)
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        replay = db.query(TemporalReplay).filter(TemporalReplay.id == replay_id).first()
        if not replay:
            return {"run_id": replay_id, "status": "missing"}
        if replay.status == "published":
            return serialize_stream(replay, db=db)
        if replay.status in {"failed", "cancelled"}:
            return serialize_stream(replay, db=db)
        replay.status = "running"
        replay.started_at = replay.started_at or _now()
        replay.pause_requested = False if replay.status == "created" else replay.pause_requested
        replay.updated_at = _now()
        db.commit()
        while True:
            db.refresh(replay)
            if replay.cancel_requested:
                replay.status = "cancelled"
                replay.error = "用户取消动态运行"
                replay.completed_at = _now()
                db.commit()
                return serialize_stream(replay, db=db)
            if replay.pause_requested:
                replay.status = "paused"
                replay.updated_at = _now()
                db.commit()
                return serialize_stream(replay, db=db)
            event = _next_simulated_live_event(db, replay) if replay.source_mode == "simulated_live" else _next_event(db, replay.id)
            if event is None:
                if replay.source_mode in {"push"}:
                    # Push streams stay open between batches.  A producer can
                    # append another event later; the ingest endpoint will
                    # move the run back to queued and dispatch the worker.
                    replay.status = "paused"
                    replay.error = "等待推送事件"
                    replay.updated_at = _now()
                    db.commit()
                    return serialize_stream(replay, db=db)
                replay.status = "completed"
                replay.current_event_index = max(-1, int(replay.total_events or 0) - 1)
                replay.current_batch_index = replay.current_event_index
                replay.completed_at = _now()
                replay.updated_at = _now()
                db.commit()
                return serialize_stream(replay, db=db)
            if replay.pause_requested:
                replay.status = "paused"
                replay.updated_at = _now()
                db.commit()
                return serialize_stream(replay, db=db)
            replay.status = "running"
            db.commit()
            try:
                process_one_event(db, replay, event, commit=False)
                if replay.source_mode == "simulated_live":
                    config = dict(replay.config or {})
                    pending_cursor = config.pop("source_cursor_pending", None)
                    if pending_cursor is not None:
                        config["source_cursor"] = int(pending_cursor)
                    replay.config = config
                db.commit()
                db.refresh(replay)
            except StreamError as exc:
                db.rollback()
                replay = db.query(TemporalReplay).filter(TemporalReplay.id == replay_id).first()
                if replay:
                    replay.status = "failed"
                    replay.error = f"{exc.code}: {exc}"
                    replay.completed_at = _now()
                    db.commit()
                return serialize_stream(replay, db=db) if replay else {"run_id": replay_id, "status": "failed", "error": str(exc)}
            except Exception as exc:
                db.rollback()
                replay = db.query(TemporalReplay).filter(TemporalReplay.id == replay_id).first()
                if replay:
                    replay.status = "failed"
                    replay.error = str(exc)[:2000]
                    replay.completed_at = _now()
                    db.commit()
                return serialize_stream(replay, db=db) if replay else {"run_id": replay_id, "status": "failed", "error": str(exc)}
            db.refresh(replay)
            if replay.step_requested:
                replay.step_requested = False
                replay.pause_requested = True
                replay.status = "paused"
                replay.updated_at = _now()
                db.commit()
                return serialize_stream(replay, db=db)
            if replay.pause_requested:
                replay.status = "paused"
                replay.updated_at = _now()
                db.commit()
                return serialize_stream(replay, db=db)
            delay = max(0.01, min(60.0, float(replay.event_interval_ms or 1000) / 1000.0))
            if delay:
                time.sleep(delay)
    finally:
        db.close()
        with _worker_lock:
            _active_stream_workers.discard(replay_id)


def dispatch_stream(replay_id: str) -> str:
    if os.getenv("CELERY_ENABLED", "").lower() in {"1", "true", "yes"}:
        try:
            from app.tasks.v2.temporal_stream import run_temporal_stream_task
            run_temporal_stream_task.delay(replay_id)
            return "celery"
        except Exception:
            pass
    threading.Thread(target=run_temporal_stream, args=(replay_id,), name=f"temporal-stream-{replay_id[:8]}", daemon=True).start()
    return "thread"


def update_stream_control(db: Session, replay: TemporalReplay, action: str, speed: float | None = None) -> tuple[TemporalReplay, bool]:
    if action not in {"start", "pause", "resume", "step", "cancel"}:
        raise StreamError("action 必须是 start、pause、resume、step 或 cancel", "INVALID_CONTROL")
    if speed is not None:
        speed = _finite_number(speed, "speed")
        if speed <= 0 or speed > MAX_SPEED:
            raise StreamError(f"speed 必须在 0 和 {MAX_SPEED} 之间", "INVALID_SPEED")
        replay.speed = speed
        replay.event_interval_ms = max(1, int(round(1000 / speed)))
    if replay.status == "published" and action != "cancel":
        raise StreamError("已发布运行不能继续控制", "RUN_PUBLISHED")
    if action == "start":
        replay.cancel_requested = False; replay.pause_requested = False; replay.step_requested = False; replay.status = "queued"
        dispatch = True
    elif action == "resume":
        replay.cancel_requested = False; replay.pause_requested = False; replay.step_requested = False; replay.status = "queued"
        # A failed event is retained for audit.  Resuming explicitly requeues
        # those events so transient storage/model errors can be retried without
        # creating a second run or losing the already committed watermark.
        failed_events = db.query(TemporalStreamEvent).filter(
            TemporalStreamEvent.replay_id == replay.id,
            TemporalStreamEvent.status == "failed",
        ).all()
        for event in failed_events:
            event.status = "queued"
            event.error = None
        replay.error = None
        dispatch = True
    elif action == "step":
        replay.cancel_requested = False; replay.pause_requested = False; replay.step_requested = True; replay.status = "queued"
        dispatch = True
    elif action == "pause":
        replay.pause_requested = True
        replay.status = "pausing" if replay.status == "running" else "paused"
        dispatch = False
    else:
        replay.cancel_requested = True; replay.pause_requested = False; replay.step_requested = False
        replay.status = "pausing" if replay.status == "running" else "cancelled"
        dispatch = False
    replay.updated_at = _now()
    db.commit(); db.refresh(replay)
    return replay, dispatch


def ingest_push_event(db: Session, replay: TemporalReplay, body: dict[str, Any]) -> tuple[TemporalStreamEvent, bool]:
    if replay.source_mode != "push":
        raise StreamError("文件回放运行不能接收推送事件", "SOURCE_MODE_MISMATCH")
    if replay.status in {"published", "cancelled"}:
        raise StreamError("当前运行已结束，不能接收新事件", "RUN_PUBLISHED" if replay.status == "published" else "RUN_CANCELLED")
    required = ["event_id", "episode_id", "entity_key", "ordinal", "source_sequence"]
    missing = [key for key in required if body.get(key) in (None, "")]
    if missing:
        raise StreamError(f"缺少事件字段：{', '.join(missing)}", "INVALID_EVENT")
    event_id = str(body["event_id"])
    payload = body.get("payload") if isinstance(body.get("payload"), dict) else {}
    payload_hash = _hash(payload)
    existing = db.query(TemporalStreamEvent).filter(TemporalStreamEvent.replay_id == replay.id, TemporalStreamEvent.event_key == event_id).first()
    if existing:
        if existing.payload_hash == payload_hash:
            return existing, True
        raise StreamError("相同 event_id 的载荷不同", "EVENT_PAYLOAD_CONFLICT", event_id=event_id)
    ordinal = _finite_number(body["ordinal"])
    try:
        source_sequence = int(body["source_sequence"])
    except (TypeError, ValueError) as exc:
        raise StreamError("source_sequence 必须是整数", "INVALID_EVENT") from exc
    if source_sequence < 0:
        raise StreamError("source_sequence 不能小于 0", "INVALID_EVENT")
    watermark = _float_or_none(replay.watermark_ordinal)
    if watermark is not None and (ordinal, source_sequence) <= (watermark, int(replay.watermark_sequence or -1)):
        raise StreamError("事件早于或等于当前水位，不能在本版本重算", "LATE_EVENT_NOT_SUPPORTED", watermark_ordinal=watermark, watermark_sequence=replay.watermark_sequence)
    event = TemporalStreamEvent(
        id=str(uuid.uuid4()), replay_id=replay.id, event_key=event_id,
        episode_id=str(body["episode_id"]), entity_key=str(body["entity_key"]),
        ordinal=Decimal(str(ordinal)), source_sequence=source_sequence,
        source_row_id=str((body.get("source_ref") or {}).get("source_row_id") or event_id),
        payload=payload, source_ref=body.get("source_ref") or {}, payload_hash=payload_hash, status="queued",
    )
    db.add(event)
    replay.total_events = int(replay.total_events or 0) + 1
    replay.selected_rows = int(replay.selected_rows or 0) + 1
    replay.total_batches = replay.total_events
    if replay.start_time is None or ordinal < float(replay.start_time):
        replay.start_time = ordinal
    if replay.end_time is None or ordinal > float(replay.end_time):
        replay.end_time = ordinal
    stream_episodes = [str(item) for item in (replay.series_ids or [])]
    if str(body["episode_id"]) not in stream_episodes:
        stream_episodes.append(str(body["episode_id"]))
        replay.series_ids = stream_episodes
    replay.metrics = {**(replay.metrics or {}), "events_total": replay.total_events, "queue": replay.total_events - int(replay.committed_events or 0)}
    replay.status = "queued" if replay.status not in {"running", "pausing"} else replay.status
    replay.error = None
    db.commit(); db.refresh(event); db.refresh(replay)
    dispatch_stream(replay.id)
    return event, False


def _sql_stream_graph(
    db: Session,
    replay: TemporalReplay,
    *,
    limit: int,
    offset: int,
    at: float | None,
    mode: str,
    relation_state: str,
    entity_type: str | None = None,
    episode_id: str | None = None,
) -> dict[str, Any]:
    events = db.query(TemporalStreamEvent).filter(
        TemporalStreamEvent.replay_id == replay.id,
        TemporalStreamEvent.status == "committed",
    ).order_by(
        TemporalStreamEvent.ordinal.asc(),
        TemporalStreamEvent.source_sequence.asc(),
        TemporalStreamEvent.id.asc(),
    ).all()
    if episode_id:
        events = [event for event in events if str(event.episode_id) == str(episode_id)]
    facts_q = db.query(TemporalFact).filter(TemporalFact.replay_id == replay.id)
    if at is not None:
        facts_q = facts_q.filter(TemporalFact.valid_from_ordinal <= Decimal(str(at))).filter((TemporalFact.valid_to_ordinal.is_(None)) | (TemporalFact.valid_to_ordinal > Decimal(str(at))))
        if mode == "window":
            # Structural facts are shown for the event at the cursor; state
            # facts remain visible while their validity interval covers it.
            facts_q = facts_q.filter(
                (TemporalFact.state_key != "")
                | (TemporalFact.valid_from_ordinal == Decimal(str(at)))
            )
    if relation_state == "current":
        # With an explicit historical position, "current" means the fact
        # valid at that position (an expired fact may still be current then).
        # Without a position it means the present active interval.
        if at is None:
            facts_q = facts_q.filter(TemporalFact.status == "active", TemporalFact.valid_to_ordinal.is_(None))
    facts = facts_q.order_by(TemporalFact.valid_from_ordinal.asc()).all()
    # Every stream fact is sourced from one event.  Restricting by the event
    # set keeps the PostgreSQL fallback semantically identical to FalkorDB's
    # episode filter, including state facts generated by a selected episode.
    event_ids = {event.id for event in events}
    facts = [fact for fact in facts if fact.source_event_id in event_ids]
    event_map = {event.id: event for event in events}
    node_map: dict[str, dict[str, Any]] = {}
    for event in events:
        ordinal = float(event.ordinal)
        if at is not None:
            if mode == "window" and not math.isclose(ordinal, float(at), rel_tol=0.0, abs_tol=1e-9):
                continue
            if mode == "cumulative" and ordinal > at:
                continue
        obs_id = f"FactoryNet:Observation:{event.episode_id}:{event.source_row_id or event.source_sequence}"
        node_map.setdefault(obs_id, {"id": obs_id, "entity_type": "Observation", "labels": ["Instance"], "event_seq": event.source_sequence, "event_ordinal": ordinal, "properties": {**(event.payload or {}), "episode_id": event.episode_id, "source_row_id": event.source_row_id}, "node_kind": "instance"})
        ep_id = f"FactoryNet:Episode:{event.episode_id}"
        node_map.setdefault(ep_id, {"id": ep_id, "entity_type": "Episode", "labels": ["Instance"], "properties": {"episode_id": event.episode_id}, "node_kind": "instance"})
    edge_rows: list[dict[str, Any]] = []
    for fact in facts:
        if fact.subject_id not in node_map:
            node_map[fact.subject_id] = {"id": fact.subject_id, "entity_type": fact.subject_id.split(":")[1] if ":" in fact.subject_id else "Entity", "labels": ["Instance"], "properties": {}, "node_kind": "instance"}
        if fact.object_id and fact.object_id not in node_map:
            node_map[fact.object_id] = {"id": fact.object_id, "entity_type": fact.object_id.split(":")[1] if ":" in fact.object_id else "Entity", "labels": ["Instance"], "properties": {"value": (fact.object_value or {}).get("value") if isinstance(fact.object_value, dict) else fact.object_value}, "node_kind": "instance"}
        edge_rows.append({"id": fact.id, "source": fact.subject_id, "target": fact.object_id, "type": fact.predicate, "label": fact.predicate, "edge_kind": "instance", "properties": {"state_key": fact.state_key, "valid_from_ordinal": float(fact.valid_from_ordinal), "valid_to_ordinal": float(fact.valid_to_ordinal) if fact.valid_to_ordinal is not None else None, "source_event_id": fact.source_event_id, "evidence_ref_id": fact.evidence_ref_id}})
    nodes = list(node_map.values())
    if entity_type:
        nodes = [node for node in nodes if str(node.get("entity_type") or "") == str(entity_type)]
    total = len(nodes)
    page = nodes[offset: offset + limit]
    ids = {node["id"] for node in page}
    edges = [edge for edge in edge_rows if edge["source"] in ids and edge["target"] in ids]
    return {"available": True, "graph_backend": "postgres-temporal-facts", "graph_namespace": replay.graph_namespace, "nodes": page, "edges": edges, "total_instances": total, "total_edges": len(edge_rows), "returned": len(page), "offset": offset, "next_offset": offset + len(page) if offset + len(page) < total else None, "sample_limit": limit, "relation_state": relation_state, "at": at, "mode": mode}


def stream_graph(
    db: Session,
    replay: TemporalReplay,
    *,
    limit: int = 200,
    offset: int = 0,
    at: float | None = None,
    mode: str = "cumulative",
    relation_state: str = "all",
    entity_type: str | None = None,
    episode_id: str | None = None,
) -> dict[str, Any]:
    limit = max(1, min(int(limit), MAX_GRAPH_NODES)); offset = max(0, int(offset))
    graph = FalkorDBService()
    if getattr(graph, "available", False):
        try:
            data = graph.get_graph_data(replay.ontology_id, limit=limit, offset=offset, entity_type=entity_type, episode_id=episode_id, seq_to=None, relation_state=relation_state, replay_id=replay.id, at=at, graph_namespace=replay.graph_namespace, mode=mode)
            return {**data, "run_id": replay.id, "ontology_id": replay.ontology_id, "graph_namespace": replay.graph_namespace, "at": at, "mode": mode}
        except TypeError:
            data = graph.get_graph_data(replay.ontology_id, limit=limit, offset=offset, entity_type=entity_type, episode_id=episode_id, relation_state=relation_state, replay_id=replay.id, at=at)
            return {**data, "run_id": replay.id, "ontology_id": replay.ontology_id, "graph_namespace": replay.graph_namespace, "at": at, "mode": mode}
        except Exception:
            pass
    return {
        **_sql_stream_graph(
            db,
            replay,
            limit=limit,
            offset=offset,
            at=at,
            mode=mode,
            relation_state=relation_state,
            entity_type=entity_type,
            episode_id=episode_id,
        ),
        "run_id": replay.id,
        "ontology_id": replay.ontology_id,
    }


def stream_facts(db: Session, replay: TemporalReplay, *, limit: int = 200, offset: int = 0, at: float | None = None, state_key: str | None = None, relation_state: str = "all") -> dict[str, Any]:
    query = db.query(TemporalFact).filter(TemporalFact.replay_id == replay.id)
    if state_key:
        query = query.filter(TemporalFact.state_key == state_key)
    if at is not None:
        point = Decimal(str(at)); query = query.filter(TemporalFact.valid_from_ordinal <= point).filter((TemporalFact.valid_to_ordinal.is_(None)) | (TemporalFact.valid_to_ordinal > point))
    if relation_state == "current" and at is None:
        query = query.filter(TemporalFact.status == "active", TemporalFact.valid_to_ordinal.is_(None))
    total = query.count(); rows = query.order_by(TemporalFact.valid_from_ordinal.asc(), TemporalFact.created_at.asc()).offset(max(0, offset)).limit(max(1, min(limit, 1000))).all()
    return {"run_id": replay.id, "facts": [_serialize_fact(row) for row in rows], "total": total, "offset": offset, "limit": limit, "next_offset": offset + len(rows) if offset + len(rows) < total else None, "at": at, "relation_state": relation_state}


def _validate_publish(db: Session, replay: TemporalReplay) -> list[str]:
    """Run deterministic gates before an immutable snapshot is published."""
    problems: list[str] = []
    event_query = db.query(TemporalStreamEvent).filter(TemporalStreamEvent.replay_id == replay.id)
    total_events = event_query.count()
    if total_events != int(replay.total_events or 0):
        problems.append(f"事件索引数量 {total_events} 与运行总数 {replay.total_events or 0} 不一致")
    pending = event_query.filter(TemporalStreamEvent.status != "committed").count()
    if pending:
        problems.append(f"仍有 {pending} 条事件未提交")

    facts = db.query(TemporalFact).filter(TemporalFact.replay_id == replay.id).all()
    for fact in facts:
        if fact.valid_to_ordinal is not None and fact.valid_to_ordinal < fact.valid_from_ordinal:
            problems.append(f"事实 {fact.id} 的有效区间反向")
        if fact.status == "active" and fact.valid_to_ordinal is not None:
            problems.append(f"当前事实 {fact.id} 仍带有失效水位")
        if fact.status == "expired" and fact.valid_to_ordinal is None:
            problems.append(f"历史事实 {fact.id} 缺少失效水位")
        if not fact.source_event_id or not fact.evidence_ref_id:
            problems.append(f"事实 {fact.id} 缺少来源事件或 EvidenceRef")
    duplicate_states = db.query(
        TemporalFact.subject_id,
        TemporalFact.state_key,
        func.count(TemporalFact.id),
    ).filter(
        TemporalFact.replay_id == replay.id,
        TemporalFact.state_key != "",
        TemporalFact.status == "active",
        TemporalFact.valid_to_ordinal.is_(None),
    ).group_by(
        TemporalFact.subject_id,
        TemporalFact.state_key,
    ).having(func.count(TemporalFact.id) > 1).all()
    if duplicate_states:
        problems.append("当前状态存在重复有效事实")

    observations = [fact for fact in facts if fact.predicate == "HAS_OBSERVATION"]
    event_episodes = {
        str(episode)
        for (episode,) in db.query(TemporalStreamEvent.episode_id).filter(
            TemporalStreamEvent.replay_id == replay.id,
            TemporalStreamEvent.status == "committed",
        ).distinct().all()
    }
    observation_episodes = {
        str(fact.subject_id).split(":", 2)[-1]
        for fact in observations
        if str(fact.subject_id).startswith("FactoryNet:Episode:")
    }
    if event_episodes - observation_episodes:
        problems.append("存在已提交事件没有对应 HAS_OBSERVATION 事实")
    for episode in observation_episodes:
        count = sum(1 for fact in observations if fact.subject_id == f"FactoryNet:Episode:{episode}")
        # NEXT_OBSERVATION is per episode; count only the edges whose source
        # observation belongs to this episode.
        next_count = sum(
            1 for fact in facts
            if fact.predicate == "NEXT_OBSERVATION"
            and str(fact.subject_id).startswith(f"FactoryNet:Observation:{episode}:")
        )
        if count > 0 and next_count != max(0, count - 1):
            problems.append(f"episode {episode} 的 Observation 顺序不连续")

    run_id = (replay.config or {}).get("construction_run_id")
    missing_evidence = db.query(TemporalFact.id).filter(
        TemporalFact.replay_id == replay.id,
        ~TemporalFact.evidence_ref_id.in_(
            db.query(EvidenceRef.id).filter(EvidenceRef.construction_run_id == run_id)
        ) if run_id else TemporalFact.evidence_ref_id.is_(None),
    ).count()
    if missing_evidence:
        problems.append(f"有 {missing_evidence} 条事实无法定位到本次 EvidenceRef")
    return problems


def publish_stream_run(db: Session, replay: TemporalReplay, *, created_by: str | None = None) -> dict[str, Any]:
    if replay.status == "published" and replay.published_snapshot_id:
        snapshot = db.query(DataModelSnapshot).filter(DataModelSnapshot.id == replay.published_snapshot_id).first()
        return {"run": serialize_stream(replay, db=db), "snapshot": _serialize_snapshot(snapshot) if snapshot else None, "already_published": True}
    if replay.status != "completed":
        raise StreamError("只有全部事件处理完成后才能发布快照", "RUN_NOT_COMPLETED")
    committed = db.query(TemporalStreamEvent).filter(TemporalStreamEvent.replay_id == replay.id, TemporalStreamEvent.status == "committed").count()
    if committed != int(replay.total_events or 0):
        raise StreamError("仍有事件未提交，不能发布", "EVENTS_INCOMPLETE", committed=committed, total=replay.total_events)
    project = db.query(OntologyProject).filter(OntologyProject.id == replay.ontology_id).first()
    if not project:
        raise StreamError("本体不存在", "ONTOLOGY_NOT_FOUND")
    if replay.schema_revision_id and project.current_revision_id and str(replay.schema_revision_id) != str(project.current_revision_id):
        raise StreamError("运行期间本体结构已变化，请基于新修订重新验证", "SCHEMA_REVISION_CONFLICT", run_revision_id=replay.schema_revision_id, current_revision_id=project.current_revision_id)
    validation_problems = _validate_publish(db, replay)
    if validation_problems:
        code = "CURRENT_STATE_CONFLICT" if any("当前状态" in item for item in validation_problems) else "STREAM_VALIDATION_FAILED"
        raise StreamError("；".join(validation_problems[:8]), code, problems=validation_problems[:20])
    facts = db.query(TemporalFact).filter(TemporalFact.replay_id == replay.id).order_by(
        TemporalFact.valid_from_ordinal.asc(),
        TemporalFact.subject_id.asc(),
        TemporalFact.predicate.asc(),
        TemporalFact.object_id.asc(),
        TemporalFact.state_key.asc(),
    ).all()
    through = _float_or_none(replay.watermark_ordinal)
    graph = stream_graph(db, replay, limit=MAX_GRAPH_NODES, offset=0, at=None, relation_state="all")
    node_count = int(graph.get("total_instances") or len(graph.get("nodes") or []))
    edge_count = int(graph.get("total_edges") or len(graph.get("edges") or []))
    evidence_count = db.query(EvidenceRef).filter(EvidenceRef.construction_run_id == (replay.config or {}).get("construction_run_id")).count()
    # Snapshot identity describes the materialized content, not the temporary
    # run UUID or graph namespace.  Excluding those run-scoped identifiers is
    # what makes a file replay and an equivalent push sequence produce the
    # same hash while still keeping both projections isolated at runtime.
    ordered_events = db.query(TemporalStreamEvent).filter(
        TemporalStreamEvent.replay_id == replay.id,
    ).order_by(TemporalStreamEvent.source_sequence.asc()).all()
    event_digest = [
        {
            "episode_id": event.episode_id,
            "entity_key": event.entity_key,
            "ordinal": str(event.ordinal),
            "source_sequence": event.source_sequence,
            "payload_hash": event.payload_hash,
        }
        for event in ordered_events
    ]
    fact_digest = [
        {
            "subject_id": fact.subject_id,
            "predicate": fact.predicate,
            "object_id": fact.object_id,
            "object_value": fact.object_value,
            "state_key": fact.state_key,
            "from": str(fact.valid_from_ordinal),
            "to": str(fact.valid_to_ordinal) if fact.valid_to_ordinal is not None else None,
            "status": fact.status,
        }
        for fact in facts
    ]
    digest_payload = {
        "ontology_id": replay.ontology_id,
        "schema_revision_id": replay.schema_revision_id,
        "through": through,
        "events": event_digest,
        "facts": fact_digest,
    }
    snapshot = DataModelSnapshot(id=str(uuid.uuid4()), ontology_id=replay.ontology_id, schema_revision_id=replay.schema_revision_id, replay_id=replay.id, dataset_version_id=replay.dataset_version_id, graph_namespace=replay.graph_namespace or f"stream_{replay.id.replace('-', '')}", through_ordinal=Decimal(str(through)) if through is not None else None, event_count=committed, node_count=node_count, edge_count=edge_count, fact_count=len(facts), evidence_count=evidence_count, snapshot_hash=_hash(digest_payload), status="published", created_by=created_by)
    db.add(snapshot)
    db.flush()
    project.current_data_snapshot_id = snapshot.id
    replay.published_snapshot_id = snapshot.id
    replay.published_at = _now()
    replay.status = "published"
    replay.updated_at = _now()
    db.commit(); db.refresh(snapshot); db.refresh(replay)
    audit_task_id = None
    try:
        from app.services.v2.audit_runner import queue_local_audit
        audit = queue_local_audit(db, ontology_id=replay.ontology_id, revision_id=replay.schema_revision_id or project.current_revision_id, construction_run_id=(replay.config or {}).get("construction_run_id"))
        audit_task_id = getattr(audit, "id", None)
    except Exception:
        # Publishing remains successful when Ollama is not configured.  The
        # audit page can be used to retry once the local model is available.
        audit_task_id = None
    return {"run": serialize_stream(replay, db=db), "snapshot": _serialize_snapshot(snapshot), "audit_task_id": audit_task_id, "already_published": False}


def stream_snapshot(db: Session, replay: TemporalReplay) -> DataModelSnapshot | None:
    if not replay.published_snapshot_id:
        return None
    return db.query(DataModelSnapshot).filter(DataModelSnapshot.id == replay.published_snapshot_id).first()


__all__ = [
    "StreamError", "STREAM_ACTIVE_STATUSES", "STREAM_TERMINAL_STATUSES", "create_stream_run",
    "process_one_event", "run_temporal_stream", "dispatch_stream", "update_stream_control",
    "ingest_push_event", "serialize_stream", "stream_graph", "stream_facts", "publish_stream_run",
    "stream_snapshot",
]
