"""Persistent Scenario and immutable revision APIs."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.deps import get_current_user, get_db, require_editor
from app.models.v2.scenario import ScenarioAudit, ScenarioChangeSet, ScenarioGrant, ScenarioResource, ScenarioRevision, ScenarioRun, ScenarioRunStage
from app.models.v2.logic_asset import LogicAsset
from app.schemas.v2.scenario import ChangeSetRequest, CreateScenarioRequest, GrantRequest, RevisionRequest, RunRequest, Edit
from uuid import uuid4
from app.services.v2.logic_assets import ASSET_DEFINITIONS, execute
from app.services.v2.object_query.normalize import stable_hash
from app.services.v2.scenarios import ScenarioError, ScenarioService, _summary, error
from app.tasks.v2.scenario import STAGES, run_scenario

router = APIRouter()


def _http(exc: ScenarioError):
    raise HTTPException(status_code=exc.status, detail=error(exc)) from exc


def service(ontology_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    svc = ScenarioService(db, ontology_id, user)
    try:
        svc.authorize_ontology()
        return svc
    except ScenarioError as exc:
        _http(exc)


def revision_response(row, db):
    return {"id": row.id, "scenario_id": row.scenario_id, "revision": row.revision, "parent_revision": row.parent_revision,
            "changeset_id": row.changeset_id, "result_view_id": row.result_view_id, "content_digest": row.content_digest,
            "status": row.status, "error": row.error_json, "created_by": row.created_by, "created_at": row.created_at.isoformat()}


@router.post("/{ontology_id}/scenarios", status_code=201)
def create(ontology_id: str, request: CreateScenarioRequest, svc=Depends(service)):
    try:
        return _summary(svc.create(request))
    except ScenarioError as exc:
        _http(exc)


@router.get("/{ontology_id}/scenarios")
def listing(ontology_id: str, include_archived: bool = False, svc=Depends(service)):
    query = svc.db.query(ScenarioResource).filter(ScenarioResource.ontology_id == ontology_id)
    if svc.user.role != "admin":
        query = query.outerjoin(ScenarioGrant, ScenarioGrant.scenario_id == ScenarioResource.id).filter(
            (ScenarioResource.owner_id == svc.user.id) | (ScenarioGrant.principal_id == svc.user.id))
    if not include_archived:
        query = query.filter(ScenarioResource.status == "active")
    rows = query.order_by(ScenarioResource.updated_at.desc()).all()
    return {"scenarios": [_summary(row) for row in rows]}


@router.get("/{ontology_id}/scenarios/{scenario_id}")
def get(ontology_id: str, scenario_id: str, svc=Depends(service)):
    try:
        row = svc.get(scenario_id)
        revisions = svc.list_revisions(row)
        return _summary(row) | {"revisions": [revision_response(item, svc.db) for item in revisions]}
    except ScenarioError as exc:
        _http(exc)


@router.post("/{ontology_id}/scenarios/{scenario_id}:duplicate", status_code=201)
def duplicate(ontology_id: str, scenario_id: str, request: CreateScenarioRequest, svc=Depends(service)):
    try:
        source = svc.get(scenario_id)
        if request.base_view_id != source.base_view_id:
            raise ScenarioError("invalid_base_view", "Scenario duplication must retain the immutable base view", 422, "base_view_id")
        created = svc.create(request)
        created.description = request.description or f"Copy of {source.name}"
        if source.head_revision:
            latest = svc.db.query(ScenarioRevision).filter_by(scenario_id=source.id,
                      revision=source.head_revision).one()
            changeset = svc.db.get(ScenarioChangeSet, latest.changeset_id)
            if changeset and changeset.validation_report.get("replace_all"):
                from app.models.v2.action import OntologyActionType
                from app.services.v2.scenario_actions import compile_actions
                from app.services.v2.object_query.core import FalkorReadAdapter
                from app.models.v2.query_view import QueryDataView
                actions = [{"action_type_id": item["action_key"],
                            "parameters": item.get("parameters", {}).get("values", {})}
                           for item in changeset.ordered_edits if item.get("op") == "invoke_action"
                           and item.get("action_key") != "scenario_reset"]
                action_ids = {item["action_type_id"] for item in actions}
                types = {row.id: row for row in svc.db.query(OntologyActionType).filter(
                    OntologyActionType.id.in_(action_ids)).all()} if action_ids else {}
                base_view = svc.db.get(QueryDataView, source.base_view_id)
                base = FalkorReadAdapter(svc.graph._graph(base_view.graph_key),
                    graph_ontology_id=base_view.graph_key).read(ontology_id)
                edits = compile_actions(ontology_id, created.id, actions, types, base)
                if not edits:
                    edits = [Edit(sequence=0, op="invoke_action", action_key="scenario_reset",
                                  parameters={}, source_action="scenario_reset")]
                svc.revision(created, RevisionRequest(base_revision=0, expected_etag=created.etag,
                    client_request_id=f"duplicate:{uuid4()}", edits=edits), replace_all=True)
        svc.db.commit(); svc.db.refresh(created)
        return _summary(created)
    except ScenarioError as exc:
        _http(exc)


@router.post("/{ontology_id}/scenarios/{scenario_id}/changesets:validate")
def validate(ontology_id: str, scenario_id: str, request: ChangeSetRequest, svc=Depends(service)):
    try:
        scenario = svc.get(scenario_id)
        canonical, payload_hash = svc.validate(scenario, request)
        return {"valid": True, "payload_hash": payload_hash, "canonical": canonical, "base_revision": scenario.head_revision}
    except ScenarioError as exc:
        _http(exc)


@router.post("/{ontology_id}/scenarios/{scenario_id}/revisions", status_code=201)
def publish(ontology_id: str, scenario_id: str, request: RevisionRequest, svc=Depends(service)):
    try:
        row = svc.get(scenario_id, write=True)
        return revision_response(svc.revision(row, request), svc.db)
    except ScenarioError as exc:
        _http(exc)
    except Exception as exc:
        svc.db.rollback()
        raise HTTPException(422, detail={"code": "revision_build_failed", "path": "edits", "message": str(exc), "details": {}}) from exc


@router.get("/{ontology_id}/scenarios/{scenario_id}/revisions/{revision}")
def get_revision(ontology_id: str, scenario_id: str, revision: int, svc=Depends(service)):
    try:
        scenario = svc.get(scenario_id)
        row = svc.db.query(ScenarioRevision).filter_by(scenario_id=scenario.id, revision=revision).first()
        if not row:
            raise ScenarioError("not_found", "Revision not found", 404, "revision")
        return revision_response(row, svc.db)
    except ScenarioError as exc:
        _http(exc)


@router.post("/{ontology_id}/scenarios/{scenario_id}:archive")
def archive(ontology_id: str, scenario_id: str, svc=Depends(service)):
    try:
        row = svc.get(scenario_id, write=True); row.status = "archived"
        svc.db.add(ScenarioAudit(scenario_id=row.id, actor_id=svc.user.id, operation="archive", summary={}))
        svc.db.commit(); return _summary(row)
    except ScenarioError as exc:
        _http(exc)


@router.put("/{ontology_id}/scenarios/{scenario_id}/grants")
def grant(ontology_id: str, scenario_id: str, request: GrantRequest, svc=Depends(service)):
    try:
        scenario = svc.get(scenario_id, write=True)
        item = svc.db.get(ScenarioGrant, (scenario.id, request.principal_id))
        if item: item.role = request.role
        else: svc.db.add(ScenarioGrant(scenario_id=scenario.id, principal_id=request.principal_id, role=request.role))
        svc.db.add(ScenarioAudit(scenario_id=scenario.id, actor_id=svc.user.id, operation="grant", summary=request.model_dump()))
        svc.db.commit(); return {"scenario_id": scenario.id, "principal_id": request.principal_id, "role": request.role}
    except ScenarioError as exc:
        _http(exc)


@router.post("/{ontology_id}/scenarios/{scenario_id}/runs", status_code=202)
def run_logic(ontology_id: str, scenario_id: str, request: RunRequest, svc=Depends(service)):
    try:
        scenario = svc.get(scenario_id, write=True)
        if request.revision > scenario.head_revision:
            raise ScenarioError("revision_not_found", "Run revision is not available", 404, "revision")
        definition = next((item for item in ASSET_DEFINITIONS if item["asset_key"] == request.logic_asset_key), None)
        if not definition:
            raise ScenarioError("logic_asset_not_found", "Logic asset is not registered", 404, "logic_asset_key")
        asset = svc.db.query(LogicAsset).filter_by(ontology_id=ontology_id, asset_key=request.logic_asset_key).first()
        if not asset:
            asset = LogicAsset(ontology_id=ontology_id, **definition); svc.db.add(asset); svc.db.flush()
        run = ScenarioRun(scenario_id=scenario.id, revision=request.revision, case_key=request.case_key,
                          logic_asset_key=request.logic_asset_key, logic_version=request.logic_version,
                          parameters=request.parameters, status="queued", current_stage="queued", progress=0,
                          input_digest=stable_hash(request.parameters), created_by=svc.user.id)
        svc.db.add(run); svc.db.flush()
        svc.db.add_all([ScenarioRunStage(run_id=run.id, sequence=index + 1, stage_key=stage) for index, stage in enumerate(STAGES)])
        svc.db.add(ScenarioAudit(scenario_id=scenario.id, run_id=run.id, actor_id=svc.user.id, operation="run_logic", summary={"asset_key": request.logic_asset_key, "status": run.status}))
        svc.db.commit()
        try:
            task = run_scenario.delay(run.id)
            run.celery_task_id = task.id
            svc.db.commit()
        except Exception as exc:
            run.status = "failed"
            run.current_stage = "dispatch"
            run.error_json = {"code": "celery_dispatch_failed", "message": str(exc)}
            svc.db.commit()
        return {"id": run.id, "scenario_id": run.scenario_id, "revision": run.revision, "case_key": run.case_key, "logic_asset_key": run.logic_asset_key, "status": run.status, "progress": run.progress, "current_stage": run.current_stage, "output_digest": run.output_digest, "error": run.error_json}
    except ScenarioError as exc:
        _http(exc)


@router.get("/{ontology_id}/scenarios/{scenario_id}/runs")
def list_runs(ontology_id: str, scenario_id: str, svc=Depends(service)):
    try:
        scenario = svc.get(scenario_id)
        rows = svc.db.query(ScenarioRun).filter_by(scenario_id=scenario.id).order_by(ScenarioRun.created_at.desc()).all()
        result = []
        for row in rows:
            stages = svc.db.query(ScenarioRunStage).filter_by(run_id=row.id).order_by(ScenarioRunStage.sequence).all()
            result.append({"id": row.id, "revision": row.revision, "case_key": row.case_key, "logic_asset_key": row.logic_asset_key, "status": row.status, "progress": row.progress, "current_stage": row.current_stage, "output_digest": row.output_digest, "error": row.error_json, "duration_ms": row.duration_ms, "result_manifest": row.result_manifest, "stages": [{"key": stage.stage_key, "status": stage.status, "duration_ms": stage.duration_ms, "summary": stage.summary_json, "error": stage.error_json} for stage in stages]})
        return {"runs": result}
    except ScenarioError as exc:
        _http(exc)


@router.post("/{ontology_id}/scenarios/{scenario_id}/runs/{run_id}:cancel")
def cancel_run(ontology_id: str, scenario_id: str, run_id: str, svc=Depends(service)):
    try:
        scenario = svc.get(scenario_id, write=True)
        row = svc.db.query(ScenarioRun).filter_by(id=run_id, scenario_id=scenario.id).first()
        if not row: raise ScenarioError("not_found", "Run not found", 404, "run_id")
        if row.status in {"queued", "running"}: row.cancel_requested = True; row.status = "cancelled"; row.current_stage = "cancelled"
        svc.db.commit(); return {"id": row.id, "status": row.status, "cancel_requested": row.cancel_requested}
    except ScenarioError as exc:
        _http(exc)
