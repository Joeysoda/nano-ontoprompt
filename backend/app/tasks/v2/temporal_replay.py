"""Celery entry point for resumable temporal replays."""
from __future__ import annotations

from app.tasks.celery_app import celery_app


@celery_app.task(name="v2.temporal_replay")
def run_temporal_replay_task(replay_id: str) -> dict:
    from app.services.v2.temporal_replay_service import run_temporal_replay
    return run_temporal_replay(replay_id)
