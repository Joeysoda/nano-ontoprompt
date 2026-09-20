"""HTTP API for FactoryNet's event-at-a-time dynamic evolution demo."""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.deps import get_current_user, require_editor
from app.models.user import User
from app.models.v2.temporal_replay import TemporalFact, TemporalReplay, TemporalStreamEvent
from app.services.v2.temporal_stream_service import (
    StreamError,
    create_stream_run,
    dispatch_stream,
    ingest_push_event,
    publish_stream_run,
    serialize_stream,
    stream_facts,
    stream_graph,
    update_stream_control,
)


router = APIRouter(dependencies=[Depends(get_current_user)])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class TemporalStreamCreate(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    dataset_id: str | None = None
    dataset_version_id: str | None = None
    source_id: str = "factorynet_cnc"
    source_mode: str = Field(default="file_replay", pattern="^(file_replay|push)$")
    episode_ids: list[str] = Field(default_factory=list, max_length=200)
    # The singular form is accepted by the browser's compact form and older
    # integrations; it is folded into episode_ids below.
    episode_id: str | None = None
    series_ids: list[str] = Field(default_factory=list, max_length=200)
    start_ordinal: float | None = None
    end_ordinal: float | None = None
    start_time: float | None = None
    end_time: float | None = None
    speed: float = Field(default=1.0, gt=0, le=20)
    event_interval_ms: int | None = Field(default=None, ge=1, le=60000)
    time_column: str = Field(default="time_s", min_length=1, max_length=200)
    entity_column: str = Field(default="episode_id", min_length=1, max_length=200)
    config: dict[str, Any] = Field(default_factory=dict)


class TemporalStreamControl(BaseModel):
    action: str = Field(pattern="^(start|pause|resume|step|cancel)$")
    speed: float | None = Field(default=None, gt=0, le=20)


class TemporalStreamEventBody(BaseModel):
    event_id: str = Field(min_length=1, max_length=300)
    episode_id: str = Field(min_length=1, max_length=200)
    entity_key: str = Field(min_length=1, max_length=200)
    ordinal: float
    source_sequence: int
    payload: dict[str, Any] = Field(default_factory=dict)
    source_ref: dict[str, Any] = Field(default_factory=dict)


def _get_replay(db: Session, run_id: str) -> TemporalReplay:
    replay = db.query(TemporalReplay).filter(TemporalReplay.id == run_id).first()
    if not replay:
        raise HTTPException(404, "动态运行不存在")
    return replay


def _raise_stream(exc: StreamError) -> None:
    status_code = 409 if exc.code in {
        "SCHEMA_REVISION_CONFLICT", "LATE_EVENT_NOT_SUPPORTED", "EVENT_PAYLOAD_CONFLICT",
        "CURRENT_STATE_CONFLICT", "RUN_NOT_COMPLETED", "EVENTS_INCOMPLETE", "RUN_PUBLISHED",
        "RUN_CANCELLED", "SOURCE_MODE_MISMATCH", "DATA_CLASS_MISMATCH", "EVENT_RUN_MISMATCH",
    } else 424 if exc.code in {"STORAGE_OBJECT_MISSING", "SOURCE_NOT_READY"} else 422
    detail = {"code": exc.code, "message": str(exc), **exc.extra}
    raise HTTPException(status_code, detail=detail) from exc


@router.get("/ontologies/{ontology_id}/temporal-streams")
def list_streams(ontology_id: str, limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    rows = db.query(TemporalReplay).filter(TemporalReplay.ontology_id == ontology_id).order_by(TemporalReplay.created_at.desc()).limit(limit).all()
    return {"runs": [serialize_stream(item, db=db) for item in rows], "count": len(rows)}


@router.post("/ontologies/{ontology_id}/temporal-streams", status_code=status.HTTP_201_CREATED)
def create_stream(ontology_id: str, body: TemporalStreamCreate, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    if body.source_id != "factorynet_cnc":
        raise HTTPException(422, detail={"code": "UNSUPPORTED_SOURCE", "message": "动态演化第一版仅支持 FactoryNet CNC"})
    episodes = list(body.episode_ids or body.series_ids or [])
    if body.episode_id:
        episodes = [body.episode_id] if not episodes else [*episodes, body.episode_id]
    config = dict(body.config or {})
    if body.event_interval_ms:
        config["event_interval_ms"] = body.event_interval_ms
        # ``speed`` remains the visible events/second value; the service uses
        # the explicit interval when supplied for a deterministic demo.
        config["explicit_event_interval_ms"] = body.event_interval_ms
    if body.start_ordinal is None and body.start_time is not None:
        start_ordinal = body.start_time
    else:
        start_ordinal = body.start_ordinal
    if body.end_ordinal is None and body.end_time is not None:
        end_ordinal = body.end_time
    else:
        end_ordinal = body.end_ordinal
    try:
        replay = create_stream_run(
            db, ontology_id,
            dataset_id=body.dataset_id,
            dataset_version_id=body.dataset_version_id,
            episode_ids=episodes,
            start_ordinal=start_ordinal,
            end_ordinal=end_ordinal,
            speed=body.speed,
            time_column=body.time_column,
            entity_column=body.entity_column,
            source_mode=body.source_mode,
            config=config,
            created_by=user.id,
        )
    except StreamError as exc:
        _raise_stream(exc)
    return serialize_stream(replay, db=db)


@router.get("/temporal-streams/{run_id}")
def get_stream(run_id: str, db: Session = Depends(get_db)):
    return serialize_stream(_get_replay(db, run_id), db=db)


@router.post("/temporal-streams/{run_id}/control")
def control_stream(run_id: str, body: TemporalStreamControl, db: Session = Depends(get_db), _user: User = Depends(require_editor)):
    replay = _get_replay(db, run_id)
    try:
        replay, should_dispatch = update_stream_control(db, replay, body.action, body.speed)
    except StreamError as exc:
        _raise_stream(exc)
    if should_dispatch:
        dispatch_stream(replay.id)
    return serialize_stream(replay, db=db)


@router.get("/temporal-streams/{run_id}/events")
def list_events(run_id: str, offset: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000), status_filter: str | None = Query(None, alias="status"), db: Session = Depends(get_db)):
    replay = _get_replay(db, run_id)
    query = db.query(TemporalStreamEvent).filter(TemporalStreamEvent.replay_id == replay.id)
    if status_filter:
        query = query.filter(TemporalStreamEvent.status == status_filter)
    total = query.count()
    rows = query.order_by(TemporalStreamEvent.ordinal.asc(), TemporalStreamEvent.source_sequence.asc()).offset(offset).limit(limit).all()
    from app.services.v2.temporal_stream_service import _serialize_event
    return {"run_id": run_id, "events": [_serialize_event(row) for row in rows], "total": total, "offset": offset, "limit": limit, "next_offset": offset + len(rows) if offset + len(rows) < total else None}


@router.post("/temporal-streams/{run_id}/events", status_code=status.HTTP_202_ACCEPTED)
def push_event(run_id: str, body: TemporalStreamEventBody, db: Session = Depends(get_db), _user: User = Depends(require_editor)):
    replay = _get_replay(db, run_id)
    try:
        event, idempotent = ingest_push_event(db, replay, body.model_dump())
    except StreamError as exc:
        _raise_stream(exc)
    from app.services.v2.temporal_stream_service import _serialize_event
    return {"event": _serialize_event(event), "idempotent": idempotent, "run": serialize_stream(replay, db=db)}


@router.get("/temporal-streams/{run_id}/facts")
def get_facts(run_id: str, offset: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000), at: float | None = None, state_key: str | None = None, relation_state: str = Query("all", pattern="^(all|current)$"), db: Session = Depends(get_db)):
    replay = _get_replay(db, run_id)
    return stream_facts(db, replay, offset=offset, limit=limit, at=at, state_key=state_key, relation_state=relation_state)


@router.get("/temporal-streams/{run_id}/graph")
def get_stream_graph(
    run_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=500),
    entity_type: str | None = None,
    episode_id: str | None = None,
    at: float | None = None,
    mode: str = Query("cumulative", pattern="^(cumulative|window)$"),
    relation_state: str = Query("all", pattern="^(all|current)$"),
    db: Session = Depends(get_db),
):
    replay = _get_replay(db, run_id)
    return stream_graph(
        db,
        replay,
        offset=offset,
        limit=limit,
        entity_type=entity_type,
        episode_id=episode_id,
        at=at,
        mode=mode,
        relation_state=relation_state,
    )


async def _event_stream(run_id: str, request: Request) -> AsyncIterator[str]:
    """Polling-to-SSE bridge with event IDs suitable for reconnects.

    The database remains authoritative, so a browser reconnect can send its
    last source sequence and receive only the committed events after that
    point.  This avoids duplicate visual commits while still working when a
    Redis/Celery broker is unavailable in a local demonstration.
    """
    try:
        last_sequence = int(request.headers.get("last-event-id") or -1)
    except (TypeError, ValueError):
        last_sequence = -1
    last_revision = ""
    for _ in range(3600):  # one hour maximum per browser connection
        if await request.is_disconnected():
            break
        db = SessionLocal()
        try:
            replay = db.query(TemporalReplay).filter(TemporalReplay.id == run_id).first()
            if not replay:
                yield f"event: error\ndata: {json.dumps({'code': 'RUN_NOT_FOUND', 'message': '动态运行不存在'}, ensure_ascii=False)}\n\n"
                break
            payload = serialize_stream(replay, db=db)
            committed_events = db.query(TemporalStreamEvent).filter(
                TemporalStreamEvent.replay_id == run_id,
                TemporalStreamEvent.status == "committed",
                TemporalStreamEvent.source_sequence > last_sequence,
            ).order_by(TemporalStreamEvent.source_sequence.asc()).all()
            from app.services.v2.temporal_stream_service import _serialize_event, _serialize_fact
            for committed_event in committed_events:
                expired_facts = db.query(TemporalFact).filter(
                    TemporalFact.replay_id == run_id,
                    TemporalFact.valid_to_ordinal == committed_event.ordinal,
                ).order_by(TemporalFact.id.asc()).all()
                packet = {
                    "event": _serialize_event(committed_event),
                    "facts_expired": [_serialize_fact(item) for item in expired_facts],
                    "run": payload,
                }
                last_sequence = int(committed_event.source_sequence)
                yield f"id: {last_sequence}\nevent: event_committed\ndata: {json.dumps(packet, ensure_ascii=False, default=str)}\n\n"
            revision = f"{payload.get('updated_at')}:{payload.get('committed_events')}:{payload.get('status')}"
            if revision != last_revision:
                last_revision = revision
                event_name = "completed" if replay.status in {"completed", "published"} else "update"
                yield f"id: {max(last_sequence, int(payload.get('committed_events', 0)) - 1)}\nevent: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
            if replay.status in {"completed", "failed", "cancelled", "published"}:
                break
        finally:
            db.close()
        await asyncio.sleep(0.8)


@router.get("/temporal-streams/{run_id}/event-stream")
async def event_stream(run_id: str, request: Request):
    return StreamingResponse(_event_stream(run_id, request), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/temporal-streams/{run_id}/publish")
def publish(run_id: str, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    replay = _get_replay(db, run_id)
    try:
        return publish_stream_run(db, replay, created_by=user.id)
    except StreamError as exc:
        _raise_stream(exc)


__all__ = ["router"]
