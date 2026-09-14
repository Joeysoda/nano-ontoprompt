"""API for the FactoryNet temporal data-arrival simulation."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.deps import get_current_user, require_editor
from app.models.ontology import OntologyProject
from app.models.user import User
from app.models.v2.dataset import Dataset, DatasetVersion
from app.models.v2.temporal_replay import TemporalReplay, TemporalReplayBatch
from app.services.v2.datasets.factorynet_installer import FACTORYNET_SOURCE_ID, find_factorynet_dataset
from app.services.v2.temporal_replay_service import (
    ACTIVE_STATUSES,
    FACTORYNET_SOURCE_ID as REPLAY_FACTORYNET_SOURCE_ID,
    build_factorynet_replay_batches,
    dispatch_replay,
    serialize_replay,
    update_replay_control,
)


router = APIRouter(prefix="/temporal/replays", dependencies=[Depends(get_current_user)])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class TemporalReplayCreate(BaseModel):
    source_id: str = FACTORYNET_SOURCE_ID
    dataset_id: str | None = None
    dataset_version_id: str | None = None
    ontology_id: str | None = None
    ontology_name: str = "FactoryNet CNC 时序模拟"
    series_ids: list[str] = Field(default_factory=list, max_length=200)
    start_time: float | None = None
    end_time: float | None = None
    window_seconds: float = Field(default=1.0, gt=0, le=3600)
    speed: float = Field(default=5.0, gt=0, le=100)
    max_rows_per_batch: int = Field(default=200, ge=1, le=5000)
    max_records: int = Field(default=5000, ge=1, le=1000000)
    entity_column: str = Field(default="episode_id", min_length=1, max_length=200)
    time_column: str = Field(default="time_s", min_length=1, max_length=200)
    config: dict[str, Any] = Field(default_factory=dict)


class TemporalReplayControl(BaseModel):
    action: str = Field(pattern="^(start|pause|resume|step|cancel)$")
    speed: float | None = Field(default=None, gt=0, le=100)


class TemporalReplaySegment(BaseModel):
    start_time: float
    end_time: float
    window_seconds: float | None = Field(default=None, gt=0, le=3600)
    max_rows_per_batch: int | None = Field(default=None, ge=1, le=5000)


def _factorynet_version(db: Session, dataset_id: str | None, version_id: str | None) -> tuple[Dataset, DatasetVersion]:
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first() if dataset_id else find_factorynet_dataset(db)
    if not dataset:
        raise HTTPException(status_code=409, detail={"error": "SOURCE_NOT_INSTALLED", "message": "请先安装 FactoryNet CNC 官方样例"})
    manifest = dict(dataset.schema_json or {})
    # ``dataset_id`` is accepted for the UI's explicit version selection, but
    # it must not turn this FactoryNet replay endpoint into a generic parser.
    # Older records may only have the canonical name, so keep that narrow
    # compatibility check while rejecting C-MAPSS or arbitrary temporal data.
    if manifest.get("source_id") != FACTORYNET_SOURCE_ID and dataset.name != "FactoryNet CNC 铣削时序数据":
        raise HTTPException(status_code=409, detail={"error": "WRONG_SOURCE", "message": "该数据版本不是 FactoryNet CNC 官方样例"})
    version = db.query(DatasetVersion).filter(DatasetVersion.id == version_id).first() if version_id else None
    if version is None and dataset.latest_version_id:
        version = db.query(DatasetVersion).filter(DatasetVersion.id == dataset.latest_version_id).first()
    if version is None:
        version = db.query(DatasetVersion).filter(DatasetVersion.dataset_id == dataset.id).order_by(DatasetVersion.version_no.desc()).first()
    if not version or not version.storage_uri:
        raise HTTPException(status_code=409, detail={"error": "SOURCE_NOT_READY", "message": "FactoryNet 数据版本没有可读取对象"})
    return dataset, version


def _read_factorynet(db: Session, version: DatasetVersion) -> list[dict[str, Any]]:
    from app.routers.v2.temporal import parse_temporal_bytes
    from app.services.storage_service import get_storage_service
    try:
        return parse_temporal_bytes(get_storage_service().get_object(version.storage_uri))
    except Exception as exc:
        raise HTTPException(status_code=424, detail={"error": "STORAGE_OBJECT_MISSING", "message": f"FactoryNet 源文件无法读取: {exc}"}) from exc


def _latest_batch(db: Session, replay_id: str) -> TemporalReplayBatch | None:
    return db.query(TemporalReplayBatch).filter(TemporalReplayBatch.replay_id == replay_id).order_by(TemporalReplayBatch.batch_no.desc()).first()


@router.get("")
def list_replays(limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    rows = db.query(TemporalReplay).order_by(TemporalReplay.created_at.desc()).limit(limit).all()
    return [serialize_replay(item, latest_batch=_latest_batch(db, item.id)) for item in rows]


@router.post("", status_code=201)
def create_replay(body: TemporalReplayCreate, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    if body.source_id != REPLAY_FACTORYNET_SOURCE_ID:
        raise HTTPException(422, "时序模拟第一版仅支持 FactoryNet CNC")
    if not body.ontology_name.strip() and not body.ontology_id:
        raise HTTPException(422, "新建本体名称不能为空")
    dataset, version = _factorynet_version(db, body.dataset_id, body.dataset_version_id)
    rows = _read_factorynet(db, version)
    if not rows:
        raise HTTPException(422, "FactoryNet 数据为空，无法创建时序模拟")
    from app.services.v2.temporal_replay_service import _episode, _source_time
    episodes = sorted({_episode(row) for row in rows})
    series_ids = [str(value) for value in body.series_ids if str(value).strip()]
    # The product default is one episode so the first demo stays readable;
    # users can explicitly select several episodes for synchronized replay.
    if not series_ids:
        series_ids = episodes[:1]
    unknown = sorted(set(series_ids) - set(episodes))
    if unknown:
        raise HTTPException(422, detail={"error": "UNKNOWN_SERIES", "message": f"不存在的 episode_id: {unknown[0]}"})
    selected_times: list[float] = []
    selected_episode_ids = set(series_ids)
    for index, row in enumerate(rows):
        if _episode(row) not in selected_episode_ids:
            continue
        try:
            selected_times.append(_source_time(row, index, body.time_column))
        except ValueError as exc:
            raise HTTPException(422, detail={"error": "INVALID_TIME_VALUE", "message": str(exc), "row_index": index}) from exc
    if not selected_times:
        raise HTTPException(422, "所选生产过程没有可用时间值")
    start = body.start_time if body.start_time is not None else min(selected_times)
    end = body.end_time if body.end_time is not None else max(selected_times)
    if end < start:
        raise HTTPException(422, "结束时间不能早于开始时间")
    batches, summary = build_factorynet_replay_batches(
        rows, series_ids=series_ids, start_time=start, end_time=end,
        window_seconds=body.window_seconds, max_rows_per_batch=body.max_rows_per_batch,
        max_records=body.max_records, time_column=body.time_column,
    )
    if not batches:
        raise HTTPException(422, "所选时间范围没有可用记录")
    if body.ontology_id:
        ontology = db.query(OntologyProject).filter(OntologyProject.id == body.ontology_id).first()
        if not ontology:
            raise HTTPException(404, "目标本体不存在")
        if ontology.data_class != "temporal":
            raise HTTPException(409, "时序模拟只能写入时序本体")
    else:
        ontology = OntologyProject(
            id=str(uuid.uuid4()), name=(body.ontology_name or "FactoryNet CNC 时序模拟").strip(),
            domain="制造", description="FactoryNet 按时间逐批到达的时序本体演示",
            build_mode="temporal_replay", data_class="temporal", created_by=user.id,
        )
        db.add(ontology)
        db.flush()
    # Publish the fixed FactoryNet vocabulary before any replay batch starts;
    # the simulation grows instances and assertions, not the ontology schema.
    from app.tasks.v2.temporal_construction import _ensure_factorynet_schema
    _ensure_factorynet_schema(db, ontology.id)
    manifest = dict(dataset.schema_json or {})
    replay_config = {
        **(body.config or {}),
        "source_id": body.source_id,
        "source_file": manifest.get("filename") or "FactoryNet CNC",
        "source_checksum": version.checksum or manifest.get("sha256"),
        "dataset_name": dataset.name,
        "max_rows_per_batch": body.max_rows_per_batch,
        "max_records": body.max_records,
        "segments": [{"start_time": start, "end_time": end}],
    }
    # Persist the ordinal offsets for the *scheduled* rows, not only rows
    # already committed by the worker.  This keeps an appended segment
    # collision-free even when the user adds it before pressing Start.
    scheduled_offsets: dict[str, int] = {}
    for item in batches:
        for row in item.get("rows", []):
            episode = str(row.get("episode_id") or row.get("_series_id") or "unknown_episode")
            try:
                sequence = int(row.get("_replay_event_seq"))
            except (TypeError, ValueError):
                sequence = scheduled_offsets.get(episode, 0)
            scheduled_offsets[episode] = max(scheduled_offsets.get(episode, 0), sequence + 1)
    replay_config["sequence_offsets"] = scheduled_offsets
    replay = TemporalReplay(
        id=str(uuid.uuid4()), ontology_id=ontology.id, dataset_id=dataset.id, dataset_version_id=version.id,
        source_id=body.source_id, status="created", time_kind="ordinal", entity_column=body.entity_column,
        time_column=body.time_column, series_ids=series_ids, start_time=float(start), end_time=float(end),
        window_seconds=float(body.window_seconds), speed=float(body.speed), current_time=None,
        current_batch_index=-1, total_batches=len(batches), source_rows=len(rows), selected_rows=summary["selected_rows"],
        normalized_rows=0, metrics={"source_rows": len(rows), "selected_rows": summary["selected_rows"], "normalized_rows": 0, "nodes_written": 0, "edges_written": 0, "committed_batches": 0},
        config=replay_config, state={}, error=None, pause_requested=False, step_requested=False, cancel_requested=False,
    )
    db.add(replay)
    for item in batches:
        db.add(TemporalReplayBatch(
            id=str(uuid.uuid4()), replay_id=replay.id, batch_no=item["batch_no"], time_from=item["time_from"],
            time_to=item["time_to"], status="queued", source_row_ids=item["source_row_ids"],
            source_rows=item["source_rows"], normalized_rows=0, nodes_written=0, edges_written=0,
            issue_count=0, latest_rows=item["latest_rows"], payload_hash=item["payload_hash"],
        ))
    db.commit(); db.refresh(replay)
    return serialize_replay(replay, latest_batch=_latest_batch(db, replay.id))


@router.get("/{replay_id}")
def get_replay(replay_id: str, db: Session = Depends(get_db)):
    replay = db.query(TemporalReplay).filter(TemporalReplay.id == replay_id).first()
    if not replay:
        raise HTTPException(404, "时序模拟不存在")
    return serialize_replay(replay, latest_batch=_latest_batch(db, replay.id))


@router.get("/{replay_id}/batches")
def get_replay_batches(replay_id: str, db: Session = Depends(get_db)):
    replay = db.query(TemporalReplay.id).filter(TemporalReplay.id == replay_id).first()
    if not replay:
        raise HTTPException(404, "时序模拟不存在")
    rows = db.query(TemporalReplayBatch).filter(TemporalReplayBatch.replay_id == replay_id).order_by(TemporalReplayBatch.batch_no).all()
    return {"replay_id": replay_id, "batches": [
        {"id": item.id, "batch_no": item.batch_no, "time_from": item.time_from, "time_to": item.time_to, "status": item.status, "source_rows": item.source_rows, "normalized_rows": item.normalized_rows, "nodes_written": item.nodes_written, "edges_written": item.edges_written, "issue_count": item.issue_count, "latest_rows": item.latest_rows or [], "error": item.error}
        for item in rows
    ]}


@router.post("/{replay_id}/control")
def control_replay(replay_id: str, body: TemporalReplayControl, db: Session = Depends(get_db), _user: User = Depends(require_editor)):
    replay = db.query(TemporalReplay).filter(TemporalReplay.id == replay_id).first()
    if not replay:
        raise HTTPException(404, "时序模拟不存在")
    try:
        replay, dispatch = update_replay_control(db, replay, body.action, body.speed)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if dispatch:
        dispatch_replay(replay.id)
    return serialize_replay(replay, latest_batch=_latest_batch(db, replay.id))


@router.post("/{replay_id}/segments", status_code=201)
def append_replay_segment(replay_id: str, body: TemporalReplaySegment, db: Session = Depends(get_db), _user: User = Depends(require_editor)):
    replay = db.query(TemporalReplay).filter(TemporalReplay.id == replay_id).first()
    if not replay:
        raise HTTPException(404, "时序模拟不存在")
    if replay.status in {"failed", "cancelled"}:
        raise HTTPException(409, "失败或取消的模拟不能追加区间")
    if body.end_time < body.start_time:
        raise HTTPException(422, "结束时间不能早于开始时间")
    if replay.end_time is not None and body.start_time <= replay.end_time:
        raise HTTPException(409, "追加区间必须严格位于当前模拟之后")
    version = db.query(DatasetVersion).filter(DatasetVersion.id == replay.dataset_version_id).first()
    if not version:
        raise HTTPException(409, "数据版本不存在")
    rows = _read_factorynet(db, version)
    from app.services.v2.temporal_replay_service import FactoryNetIncrementalState
    state = FactoryNetIncrementalState.from_dict(replay.state or {})
    configured_offsets = {
        str(key): int(value)
        for key, value in ((replay.config or {}).get("sequence_offsets") or {}).items()
    }
    # A replay can be extended while its original batches are still queued.
    # Use the larger of the persisted schedule and the committed checkpoint.
    offsets = {
        key: max(configured_offsets.get(key, 0), state.sequence_by_episode.get(key, 0))
        for key in set(configured_offsets) | set(state.sequence_by_episode)
    }
    window = body.window_seconds or replay.window_seconds
    max_rows = body.max_rows_per_batch or int((replay.config or {}).get("max_rows_per_batch") or 200)
    batches, summary = build_factorynet_replay_batches(
        rows, series_ids=replay.series_ids or [], start_time=body.start_time, end_time=body.end_time,
        window_seconds=window, max_rows_per_batch=max_rows,
        sequence_offsets=offsets, batch_offset=replay.total_batches,
        time_column=replay.time_column or "time_s",
    )
    if not batches:
        raise HTTPException(422, "追加区间没有可用记录")
    for item in batches:
        db.add(TemporalReplayBatch(
            id=str(uuid.uuid4()), replay_id=replay.id, batch_no=item["batch_no"], time_from=item["time_from"], time_to=item["time_to"], status="queued", source_row_ids=item["source_row_ids"], source_rows=item["source_rows"], latest_rows=item["latest_rows"], payload_hash=item["payload_hash"],
        ))
    replay.total_batches += len(batches)
    replay.selected_rows += summary["selected_rows"]
    replay.end_time = float(body.end_time)
    replay.window_seconds = float(window)
    appended_offsets = dict(offsets)
    for item in batches:
        for row in item.get("rows", []):
            episode = str(row.get("episode_id") or row.get("_series_id") or "unknown_episode")
            try:
                sequence = int(row.get("_replay_event_seq"))
            except (TypeError, ValueError):
                sequence = appended_offsets.get(episode, 0)
            appended_offsets[episode] = max(appended_offsets.get(episode, 0), sequence + 1)
    replay.config = {
        **(replay.config or {}),
        "sequence_offsets": appended_offsets,
        "segments": [*((replay.config or {}).get("segments") or []), {"start_time": body.start_time, "end_time": body.end_time}],
    }
    replay.status = "paused" if replay.current_batch_index >= 0 else "created"
    replay.pause_requested = True
    replay.step_requested = False
    replay.updated_at = datetime.now(timezone.utc)
    db.commit(); db.refresh(replay)
    return serialize_replay(replay, latest_batch=_latest_batch(db, replay.id))


@router.get("/{replay_id}/graph")
def replay_graph(
    replay_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=500),
    entity_type: str | None = None,
    episode_id: str | None = None,
    seq_from: int | None = None,
    seq_to: int | None = None,
    at: float | None = Query(None),
    relation_state: str = Query("all", pattern="^(all|current)$"),
    db: Session = Depends(get_db),
):
    replay = db.query(TemporalReplay).filter(TemporalReplay.id == replay_id).first()
    if not replay:
        raise HTTPException(404, "时序模拟不存在")
    from app.services.v2.graph.falkordb_service import FalkorDBService
    graph = FalkorDBService().get_graph_data(
        replay.ontology_id, offset=offset, limit=limit, entity_type=entity_type,
        episode_id=episode_id, seq_from=seq_from, seq_to=seq_to,
        relation_state=relation_state, replay_id=replay.id, at=at,
    )
    return {**graph, "replay_id": replay.id, "ontology_id": replay.ontology_id, "current_time": replay.current_time, "status": replay.status}
