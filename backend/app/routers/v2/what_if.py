"""FactoryNet-only What-If scenarios; results are isolated previews."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.deps import get_current_user, require_editor
from app.models.user import User
from app.models.ontology import OntologyProject
from app.models.v2.dynamic_ontology import WhatIfRun, WhatIfScenario
from app.services.v2.what_if_service import WhatIfCancelled, WhatIfError, build_context, run_preview, serialize_run, serialize_scenario

router = APIRouter(dependencies=[Depends(get_current_user)])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _error(exc: WhatIfError):
    raise HTTPException(status_code=422, detail={"code": "WHAT_IF_INVALID", "message": str(exc)})


@router.get("/{ontology_id}/what-if/context")
def context(ontology_id: str, target_instance_id: str | None = None, episode_id: str | None = None, at: str | None = None, mode: str = Query("cumulative", pattern="^(cumulative|window)$"), db: Session = Depends(get_db)):
    try:
        return build_context(db, ontology_id, target_instance_id=target_instance_id, episode_id=episode_id, at=at, mode=mode)
    except WhatIfError as exc:
        _error(exc)


@router.get("/{ontology_id}/what-if/scenarios")
def list_scenarios(ontology_id: str, db: Session = Depends(get_db)):
    rows = db.query(WhatIfScenario).filter(WhatIfScenario.ontology_id == ontology_id).order_by(WhatIfScenario.updated_at.desc()).limit(100).all()
    return {"scenarios": [serialize_scenario(row) for row in rows]}


@router.post("/{ontology_id}/what-if/scenarios")
def create_scenario(ontology_id: str, body: dict, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    project = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    if not project:
        raise HTTPException(404, "本体不存在")
    baseline = body.get("baseline") or {}
    if not baseline.get("nodes"):
        try:
            baseline = build_context(db, ontology_id, target_instance_id=body.get("target_instance_id"), episode_id=body.get("episode_id"), at=body.get("at"), mode=body.get("mode", "cumulative"))
        except WhatIfError as exc:
            _error(exc)
    if baseline.get("ontology_id") and str(baseline.get("ontology_id")) != str(ontology_id):
        raise HTTPException(422, "推演基线不属于当前本体")
    if len(baseline.get("nodes") or []) > 500:
        raise HTTPException(422, "推演基线超过 500 个节点限制，请缩小范围")
    if len(baseline.get("edges") or []) > 2000:
        raise HTTPException(422, "推演基线超过 2,000 条事实限制，请缩小范围")
    scenario = WhatIfScenario(id=str(uuid.uuid4()), ontology_id=ontology_id, base_revision_id=baseline.get("base_revision_id") or project.current_revision_id, dataset_version_id=baseline.get("dataset_version_id"), name=str(body.get("name") or "FactoryNet 情景"), status="draft", baseline_json=baseline, assumptions_json=body.get("assumptions") or [], rule_overrides_json=body.get("rule_overrides") or {}, created_by=user.id)
    db.add(scenario); db.commit(); db.refresh(scenario)
    return serialize_scenario(scenario)


@router.patch("/{ontology_id}/what-if/scenarios/{scenario_id}")
def patch_scenario(ontology_id: str, scenario_id: str, body: dict, db: Session = Depends(get_db), _=Depends(require_editor)):
    scenario = db.query(WhatIfScenario).filter(WhatIfScenario.id == scenario_id, WhatIfScenario.ontology_id == ontology_id).first()
    if not scenario:
        raise HTTPException(404, "情景不存在")
    if "name" in body: scenario.name = str(body["name"])
    if "assumptions" in body: scenario.assumptions_json = body["assumptions"] or []
    if "rule_overrides" in body: scenario.rule_overrides_json = body["rule_overrides"] or {}
    db.commit(); db.refresh(scenario)
    return serialize_scenario(scenario)


def _execute_run(run_id: str) -> None:
    db = SessionLocal()
    try:
        run = db.query(WhatIfRun).filter(WhatIfRun.id == run_id).first()
        if not run:
            return
        scenario = db.query(WhatIfScenario).filter(WhatIfScenario.id == run.scenario_id).first()
        if not scenario:
            run.status = "failed"; run.error = "情景不存在"; db.commit(); return
        run.status = "running"; run.stage = "prepare_baseline"; run.progress = 10; run.started_at = datetime.now(timezone.utc); db.commit()
        if run.cancel_requested:
            run.status = "cancelled"; run.stage = "cancelled"; db.commit(); return
        run.stage = "baseline_reasoning"; run.progress = 35; db.commit()

        def on_stage(stage: str, progress: int) -> None:
            db.refresh(run)
            if run.cancel_requested:
                run.status = "cancelled"
                run.stage = "cancelled"
                run.error = "用户取消了推演任务"
                db.commit()
                raise WhatIfCancelled("推演已取消")
            run.stage = stage
            run.progress = progress
            db.commit()

        payload = run_preview(db, scenario, run, on_stage=on_stage)
        run.stage = "completed"; run.progress = 100; run.context_json = scenario.baseline_json or {}; run.baseline_result_json = payload["baseline"]; run.scenario_result_json = payload["scenario"]; run.diff_json = payload["diff"]; run.engine = payload["engine"]; run.engine_version = payload["engine_version"]; run.status = "completed"; run.completed_at = datetime.now(timezone.utc); db.commit()
    except WhatIfCancelled:
        db.rollback()
        run = db.query(WhatIfRun).filter(WhatIfRun.id == run_id).first()
        if run:
            run.status = "cancelled"; run.stage = "cancelled"; run.error = "用户取消了推演任务"; db.commit()
    except WhatIfError as exc:
        db.rollback(); run = db.query(WhatIfRun).filter(WhatIfRun.id == run_id).first()
        if run:
            run.status = "failed"; run.stage = "failed"; run.error = str(exc); db.commit()
    except Exception as exc:
        db.rollback(); run = db.query(WhatIfRun).filter(WhatIfRun.id == run_id).first()
        if run:
            run.status = "failed"; run.stage = "failed"; run.error = str(exc)[:2000]; db.commit()
    finally:
        db.close()


@router.post("/{ontology_id}/what-if/scenarios/{scenario_id}/runs", status_code=202)
def start_run(ontology_id: str, scenario_id: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db), user: User = Depends(require_editor)):
    scenario = db.query(WhatIfScenario).filter(WhatIfScenario.id == scenario_id, WhatIfScenario.ontology_id == ontology_id).first()
    if not scenario:
        raise HTTPException(404, "情景不存在")
    payload = {"scenario_id": scenario.id, "baseline": scenario.baseline_json or {}, "assumptions": scenario.assumptions_json or [], "rules": scenario.rule_overrides_json or {}}
    run = WhatIfRun(id=str(uuid.uuid4()), scenario_id=scenario.id, ontology_id=ontology_id, status="queued", stage="prepare_baseline", progress=0, context_json=scenario.baseline_json or {}, input_hash=hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest(), created_by=user.id)
    scenario.status = "queued"; db.add(run); db.commit(); db.refresh(run)
    if os.getenv("CELERY_ENABLED", "").lower() in {"1", "true", "yes"}:
        try:
            from app.tasks.v2.workbench import run_what_if_task
            run_what_if_task.delay(run.id)
        except Exception:
            background_tasks.add_task(_execute_run, run.id)
    else:
        background_tasks.add_task(_execute_run, run.id)
    return serialize_run(run)


@router.get("/{ontology_id}/what-if/runs/{run_id}")
def get_run(ontology_id: str, run_id: str, db: Session = Depends(get_db)):
    run = db.query(WhatIfRun).filter(WhatIfRun.id == run_id, WhatIfRun.ontology_id == ontology_id).first()
    if not run:
        raise HTTPException(404, "推演运行不存在")
    return serialize_run(run)


@router.post("/{ontology_id}/what-if/runs/{run_id}/cancel")
def cancel_run(ontology_id: str, run_id: str, db: Session = Depends(get_db), _=Depends(require_editor)):
    """Request cooperative cancellation; the worker owns the final state."""
    run = db.query(WhatIfRun).filter(
        WhatIfRun.id == run_id, WhatIfRun.ontology_id == ontology_id
    ).first()
    if not run:
        raise HTTPException(404, "推演运行不存在")
    if run.status in {"queued", "running"}:
        run.cancel_requested = True
        db.commit()
    return serialize_run(run)
