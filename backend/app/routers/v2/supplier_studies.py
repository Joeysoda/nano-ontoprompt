"""Study/Case commands and read models for the supplier workbench."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from app.deps import get_current_user, get_db
from app.models.v2.query_view import QueryDataView
from app.models.v2.scenario import (ScenarioChangeSet, ScenarioResource, ScenarioRevision, ScenarioRun,
                                    ScenarioRunStage, ScenarioStudy, ScenarioStudyCase,
                                    ScenarioRunWarning, ScenarioRunArtifact, ScenarioAudit)
from app.services.v2.graph.falkordb_service import FalkorDBService
from app.services.v2.object_query.core import FalkorReadAdapter
from app.services.v2.object_query.normalize import stable_hash
from app.services.v2.scenarios import ScenarioService
from app.services.v2.supplier_action import compile_supplier_action
from app.services.v2.supplier_resilience import WOOD, validate_profile
from app.tasks.v2.scenario import STAGES, run_scenario

router = APIRouter()


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Options(StrictBody):
    etag: int
    scenario_time: str
    scope_items: list[str]
    smoothing_minutes: int = 0
    run_baseline_sim: bool = True
    parameter_projection: list[str] = Field(default_factory=list)


class Rename(StrictBody):
    etag: int
    name: str = Field(min_length=1, max_length=200)


class CreateCase(StrictBody):
    name: str = Field(min_length=1, max_length=100)
    source_case_id: str | None = None


class RenameCase(StrictBody):
    etag: int
    name: str = Field(min_length=1, max_length=100)


class ArchiveCase(StrictBody):
    etag: int


class Submit(StrictBody):
    etag: int
    client_request_id: str = Field(min_length=1, max_length=200)
    action_key: str = "replace_wood_supplier_v1"
    parameters: dict


class RunCommand(StrictBody):
    etag: int
    client_request_id: str = Field(min_length=1, max_length=200)


class RetryCommand(StrictBody):
    client_request_id: str = Field(min_length=1, max_length=200)


def _study(db, user, ontology_id, study_id):
    row = db.get(ScenarioStudy, study_id)
    if not row or row.ontology_id != ontology_id:
        raise HTTPException(404, "Study not found")
    if user.role != "admin" and row.owner_id != user.id:
        raise HTTPException(403, "Study access denied")
    return row


def _case(db, study, case_id):
    row = db.get(ScenarioStudyCase, case_id)
    if not row or row.study_id != study.id or db.get(ScenarioResource, row.scenario_id).status != "active":
        raise HTTPException(404, "Case not found")
    return row


def _snapshot(db, view_id, ontology_id):
    view = db.get(QueryDataView, view_id)
    if not view or view.ontology_id != ontology_id or view.status != "ready":
        raise HTTPException(409, "Pinned QueryDataView is not ready")
    graph = FalkorDBService()
    if not graph.available:
        raise HTTPException(503, "FalkorDB unavailable")
    return FalkorReadAdapter(graph._graph(view.graph_key), graph_ontology_id=view.graph_key).read(ontology_id)


def _compatibility(study, view):
    return stable_hash({"base": view.content_digest, "manifest": study.source_manifest_digest,
                        "time": study.scenario_time, "scope": study.scope_hash,
                        "smoothing": study.smoothing_minutes, "model": (study.model_key, study.model_version,
                        study.model_config_alias), "projection": study.parameter_projection})


def _latest_compatible_run(db, case, compatibility, run_id=None):
    """Resolve the one result identity shared by every Supplier Study read."""
    scenario = db.get(ScenarioResource, case.scenario_id)
    query = db.query(ScenarioRun).filter_by(
        case_id=case.id,
        revision=scenario.head_revision,
        case_definition_revision=case.definition_revision,
        compatibility_hash=compatibility,
        status="completed",
    )
    run = query.filter(ScenarioRun.id == run_id).first() if run_id else query.order_by(
        ScenarioRun.created_at.desc()
    ).first()
    if run_id and not run:
        raise HTTPException(409, "Pinned Run is not compatible with this Case context")
    if not run:
        return None, "no_compatible_result"
    manifest_view = (run.result_manifest or {}).get("provenance", {}).get("result_view_id")
    if run.result_view_id and manifest_view and run.result_view_id != manifest_view:
        return run, "inconsistent_result_view"
    if not run.result_view_id:
        return run, "missing_result_view"
    return run, "current_success"


def _context_ref(db, study, case, compatibility, run_id=None):
    scenario = db.get(ScenarioResource, case.scenario_id)
    revision = db.query(ScenarioRevision).filter_by(
        scenario_id=scenario.id, revision=scenario.head_revision
    ).first() if scenario.head_revision else None
    run, selection_reason = _latest_compatible_run(db, case, compatibility, run_id)
    revision_view_id = revision.result_view_id if revision else scenario.base_view_id
    selected_result_view_id = run.result_view_id if run and selection_reason == "current_success" else None
    token_payload = {
        "ontology_id": study.ontology_id,
        "study_id": study.id,
        "case_id": case.id,
        "case_definition_revision": case.definition_revision,
        "compatibility_hash": compatibility,
        "scenario_id": scenario.id,
        "scenario_revision": scenario.head_revision,
        "revision_view_id": revision_view_id,
        "selected_run_id": run.id if selected_result_view_id else None,
        "selected_result_view_id": selected_result_view_id,
    }
    return {
        **token_payload,
        "case_key": case.case_key,
        "study_etag": study.etag,
        "case_etag": case.etag,
        "changeset_id": revision.changeset_id if revision else None,
        "selected_run_status": run.status if run else None,
        "input_digest": run.input_digest if selected_result_view_id else None,
        "output_digest": run.output_digest if selected_result_view_id else None,
        "selection_reason": selection_reason,
        "context_token": stable_hash(token_payload),
    }


def _run_json(db, run):
    stages = db.query(ScenarioRunStage).filter_by(run_id=run.id).order_by(ScenarioRunStage.sequence).all()
    warnings = db.query(ScenarioRunWarning).filter_by(run_id=run.id).order_by(ScenarioRunWarning.code).all()
    artifacts = db.query(ScenarioRunArtifact).filter_by(run_id=run.id).order_by(ScenarioRunArtifact.artifact_type).all()
    return {"id": run.id, "case_id": run.case_id, "case_key": run.case_key, "status": run.status,
            "client_request_id": run.client_request_id, "case_definition_revision": run.case_definition_revision,
            "result_view_id": run.result_view_id, "retry_of_run_id": run.retry_of_run_id,
            "progress": run.progress, "current_stage": run.current_stage, "revision": run.revision,
            "depends_on_run_id": run.depends_on_run_id, "compatibility_hash": run.compatibility_hash,
            "duration_ms": run.duration_ms, "input_digest": run.input_digest, "output_digest": run.output_digest,
            "error": run.error_json, "result_manifest": run.result_manifest,
            "warnings": [{"code": item.code, "severity": item.severity, "message": item.message,
                          "object_ref": item.object_ref, "provenance": item.provenance} for item in warnings],
            "artifacts": [{"id": item.id, "type": item.artifact_type, "data_view_id": item.data_view_id,
                           "object_set_definition": item.object_set_definition,
                           "manifest": item.manifest_json, "content_digest": item.content_digest} for item in artifacts],
            "stages": [{"key": s.stage_key, "status": s.status, "duration_ms": s.duration_ms,
                        "summary": s.summary_json, "error": s.error_json} for s in stages]}


def _view(db, study):
    base_view = db.get(QueryDataView, study.base_view_id)
    compatibility = _compatibility(study, base_view)
    cases = [case for case in db.query(ScenarioStudyCase).filter_by(study_id=study.id).order_by(ScenarioStudyCase.sort_order).all()
             if db.get(ScenarioResource, case.scenario_id).status == "active"]
    entries = []
    for case in cases:
        scenario = db.get(ScenarioResource, case.scenario_id)
        revision = db.query(ScenarioRevision).filter_by(scenario_id=scenario.id, revision=scenario.head_revision).first() if scenario.head_revision else None
        runs = db.query(ScenarioRun).filter_by(case_id=case.id).order_by(ScenarioRun.created_at.desc()).limit(10).all()
        entries.append({"id": case.id, "key": case.case_key, "name": case.display_name, "kind": case.case_kind,
                        "protected": scenario.protected_demo,
                        "etag": case.etag, "definition_revision": case.definition_revision,
                        "action_key": case.action_key, "parameters": case.submitted_parameters,
                        "scenario_id": scenario.id, "scenario_revision": scenario.head_revision,
                        "changeset_id": revision.changeset_id if revision else None,
                        "revision_view_id": revision.result_view_id if revision else scenario.base_view_id,
                        "context_ref": _context_ref(db, study, case, compatibility),
                        "runs": [_run_json(db, run) for run in runs]})
    return {"id": study.id, "name": study.name, "description": study.description,
            "ontology_id": study.ontology_id, "etag": study.etag,
            "compatibility_hash": compatibility,
            "base_view_id": study.base_view_id, "source_manifest_digest": study.source_manifest_digest,
            "scenario_time": study.scenario_time, "scope": study.scope_definition,
            "smoothing_minutes": study.smoothing_minutes, "run_baseline_sim": study.run_baseline_sim,
            "model": {"key": study.model_key, "version": study.model_version,
                      "config_alias": study.model_config_alias},
            "parameter_projection": study.parameter_projection, "cases": entries}


@router.get("/{ontology_id}/scenario-studies/{study_id}")
def get_study(ontology_id: str, study_id: str, db=Depends(get_db), user=Depends(get_current_user)):
    return _view(db, _study(db, user, ontology_id, study_id))


@router.patch("/{ontology_id}/scenario-studies/{study_id}")
def rename_study(ontology_id: str, study_id: str, body: Rename, db=Depends(get_db), user=Depends(get_current_user)):
    study = _study(db, user, ontology_id, study_id)
    if study.etag != body.etag:
        raise HTTPException(409, "Study changed; reload before renaming")
    study.name = body.name.strip()
    if not study.name:
        raise HTTPException(422, "Study name cannot be blank")
    study.etag += 1
    study.updated_at = datetime.now(timezone.utc)
    db.commit()
    return _view(db, study)


@router.post("/{ontology_id}/scenario-studies/{study_id}/cases", status_code=201)
def create_case(ontology_id: str, study_id: str, body: CreateCase,
                db=Depends(get_db), user=Depends(get_current_user)):
    study = _study(db, user, ontology_id, study_id)
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "Case name cannot be blank")
    cases = db.query(ScenarioStudyCase).filter_by(study_id=study.id).all()
    if any(case.display_name.casefold() == name.casefold() and
           db.get(ScenarioResource, case.scenario_id).status == "active" for case in cases):
        raise HTTPException(409, "Case name already exists")
    if body.source_case_id:
        source = _case(db, study, body.source_case_id)
        if source.case_kind == "baseline":
            raise HTTPException(422, "Choose a candidate Case as the profile source")
    else:
        source = next((case for case in cases if case.case_kind == "candidate" and
                       db.get(ScenarioResource, case.scenario_id).status == "active"), None)
        if source is None:
            raise HTTPException(409, "A candidate profile is required")
    profile = dict(source.submitted_parameters)
    validate_profile(profile)
    scenario = ScenarioResource(ontology_id=ontology_id, owner_id=user.id, name=name,
                                description=f"{name} in {study.name}", base_view_id=study.base_view_id)
    db.add(scenario)
    db.flush()
    case = ScenarioStudyCase(study_id=study.id, scenario_id=scenario.id,
                             case_key=f"case_{uuid4().hex[:24]}", display_name=name,
                             case_kind="candidate", sort_order=max((item.sort_order for item in cases), default=0) + 1,
                             action_key="replace_wood_supplier_v1", submitted_parameters=profile,
                             parameters_hash=stable_hash(profile), synthetic_manifest_hash=study.source_manifest_digest)
    db.add(case)
    db.add(ScenarioAudit(scenario_id=scenario.id, actor_id=user.id, operation="create",
                         summary={"study_id": study.id, "source_case_id": source.id}))
    db.commit()
    return next(item for item in _view(db, study)["cases"] if item["id"] == case.id)


@router.patch("/{ontology_id}/scenario-studies/{study_id}/cases/{case_id}/name")
def rename_case(ontology_id: str, study_id: str, case_id: str, body: RenameCase,
                db=Depends(get_db), user=Depends(get_current_user)):
    study = _study(db, user, ontology_id, study_id)
    case = _case(db, study, case_id)
    if case.etag != body.etag:
        raise HTTPException(409, "Case changed; reload before renaming")
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "Case name cannot be blank")
    if db.query(ScenarioStudyCase).filter_by(study_id=study.id, display_name=name).filter(
            ScenarioStudyCase.id != case.id).first():
        raise HTTPException(409, "Case name already exists")
    case.display_name = name
    case.etag += 1
    scenario = db.get(ScenarioResource, case.scenario_id)
    scenario.name = name
    scenario.etag += 1
    scenario.updated_at = datetime.now(timezone.utc)
    db.add(ScenarioAudit(scenario_id=scenario.id, actor_id=user.id, operation="rename",
                         summary={"name": name}))
    db.commit()
    return next(item for item in _view(db, study)["cases"] if item["id"] == case.id)


@router.post("/{ontology_id}/scenario-studies/{study_id}/cases/{case_id}:archive")
def archive_case(ontology_id: str, study_id: str, case_id: str, body: ArchiveCase,
                 db=Depends(get_db), user=Depends(get_current_user)):
    study = _study(db, user, ontology_id, study_id)
    case = _case(db, study, case_id)
    if case.case_kind == "baseline" or db.get(ScenarioResource, case.scenario_id).protected_demo:
        raise HTTPException(422, "Prepared Cases cannot be archived")
    if case.etag != body.etag:
        raise HTTPException(409, "Case changed; reload before archiving")
    if db.query(ScenarioRun).filter_by(case_id=case.id).filter(ScenarioRun.status.in_(["queued", "running"])).first():
        raise HTTPException(409, "Wait for the active Run to finish")
    scenario = db.get(ScenarioResource, case.scenario_id)
    scenario.status = "archived"
    scenario.etag += 1
    scenario.updated_at = datetime.now(timezone.utc)
    db.add(ScenarioAudit(scenario_id=scenario.id, actor_id=user.id, operation="archive",
                         summary={"study_id": study.id, "case_id": case.id}))
    db.commit()
    return {"id": case.id, "status": "archived"}


@router.get("/{ontology_id}/scenario-studies/{study_id}/cases/{case_id}/graph")
def case_graph(ontology_id: str, study_id: str, case_id: str,
               run_id: str | None = Query(default=None),
               context_token: str | None = Query(default=None),
               db=Depends(get_db), user=Depends(get_current_user)):
    study = _study(db, user, ontology_id, study_id)
    case = _case(db, study, case_id)
    compatibility = _compatibility(study, db.get(QueryDataView, study.base_view_id))
    context = _context_ref(db, study, case, compatibility, run_id)
    if context_token and context_token != context["context_token"]:
        raise HTTPException(409, "Scenario context is stale; reload the Study")
    if context["selection_reason"] == "inconsistent_result_view":
        raise HTTPException(409, "Run result view provenance is inconsistent")
    view_id = context["selected_result_view_id"] or context["revision_view_id"]
    snapshot = _snapshot(db, view_id, ontology_id)
    kinds = {"Supplier", "SupplyOption", "Item", "Demand", "Operation", "MaterialRequirement", "Inventory", "DemandImpact"}
    nodes = [{"id": identity[1], "type": identity[0], "name": props.get("name", identity[1]),
              "properties": props} for identity, props in snapshot.objects.items() if identity[0] in kinds]
    ids = {node["id"] for node in nodes}
    edges = [{"source": source[1], "target": target[1], "type": relation}
             for source, relation, target in snapshot.edges if source[1] in ids and target[1] in ids]
    return {"context": {**context, "view_id": view_id,
                         "read_layer": "result" if context["selected_result_view_id"] else "revision"},
            "nodes": nodes, "edges": edges, "truncated": False}


@router.patch("/{ontology_id}/scenario-studies/{study_id}/options")
def update_options(ontology_id: str, study_id: str, body: Options, db=Depends(get_db), user=Depends(get_current_user)):
    study = _study(db, user, ontology_id, study_id)
    if study.etag != body.etag:
        raise HTTPException(409, "Study changed; reload before editing")
    if body.smoothing_minutes != 0:
        raise HTTPException(422, "Non-zero smoothing is not supported by this model")
    try:
        anchor = datetime.fromisoformat(body.scenario_time.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(422, "Invalid scenario time") from exc
    if not datetime(2021, 1, 1) <= anchor.replace(tzinfo=None) <= datetime(2021, 12, 31):
        raise HTTPException(422, "Scenario time must remain in the pinned fixture year")
    if not body.scope_items or not set(body.scope_items) <= set(WOOD):
        raise HTTPException(422, "Scope must contain one or both wood materials")
    allowed = {"lead_days", "minimum_order", "order_multiple", "capacity", "cost_multiplier"}
    if not set(body.parameter_projection) <= allowed:
        raise HTTPException(422, "Unknown parameter projection")
    study.scenario_time = body.scenario_time
    study.scope_definition = {"kind": "objects_on_graph", "items": body.scope_items}
    study.scope_hash = stable_hash(study.scope_definition)
    study.run_baseline_sim = body.run_baseline_sim
    study.parameter_projection = body.parameter_projection
    study.etag += 1
    study.updated_at = datetime.now(timezone.utc)
    db.commit()
    return _view(db, study)


@router.patch("/{ontology_id}/scenario-studies/{study_id}/cases/{case_id}")
def submit_action(ontology_id: str, study_id: str, case_id: str, body: Submit,
                  db=Depends(get_db), user=Depends(get_current_user)):
    study = _study(db, user, ontology_id, study_id)
    case = _case(db, study, case_id)
    if case.case_kind == "baseline":
        raise HTTPException(422, "Baseline has no Action")
    prior = db.query(ScenarioChangeSet).filter_by(scenario_id=case.scenario_id,
                                                   client_request_id=body.client_request_id).first()
    if prior:
        prior_profile = prior.ordered_edits[0].get("parameters", {}) if prior.ordered_edits else {}
        if stable_hash(prior_profile) != stable_hash(body.parameters):
            raise HTTPException(409, "Action request ID was used with different parameters")
        revision = db.get(ScenarioRevision, prior.validation_report.get("revision_id"))
        return {"case": next(item for item in _view(db, study)["cases"] if item["id"] == case.id),
                "revision": revision.revision, "changeset_id": revision.changeset_id}
    if body.etag != case.etag:
        raise HTTPException(409, "Case changed; reload before submitting")
    if body.action_key != "replace_wood_supplier_v1":
        raise HTTPException(422, "Unsupported Action")
    try:
        validate_profile(body.parameters)
        scenario = ScenarioService(db, ontology_id, user).get(case.scenario_id, write=True)
        snapshot = _snapshot(db, scenario.base_view_id, ontology_id)
        command = compile_supplier_action(ontology_id, scenario, snapshot, body.parameters, body.client_request_id)
        revision = ScenarioService(db, ontology_id, user).revision(scenario, command)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    case.submitted_parameters = body.parameters
    case.parameters_hash = stable_hash(body.parameters)
    case.definition_revision += 1
    case.etag += 1
    db.commit()
    return {"case": next(item for item in _view(db, study)["cases"] if item["id"] == case.id),
            "revision": revision.revision, "changeset_id": revision.changeset_id}


def _enqueue(db, user, study, case, compatibility, dependency=None, request_id=None, retry_of=None):
    scenario = db.get(ScenarioResource, case.scenario_id)
    profile = case.submitted_parameters
    frozen = {"profile": profile, "scenario_time": study.scenario_time,
              "scope": study.scope_definition, "smoothing_minutes": study.smoothing_minutes,
              "definition_revision": case.definition_revision,
              "manifest_hash": study.source_manifest_digest,
              "model_config_alias": study.model_config_alias,
              "parameter_projection": study.parameter_projection}
    run = ScenarioRun(scenario_id=scenario.id, study_id=study.id, case_id=case.id,
                      revision=scenario.head_revision, case_key=case.case_key,
                      logic_asset_key=study.model_key, logic_version=study.model_version,
                      compatibility_hash=compatibility, depends_on_run_id=dependency,
                      client_request_id=request_id or str(uuid4()),
                      case_definition_revision=case.definition_revision, retry_of_run_id=retry_of,
                      parameters=frozen, input_digest=stable_hash(frozen), created_by=user.id,
                      status="queued", current_stage="queued")
    db.add(run); db.flush()
    db.add_all([ScenarioRunStage(run_id=run.id, sequence=i + 1, stage_key=key) for i, key in enumerate(STAGES)])
    db.commit()
    try:
        task = run_scenario.apply_async(args=(run.id,), countdown=1 if dependency else 0)
        run.celery_task_id = task.id
        db.commit()
    except Exception as exc:
        run.status = "failed"; run.current_stage = "dispatch"
        run.error_json = {"code": "celery_dispatch_failed", "message": str(exc)}
        db.commit()
    return run


@router.post("/{ontology_id}/scenario-studies/{study_id}/cases/{case_id}/runs", status_code=status.HTTP_202_ACCEPTED)
def start_run(ontology_id: str, study_id: str, case_id: str, body: RunCommand,
              db=Depends(get_db), user=Depends(get_current_user)):
    study = _study(db, user, ontology_id, study_id)
    case = _case(db, study, case_id)
    if case.etag != body.etag:
        raise HTTPException(409, "Case changed; reload before running")
    if case.case_kind != "baseline" and case.definition_revision == 0:
        raise HTTPException(409, "Submit the candidate Action before Run")
    view = db.get(QueryDataView, study.base_view_id)
    if not view or view.status != "ready":
        raise HTTPException(409, "Base view unavailable")
    compatibility = _compatibility(study, view)
    existing_request = db.query(ScenarioRun).filter_by(case_id=case.id, client_request_id=body.client_request_id).first()
    if existing_request:
        if (existing_request.case_definition_revision != case.definition_revision
                or existing_request.compatibility_hash != compatibility):
            raise HTTPException(409, "Run request ID was used with different inputs")
        return _run_json(db, existing_request)
    dependency = None
    if case.case_kind != "baseline":
        baseline = db.query(ScenarioStudyCase).filter_by(study_id=study.id, case_key="baseline").one()
        existing = db.query(ScenarioRun).filter_by(case_id=baseline.id, compatibility_hash=compatibility,
                                                       status="completed").order_by(ScenarioRun.created_at.desc()).first()
        if existing and not db.query(ScenarioRunArtifact).filter_by(
                run_id=existing.id, artifact_type="demand_impacts").first():
            # A completed Run from before evidence persistence is not a valid
            # dependency for the current contract; preserve it for audit and
            # create a fresh attempt with complete artifacts.
            existing = None
        if not existing:
            if not study.run_baseline_sim:
                raise HTTPException(409, "A compatible Baseline run is required")
            queued = db.query(ScenarioRun).filter_by(case_id=baseline.id, compatibility_hash=compatibility).filter(
                ScenarioRun.status.in_(["queued", "running"])).order_by(ScenarioRun.created_at.desc()).first()
            existing = queued or _enqueue(db, user, study, baseline, compatibility)
        dependency = existing.id
    run = _enqueue(db, user, study, case, compatibility, dependency, request_id=body.client_request_id)
    return _run_json(db, run)


@router.get("/{ontology_id}/scenario-runs/{run_id}")
def get_run(ontology_id: str, run_id: str, db=Depends(get_db), user=Depends(get_current_user)):
    run = db.get(ScenarioRun, run_id)
    if not run or not run.study_id:
        raise HTTPException(404, "Run not found")
    _study(db, user, ontology_id, run.study_id)
    return _run_json(db, run)


@router.post("/{ontology_id}/scenario-runs/{run_id}:cancel")
def cancel_run(ontology_id: str, run_id: str, db=Depends(get_db), user=Depends(get_current_user)):
    run = db.get(ScenarioRun, run_id)
    if not run or not run.study_id:
        raise HTTPException(404, "Run not found")
    _study(db, user, ontology_id, run.study_id)
    if run.status in ("queued", "running"):
        run.cancel_requested = True
        db.commit()
    return _run_json(db, run)


@router.post("/{ontology_id}/scenario-runs/{run_id}:retry", status_code=status.HTTP_202_ACCEPTED)
def retry_run(ontology_id: str, run_id: str, body: RetryCommand, db=Depends(get_db), user=Depends(get_current_user)):
    previous = db.get(ScenarioRun, run_id)
    if not previous or not previous.study_id:
        raise HTTPException(404, "Run not found")
    study = _study(db, user, ontology_id, previous.study_id)
    if previous.status not in {"failed", "cancelled"}:
        raise HTTPException(409, "Only a failed or cancelled Run can be retried")
    case = _case(db, study, previous.case_id)
    result = start_run(ontology_id, study.id, case.id,
                       RunCommand(etag=case.etag, client_request_id=body.client_request_id), db, user)
    retry = db.get(ScenarioRun, result["id"])
    if retry.id != previous.id and retry.retry_of_run_id is None:
        retry.retry_of_run_id = previous.id
        db.commit()
    return _run_json(db, retry)


@router.get("/{ontology_id}/scenario-studies/{study_id}/compare")
def compare(ontology_id: str, study_id: str, db=Depends(get_db), user=Depends(get_current_user)):
    study = _study(db, user, ontology_id, study_id)
    view = db.get(QueryDataView, study.base_view_id)
    compatibility = _compatibility(study, view)
    cases = [case for case in db.query(ScenarioStudyCase).filter_by(study_id=study.id).order_by(ScenarioStudyCase.sort_order).all()
             if db.get(ScenarioResource, case.scenario_id).status == "active"]
    columns = []
    for case in cases:
        run, selection_reason = _latest_compatible_run(db, case, compatibility)
        context = _context_ref(db, study, case, compatibility)
        selected = run if selection_reason == "current_success" else None
        columns.append({"case_key": case.case_key, "name": case.display_name,
                        "run_id": selected.id if selected else None,
                        "context_token": context["context_token"],
                        "selection_reason": selection_reason,
                        "result_view_id": selected.result_view_id if selected else None,
                        "metrics": selected.result_manifest.get("metrics", []) if selected and selected.result_manifest else [],
                        "duration_ms": selected.duration_ms if selected else None})
    baseline = {m["key"]: m["value"] for m in columns[0]["metrics"]} if columns else {}
    for col in columns:
        col["deltas"] = {m["key"]: round(m["value"] - baseline[m["key"]], 4)
                         for m in col["metrics"] if m["key"] in baseline}
    compatible = all(col["run_id"] for col in columns)
    preference = {"on_time_delivery_rate": "high", "late_demand_count": "low",
                  "shortage_quantity": "low", "estimated_procurement_cost": "low",
                  "weighted_lead_time_days": "low", "affected_customer_count": "low"}
    rankings = {}
    if compatible:
        for key, direction in preference.items():
            entries = [(col["case_key"], next((metric for metric in col["metrics"] if metric["key"] == key), None))
                       for col in columns]
            if any(metric is None or metric["completeness"] != "exact" for _, metric in entries):
                continue
            ordered = sorted(entries, key=lambda pair: (pair[1]["value"] * (-1 if direction == "high" else 1), pair[0]))
            groups = []
            for case_key, metric in ordered:
                if not groups or metric["value"] != groups[-1][0][1]:
                    groups.append([])
                groups[-1].append((case_key, metric["value"]))
            rankings[key] = [[case_key for case_key, _ in group] for group in groups]
    return {"compatible": compatible, "compatibility_hash": compatibility,
            "metric_contract": "supplier-metrics-v1", "rankings": rankings,
            "ranking_available": compatible and len(rankings) == len(preference), "columns": columns}
