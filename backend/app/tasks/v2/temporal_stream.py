"""Celery entry point for the event-at-a-time temporal stream."""
from __future__ import annotations

from app.tasks.celery_app import celery_app


@celery_app.task(name="v2.temporal_stream")
def run_temporal_stream_task(run_id: str) -> dict:
    from app.services.v2.temporal_stream_service import run_temporal_stream

    return run_temporal_stream(run_id)
