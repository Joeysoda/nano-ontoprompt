"""Canonical APIs for authoring and compiling versioned scenario studies."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from app.deps import get_db, require_editor
from app.models.v2.scenario_study import CaseDefinition, RunPlan, StudyDefinition, StudyVersion
from app.models.v2.scenario import ScenarioAudit, ScenarioRun, ScenarioRunArtifact, ScenarioRunStage
from app.schemas.v2.scenario_study import (
    CreateCaseRequest,
    CreateStudyRequest,
    PublishStudyRequest,
    InspectStudyPackageRequest,
    InstallStudyPackageRequest,
    StartStudyRunRequest,
    RetryStudyRunRequest,
    UpdateCaseRequest,
    UpdateStudyRequest,
)
from app.services.v2.scenario_studies import (
    StudyError,
    StudyService,
    case_summary,
    error_detail,
    plan_summary,
    study_summary,
    version_summary,
)
from app.tasks.v2.scenario import run_scenario
from app.services.v2.scenario_study_templates import templates
from app.services.v2.study_packages import export_package, inspect_package, install_package


router = APIRouter()


def _raise(exc: StudyError):
    raise HTTPException(status_code=exc.status, detail=error_detail(exc)) from exc


def service(ontology_id: str, db: Session = Depends(get_db), user=Depends(require_editor)) -> StudyService:
    svc = StudyService(db, ontology_id, user)
    try:
        svc.authorize_ontology()
    except StudyError as exc:
        _raise(exc)
    return svc


@router.get("/{ontology_id}/scenario-study-templates")
def list_templates(ontology_id: str, svc: StudyService = Depends(service)):
    return {"items": templates()}


@router.get("/{ontology_id}/scenario-study-definitions")
def list_studies(ontology_id: str, include_archived: bool = False, svc: StudyService = Depends(service)):
    query = svc.db.query(StudyDefinition).filter_by(ontology_id=ontology_id)
    if svc.user.role != "admin":
        query = query.filter_by(owner_id=svc.user.id)
    if not include_archived:
        query = query.filter(StudyDefinition.status != "archived")
    return {"items": [study_summary(row) for row in query.order_by(StudyDefinition.updated_at.desc()).all()]}


@router.post("/{ontology_id}/scenario-study-definitions", status_code=201)
def create_study(ontology_id: str, request: CreateStudyRequest, svc: StudyService = Depends(service)):
    try:
        return study_summary(svc.create(request.name, request.description, request.draft_spec))
    except StudyError as exc:
        svc.db.rollback()
        _raise(exc)


@router.get("/{ontology_id}/scenario-study-definitions/{study_id}")
def get_study(ontology_id: str, study_id: str, svc: StudyService = Depends(service)):
    try:
        study = svc.get_study(study_id)
        versions = svc.db.query(StudyVersion).filter_by(study_id=study.id).order_by(StudyVersion.version).all()
        return study_summary(study) | {"versions": [version_summary(row) for row in versions]}
    except StudyError as exc:
        _raise(exc)


@router.patch("/{ontology_id}/scenario-study-definitions/{study_id}")
def update_study(ontology_id: str, study_id: str, request: UpdateStudyRequest, svc: StudyService = Depends(service)):
    try:
        study = svc.get_study(study_id, write=True)
        return study_summary(svc.update(
            study,
            expected_etag=request.expected_etag,
            name=request.name,
            description=request.description,
            spec=request.draft_spec,
        ))
    except StudyError as exc:
        svc.db.rollback()
        _raise(exc)


@router.post("/{ontology_id}/scenario-study-definitions/{study_id}:validate")
def validate_study(ontology_id: str, study_id: str, svc: StudyService = Depends(service)):
    try:
        return svc.validate_draft(svc.get_study(study_id))
    except StudyError as exc:
        _raise(exc)


@router.post("/{ontology_id}/scenario-study-definitions/{study_id}:publish", status_code=201)
def publish_study(ontology_id: str, study_id: str, request: PublishStudyRequest, svc: StudyService = Depends(service)):
    try:
        study = svc.get_study(study_id, write=True)
        return version_summary(svc.publish(
            study,
            expected_etag=request.expected_etag,
            change_note=request.change_note,
            compatibility=request.compatibility,
        ))
    except StudyError as exc:
        svc.db.rollback()
        _raise(exc)


@router.get("/{ontology_id}/scenario-study-definitions/{study_id}/versions/{version}")
def get_version(ontology_id: str, study_id: str, version: int, svc: StudyService = Depends(service)):
    try:
        study = svc.get_study(study_id)
        return version_summary(svc.get_version(study, version))
    except StudyError as exc:
        _raise(exc)


@router.get("/{ontology_id}/scenario-study-definitions/{study_id}/versions/{version}:export")
def export_study_package(ontology_id: str, study_id: str, version: int, svc: StudyService = Depends(service)):
    try:
        study = svc.get_study(study_id)
        return export_package(svc.db, study, svc.get_version(study, version))
    except StudyError as exc:
        _raise(exc)


@router.post("/{ontology_id}/scenario-study-packages:inspect")
def inspect_study_package(ontology_id: str, request: InspectStudyPackageRequest,
                          svc: StudyService = Depends(service)):
    try:
        return inspect_package(svc.db, ontology_id, request.package, request.bindings)
    except StudyError as exc:
        _raise(exc)


@router.post("/{ontology_id}/scenario-study-packages:install", status_code=201)
def install_study_package(ontology_id: str, request: InstallStudyPackageRequest,
                          svc: StudyService = Depends(service)):
    try:
        return study_summary(install_package(
            svc.db, ontology_id, svc.user.id, request.package, request.bindings, name=request.name,
        ))
    except StudyError as exc:
        svc.db.rollback()
        _raise(exc)


@router.post("/{ontology_id}/scenario-study-definitions/{study_id}/cases", status_code=201)
def create_case(ontology_id: str, study_id: str, request: CreateCaseRequest, svc: StudyService = Depends(service)):
    try:
        study = svc.get_study(study_id)
        version = svc.get_version(study, request.study_version)
        return case_summary(svc.create_case(study, version, request))
    except IntegrityError as exc:
        svc.db.rollback()
        raise HTTPException(409, detail={"code": "case_binding_conflict", "message": "This Scenario or baseline Study version was claimed by another Case"}) from exc
    except StudyError as exc:
        svc.db.rollback()
        _raise(exc)


@router.get("/{ontology_id}/scenario-study-definitions/{study_id}/cases")
def list_cases(ontology_id: str, study_id: str, study_version: int | None = None,
               include_archived: bool = False, svc: StudyService = Depends(service)):
    try:
        study = svc.get_study(study_id)
        query = svc.db.query(CaseDefinition).join(
            StudyVersion, StudyVersion.id == CaseDefinition.study_version_id
        ).filter(StudyVersion.study_id == study.id)
        if study_version is not None:
            version = svc.get_version(study, study_version)
            query = query.filter(CaseDefinition.study_version_id == version.id)
        if not include_archived:
            query = query.filter(CaseDefinition.status == "active")
        rows = query.order_by(CaseDefinition.created_at.asc()).all()
        return {"items": [case_summary(row) for row in rows]}
    except StudyError as exc:
        _raise(exc)


@router.patch("/{ontology_id}/scenario-study-definitions/{study_id}/cases/{case_id}")
def update_case(ontology_id: str, study_id: str, case_id: str, request: UpdateCaseRequest, svc: StudyService = Depends(service)):
    try:
        study = svc.get_study(study_id)
        case, _ = svc.get_case(study, case_id)
        return case_summary(svc.update_case(
            case,
            expected_etag=request.expected_etag,
            name=request.name,
            parameter_overrides=request.parameter_overrides,
            ordered_actions=request.ordered_actions,
        ))
    except StudyError as exc:
        svc.db.rollback()
        _raise(exc)


@router.post("/{ontology_id}/scenario-study-definitions/{study_id}/cases/{case_id}:archive")
def archive_case(ontology_id: str, study_id: str, case_id: str, expected_etag: int,
                 svc: StudyService = Depends(service)):
    try:
        study = svc.get_study(study_id)
        case, _ = svc.get_case(study, case_id)
        if case.etag != expected_etag:
            raise StudyError("etag_mismatch", "Case changed; refresh and retry", 409, "expected_etag")
        if case.case_kind == "baseline":
            raise StudyError("baseline_immutable", "Baseline cases cannot be archived", 409, "case_id")
        active_runs = svc.db.query(ScenarioRun).filter_by(case_id=case.id, status="running").count()
        if active_runs:
            raise StudyError("case_running", "A running case cannot be archived", 409, "case_id")
        case.status = "archived"
        case.etag += 1
        case.updated_at = datetime.now(timezone.utc)
        svc.db.commit()
        svc.db.refresh(case)
        return case_summary(case)
    except StudyError as exc:
        svc.db.rollback()
        _raise(exc)


@router.post("/{ontology_id}/scenario-study-definitions/{study_id}/cases/{case_id}:compile", status_code=201)
def compile_case(ontology_id: str, study_id: str, case_id: str, svc: StudyService = Depends(service)):
    try:
        study = svc.get_study(study_id)
        case, version = svc.get_case(study, case_id)
        return plan_summary(svc.compile(study, case, version))
    except StudyError as exc:
        svc.db.rollback()
        _raise(exc)


@router.get("/{ontology_id}/scenario-study-definitions/{study_id}/versions/{version}/compare")
def compare_version(ontology_id: str, study_id: str, version: int,
                    svc: StudyService = Depends(service)):
    try:
        study = svc.get_study(study_id)
        selected_version = svc.get_version(study, version)
    except StudyError as exc:
        _raise(exc)
    cases = svc.db.query(CaseDefinition).filter_by(
        study_version_id=selected_version.id, status="active"
    ).order_by(CaseDefinition.case_kind.desc(), CaseDefinition.created_at).all()
    columns = []
    for case in cases:
        run = svc.db.query(ScenarioRun).filter_by(
            case_id=case.id, status="completed"
        ).order_by(ScenarioRun.created_at.desc()).first()
        run_plan = svc.db.get(RunPlan, run.run_plan_id) if run and run.run_plan_id else None
        stale = bool(run and (not run_plan or run_plan.case_hash != case.case_hash or run_plan.spec_hash != selected_version.spec_hash))
        manifest = run.result_manifest if run and isinstance(run.result_manifest, dict) else {}
        metrics = manifest.get("metrics", []) if isinstance(manifest, dict) else []
        columns.append({
            "case": case_summary(case),
            "run_id": run.id if run else None,
            "run_status": "stale" if stale else (run.status if run else "not_run"),
            "stale": stale,
            "stale_reason": "Case or Study version changed; rerun required" if stale else None,
            "plan_hash": run.input_digest if run else None,
            "metrics": metrics,
            "provenance": manifest.get("provenance") if run else None,
        })
    baseline_column = next((item for item in columns if item["case"]["case_kind"] == "baseline"), None)
    baseline_metrics = {item["key"]: item for item in (baseline_column or {}).get("metrics", [])}
    for column in columns:
        deltas = {}
        for metric in column["metrics"]:
            baseline = baseline_metrics.get(metric["key"])
            if baseline and baseline.get("unit") == metric.get("unit") and isinstance(metric.get("value"), (int, float)) and isinstance(baseline.get("value"), (int, float)):
                deltas[metric["key"]] = {
                    "absolute": metric["value"] - baseline["value"],
                    "unit": metric.get("unit", ""),
                    "direction": metric.get("direction", "neutral"),
                }
        column["deltas_from_baseline"] = deltas
    return {"study_id": study.id, "study_version": version, "spec_hash": selected_version.spec_hash,
            "baseline_case_id": baseline_column["case"]["id"] if baseline_column else None,
            "cases": columns}


@router.post("/{ontology_id}/scenario-study-definitions/{study_id}/cases/{case_id}/runs", status_code=202)
def start_case_run(ontology_id: str, study_id: str, case_id: str, request: StartStudyRunRequest,
                   svc: StudyService = Depends(service)):
    try:
        study = svc.get_study(study_id)
        case, version = svc.get_case(study, case_id)
        if case.etag != request.expected_etag:
            raise StudyError("etag_mismatch", "Case changed; refresh and retry", 409, "expected_etag")
        plan = svc.compile(study, case, version)
        existing = svc.db.query(ScenarioRun).filter_by(
            case_id=case.id, client_request_id=request.client_request_id
        ).first()
        if existing:
            if existing.run_plan_id != plan.id:
                raise StudyError("idempotency_conflict", "Request ID was used for another run plan", 409, "client_request_id")
            return _run_summary(svc.db, existing)

        run = ScenarioRun(
            scenario_id=plan.scenario_id,
            revision=plan.scenario_revision,
            case_key=case.name,
            logic_asset_key=f"study:{study.id}",
            logic_version=version.spec_version,
            parameters={"run_plan_id": plan.id, "plan_hash": plan.plan_hash},
            status="queued",
            progress=0,
            current_stage="queued",
            input_digest=plan.plan_hash,
            study_id=study.id,
            case_id=case.id,
            run_plan_id=plan.id,
            client_request_id=request.client_request_id,
            created_by=svc.user.id,
        )
        svc.db.add(run)
        svc.db.flush()
        for index, node in enumerate(plan.plan_json["nodes"]):
            svc.db.add(ScenarioRunStage(
                run_id=run.id,
                sequence=index + 1,
                stage_key=f"node_{index:03d}",
                summary_json={"node_key": node["key"], "kind": node["kind"]},
            ))
        svc.db.add(ScenarioAudit(
            scenario_id=plan.scenario_id,
            run_id=run.id,
            actor_id=svc.user.id,
            operation="run_study_case",
            summary={"study_id": study.id, "study_version": version.version,
                     "case_id": case.id, "run_plan_id": plan.id, "plan_hash": plan.plan_hash},
            request_id=request.client_request_id,
        ))
        svc.db.commit()
        svc.db.refresh(run)
        try:
            task = run_scenario.apply_async(args=(run.id,))
            run.celery_task_id = task.id
            svc.db.commit()
        except Exception as exc:
            run.status = "failed"
            run.current_stage = "dispatch"
            run.error_json = {"code": "celery_dispatch_failed", "message": "Run could not be queued"}
            run.completed_at = datetime.now(timezone.utc)
            svc.db.commit()
        return _run_summary(svc.db, run)
    except StudyError as exc:
        svc.db.rollback()
        _raise(exc)


def _run_summary(db, run: ScenarioRun):
    stages = db.query(ScenarioRunStage).filter_by(run_id=run.id).order_by(ScenarioRunStage.sequence).all()
    artifacts = db.query(ScenarioRunArtifact).filter_by(run_id=run.id).order_by(ScenarioRunArtifact.artifact_type).all()
    return {
        "id": run.id,
        "scenario_id": run.scenario_id,
        "scenario_revision": run.revision,
        "artifacts": [{"id": item.id, "artifact_type": item.artifact_type, "content_digest": item.content_digest} for item in artifacts],
        "case_id": run.case_id,
        "run_plan_id": run.run_plan_id,
        "status": run.status,
        "progress": run.progress,
        "current_stage": run.current_stage,
        "input_digest": run.input_digest,
        "output_digest": run.output_digest,
        "error": run.error_json,
        "result_manifest": run.result_manifest,
        "stages": [{"key": item.stage_key, "status": item.status,
                    "summary": item.summary_json, "error": item.error_json,
                    "duration_ms": item.duration_ms} for item in stages],
    }


@router.get("/{ontology_id}/scenario-study-runs/{run_id}")
def get_study_run(ontology_id: str, run_id: str, svc: StudyService = Depends(service)):
    # The ownership join is expressed explicitly to avoid trusting a run's
    # opaque study_id before checking the study resource.
    run = svc.db.query(ScenarioRun).join(
        StudyDefinition, StudyDefinition.id == ScenarioRun.study_id
    ).filter(ScenarioRun.id == run_id, StudyDefinition.ontology_id == ontology_id).first()
    if not run:
        raise HTTPException(404, detail={"code": "run_not_found", "message": "Run not found"})
    try:
        svc.get_study(run.study_id)
    except StudyError as exc:
        _raise(exc)
    return _run_summary(svc.db, run)


@router.post("/{ontology_id}/scenario-study-runs/{run_id}:cancel")
def cancel_study_run(ontology_id: str, run_id: str, svc: StudyService = Depends(service)):
    run = svc.db.query(ScenarioRun).filter_by(id=run_id).first()
    if not run:
        raise HTTPException(404, detail={"code": "run_not_found", "message": "Run not found"})
    try:
        svc.get_study(run.study_id)
    except StudyError as exc:
        _raise(exc)
    if run.status in {"queued", "running"}:
        run.cancel_requested = True
        if run.status == "queued":
            run.status = "cancelled"
            run.current_stage = "cancelled"
            run.completed_at = datetime.now(timezone.utc)
            for stage in svc.db.query(ScenarioRunStage).filter_by(run_id=run.id, status="pending").all():
                stage.status = "cancelled"
                stage.completed_at = run.completed_at
        svc.db.commit()
        svc.db.refresh(run)
    return _run_summary(svc.db, run)


@router.post("/{ontology_id}/scenario-study-runs/{run_id}:retry", status_code=202)
def retry_study_run(ontology_id: str, run_id: str, request: RetryStudyRunRequest,
                    svc: StudyService = Depends(service)):
    previous = svc.db.query(ScenarioRun).filter_by(id=run_id).first()
    if not previous or not previous.run_plan_id:
        raise HTTPException(404, detail={"code": "run_not_found", "message": "Run not found"})
    try:
        study = svc.get_study(previous.study_id)
    except StudyError as exc:
        _raise(exc)
    if previous.status not in {"failed", "cancelled"}:
        raise HTTPException(409, detail={"code": "run_not_retryable", "message": "Only failed or cancelled runs can be retried"})
    prior = svc.db.query(ScenarioRun).filter_by(
        case_id=previous.case_id, client_request_id=request.client_request_id
    ).first()
    if prior:
        if prior.retry_of_run_id != previous.id:
            raise HTTPException(409, detail={"code": "idempotency_conflict", "message": "Request ID belongs to another run"})
        return _run_summary(svc.db, prior)
    plan = svc.db.get(RunPlan, previous.run_plan_id)
    if not plan:
        raise HTTPException(409, detail={"code": "run_plan_missing", "message": "The pinned RunPlan is unavailable"})
    run = ScenarioRun(
        scenario_id=previous.scenario_id,
        revision=previous.revision,
        case_key=previous.case_key,
        logic_asset_key=previous.logic_asset_key,
        logic_version=previous.logic_version,
        parameters=previous.parameters,
        status="queued",
        progress=0,
        current_stage="queued",
        input_digest=previous.input_digest,
        study_id=previous.study_id,
        case_id=previous.case_id,
        run_plan_id=previous.run_plan_id,
        client_request_id=request.client_request_id,
        retry_of_run_id=previous.id,
        created_by=svc.user.id,
    )
    svc.db.add(run)
    svc.db.flush()
    for index, node in enumerate(plan.plan_json["nodes"]):
        svc.db.add(ScenarioRunStage(run_id=run.id, sequence=index + 1,
            stage_key=f"node_{index:03d}", summary_json={"node_key": node["key"], "kind": node["kind"]}))
    svc.db.add(ScenarioAudit(scenario_id=run.scenario_id, run_id=run.id, actor_id=svc.user.id,
        operation="retry_study_run", summary={"retry_of_run_id": previous.id, "run_plan_id": plan.id},
        request_id=request.client_request_id))
    svc.db.commit()
    svc.db.refresh(run)
    try:
        task = run_scenario.apply_async(args=(run.id,))
        run.celery_task_id = task.id
        svc.db.commit()
    except Exception:
        run.status = "failed"
        run.current_stage = "dispatch"
        run.error_json = {"code": "celery_dispatch_failed", "message": "Run could not be queued"}
        run.completed_at = datetime.now(timezone.utc)
        svc.db.commit()
    return _run_summary(svc.db, run)
