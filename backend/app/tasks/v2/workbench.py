"""Celery entry points for long-running workbench operations."""
from __future__ import annotations

from app.tasks.celery_app import celery_app


@celery_app.task(name="v2.ibadas_install")
def run_ibadas_install_task(task_id: str) -> None:
    # Import lazily to avoid the router importing Celery while the app is
    # registering routes.
    from app.routers.v2.multimodal import _execute_ibadas_install
    _execute_ibadas_install(task_id)


@celery_app.task(name="v2.multimodal_construction")
def run_multimodal_construction_task(run_id: str, model_id: str | None, sample_limit: int, prompt: str | None, sample_ids: list[str], selected_assets: list[str], privacy_level: str, send_fields: list[str]) -> None:
    from app.routers.v2.multimodal import _execute_multimodal_run
    _execute_multimodal_run(run_id, model_id, sample_limit, prompt, sample_ids, selected_assets, privacy_level, send_fields)


@celery_app.task(name="v2.temporal_profile")
def run_temporal_profile_task(profile_id: str) -> None:
    from app.services.v2.temporal_profile_service import run_profile
    run_profile(profile_id)


@celery_app.task(name="v2.mapping_generation")
def run_mapping_task(task_id: str) -> None:
    from app.routers.v2.construction_drafts import _execute_mapping_task
    _execute_mapping_task(task_id)


@celery_app.task(name="v2.regular_construction")
def run_regular_construction_task(run_id: str, sample_limit: int = 5000) -> None:
    from app.routers.v2.construction_drafts import _execute_regular_run
    _execute_regular_run(run_id, sample_limit)
