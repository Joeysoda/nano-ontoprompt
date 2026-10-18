"""Rule runtime API: proposals, versions, evaluation and alert lifecycle."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db, require_editor
from app.models.v2.query_view import QueryDataView
from app.models.v2.rule_runtime import RuleAlert, RuleAlertEvent, RuleEvaluation, RuleProposal, RuleResource, RuleVersion
from app.models.v2.time_series import TimeSeriesSync
from app.schemas.v2.rule_runtime import (
    AlertTransitionRequest,
    CreateRuleRequest,
    EvaluateRuleRequest,
    ProposeRuleChangeRequest,
    ReviewProposalRequest,
    RuleScheduleRequest,
    RuleStatusRequest,
)
from app.services.v2.authorization_service import redact_record
from app.services.v2.object_query.authorization import build_query_policy, policies_from_projection, require_ontology_access
from app.services.v2.object_query.core import FalkorReadAdapter, QueryCore
from app.services.v2.object_query.data_views import require_ready
from app.services.v2.object_query.errors import ObjectQueryError
from app.services.v2.object_query.metadata import load_sql_metadata
from app.services.v2.rule_runtime import STATUS, RuleService, alert_summary, proposal_summary, rule_summary, series_loader
from app.services.v2.scenarios import ScenarioError, resolve_scenario_context

router = APIRouter()


def http_error(exc: ObjectQueryError) -> HTTPException:
    return HTTPException(status_code=STATUS.get(exc.code, 422), detail=exc.as_dict())


def authorize(ontology_id: str, db: Session, user):
    """Plan A ontology/data decision; the same one Object Query terminals use."""
    try:
        return require_ontology_access(db, ontology_id, str(user.id), user.role)
    except ObjectQueryError as exc:
        raise HTTPException(status_code=404, detail="Ontology not found or inaccessible") from exc


def visible_alert(access, alert, series_property: str | None) -> dict | None:
    """Redact evidence for the viewer; alerts on invisible subjects are hidden."""
    summary = alert_summary(alert)
    shown = dict((summary["evidence"] or {}).get("evidence") or {})
    record = {"id": alert.subject_id, "entity_type": alert.subject_type,
              "properties": {**shown, **({series_property: summary["evidence"].get("series_id")} if series_property else {})}}
    safe = redact_record(access, record)
    if safe is None:
        return None
    # Plan A masks denied fields to None (keys stay), so always read the redacted values.
    safe_props = safe.get("properties") or {}
    evidence = {**summary["evidence"], "evidence": {k: safe_props.get(k) for k in shown}}
    if series_property and safe_props.get(series_property) is None:
        for derived in ("series_id", "peak", "breaching_windows"):
            evidence.pop(derived, None)
    return {**summary, "evidence": evidence}


def _series_properties(db: Session, alerts) -> dict[str, str | None]:
    out = {}
    for version_id in {a.rule_version_id for a in alerts}:
        logic = (db.get(RuleVersion, version_id).logic or {})
        block = next((b for b in logic.get("blocks", []) if b.get("kind") == "series_window"), None)
        out[version_id] = block.get("series_property") if block else None
    return out


def get_graph_reader():
    """Return view -> read adapter. Overridden in tests with in-memory graphs."""
    from app.services.v2.graph.falkordb_service import FalkorDBService
    service = FalkorDBService()

    def reader(view: QueryDataView):
        if not service.available:
            raise ObjectQueryError("graph_unavailable", "execution", "FalkorDB is unavailable")
        return FalkorReadAdapter(service._graph(view.graph_key), graph_ontology_id=view.graph_key)
    return reader


def svc(ontology_id: str, db: Session, user) -> RuleService:
    service = RuleService(db, ontology_id, user, load_sql_metadata(db, ontology_id))
    service.access = authorize(ontology_id, db, user)
    return service


@router.get("/{ontology_id}/rules")
def list_rules(ontology_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    authorize(ontology_id, db, user)
    rules = db.query(RuleResource).filter_by(ontology_id=ontology_id).order_by(RuleResource.created_at).all()
    return {"rules": [rule_summary(r) for r in rules]}


@router.post("/{ontology_id}/rules", status_code=201)
def create_rule(ontology_id: str, body: CreateRuleRequest, db: Session = Depends(get_db), user=Depends(require_editor)):
    try:
        rule, proposal = svc(ontology_id, db, user).create(body)
    except ObjectQueryError as exc:
        db.rollback()
        raise http_error(exc) from exc
    return {"rule": rule_summary(rule), "proposal": proposal_summary(proposal)}


@router.get("/{ontology_id}/rules/{rule_id}")
def get_rule(ontology_id: str, rule_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    try:
        rule = svc(ontology_id, db, user).rule(rule_id)
    except ObjectQueryError as exc:
        raise http_error(exc) from exc
    versions = db.query(RuleVersion).filter_by(rule_id=rule.id).order_by(RuleVersion.version).all()
    proposals = db.query(RuleProposal).filter_by(rule_id=rule.id).order_by(RuleProposal.created_at).all()
    return {"rule": rule_summary(rule),
            "versions": [{"version": v.version, "logic": v.logic, "logic_digest": v.logic_digest, "proposal_id": v.proposal_id} for v in versions],
            "proposals": [proposal_summary(p) for p in proposals]}


@router.post("/{ontology_id}/rules/{rule_id}/proposals", status_code=201)
def propose(ontology_id: str, rule_id: str, body: ProposeRuleChangeRequest, db: Session = Depends(get_db), user=Depends(require_editor)):
    try:
        return proposal_summary(svc(ontology_id, db, user).propose(rule_id, body))
    except ObjectQueryError as exc:
        db.rollback()
        raise http_error(exc) from exc


@router.post("/{ontology_id}/rules/{rule_id}/proposals/{proposal_id}:review")
def review(ontology_id: str, rule_id: str, proposal_id: str, body: ReviewProposalRequest,
           db: Session = Depends(get_db), user=Depends(require_editor)):
    try:
        return proposal_summary(svc(ontology_id, db, user).review(rule_id, proposal_id, body))
    except ObjectQueryError as exc:
        raise http_error(exc) from exc


@router.post("/{ontology_id}/rules/{rule_id}/status")
def set_status(ontology_id: str, rule_id: str, body: RuleStatusRequest, db: Session = Depends(get_db), user=Depends(require_editor)):
    try:
        return rule_summary(svc(ontology_id, db, user).set_status(rule_id, body.status))
    except ObjectQueryError as exc:
        raise http_error(exc) from exc


@router.post("/{ontology_id}/rules/{rule_id}/schedule")
def set_schedule(ontology_id: str, rule_id: str, body: RuleScheduleRequest, db: Session = Depends(get_db), user=Depends(require_editor)):
    try:
        return rule_summary(svc(ontology_id, db, user).set_schedule(rule_id, body.interval_minutes, body.source))
    except ObjectQueryError as exc:
        raise http_error(exc) from exc


@router.post("/{ontology_id}/rules/{rule_id}/evaluate")
def evaluate(ontology_id: str, rule_id: str, body: EvaluateRuleRequest, db: Session = Depends(get_db),
             user=Depends(get_current_user), reader=Depends(get_graph_reader)):
    service = svc(ontology_id, db, user)
    try:
        if body.context.ontology_id != ontology_id:
            raise ObjectQueryError("ontology_context_mismatch", "context.ontology_id", "Path and context ontology must match")
        if body.mode == "commit" and user.role not in ("admin", "editor"):
            raise ObjectQueryError("forbidden", "mode", "Committing rule evaluations requires the editor role")
        scenario_id = body.context.scenario_id
        try:
            context = resolve_scenario_context(db, body.context, ontology_id, user)
        except ScenarioError as exc:
            raise ObjectQueryError(exc.code, exc.path, exc.message) from exc
        if not context.data_view_id:
            raise ObjectQueryError("consistency_unavailable", "context.data_view_id", "Rule evaluation requires a pinned data view")
        context = context.model_copy(update={"consistency": "snapshot", "scenario_id": scenario_id})
        view = require_ready(db, context.data_view_id, ontology_id, principal_id=user.id, is_admin=user.role == "admin")
        policy = build_query_policy(db, ontology_id, str(user.id), user.role, preloaded_policies=policies_from_projection(service.access))
        core = QueryCore(service.metadata, reader(view), policy)
        request = body.model_copy(update={"context": context})
        return service.evaluate(rule_id, request, core, series_loader(db, view))
    except ObjectQueryError as exc:
        db.rollback()
        raise http_error(exc) from exc


@router.post("/{ontology_id}/rules/{rule_id}/evaluate:latest")
def evaluate_latest(ontology_id: str, rule_id: str, db: Session = Depends(get_db), user=Depends(require_editor),
                    reader=Depends(get_graph_reader)):
    """Commit an evaluation on the caller's newest ready view (the UI's "evaluate now")."""
    from datetime import datetime, timezone
    from app.schemas.v2.object_query import ExecutionContext
    from app.services.v2.rule_scheduler import latest_view
    service = svc(ontology_id, db, user)
    try:
        view = latest_view(db, ontology_id, user, datetime.now(timezone.utc))
        if view is None:
            raise ObjectQueryError("no_ready_view", "data_view", "No ready data view; pin current data in the Object Explorer first")
        policy = build_query_policy(db, ontology_id, str(user.id), user.role, preloaded_policies=policies_from_projection(service.access))
        request = EvaluateRuleRequest(context=ExecutionContext(ontology_id=ontology_id, consistency="snapshot", data_view_id=view.id), mode="commit")
        return service.evaluate(rule_id, request, QueryCore(service.metadata, reader(view), policy), series_loader(db, view))
    except ObjectQueryError as exc:
        db.rollback()
        raise http_error(exc) from exc


@router.get("/{ontology_id}/rules/{rule_id}/evaluations")
def list_evaluations(ontology_id: str, rule_id: str, limit: int = 50, db: Session = Depends(get_db), user=Depends(get_current_user)):
    try:
        rule = svc(ontology_id, db, user).rule(rule_id)
    except ObjectQueryError as exc:
        raise http_error(exc) from exc
    rows = (db.query(RuleEvaluation).filter_by(rule_id=rule.id).order_by(RuleEvaluation.created_at.desc())
            .limit(max(1, min(limit, 200))).all())
    return {"evaluations": [RuleService._evaluation_summary(r) for r in rows]}


@router.get("/{ontology_id}/alerts")
def list_alerts(ontology_id: str, status: str | None = None, subject_type: str | None = None, subject_id: str | None = None,
                rule_id: str | None = None, limit: int = 100, offset: int = 0,
                db: Session = Depends(get_db), user=Depends(get_current_user)):
    access = authorize(ontology_id, db, user)
    query = db.query(RuleAlert).filter_by(ontology_id=ontology_id)
    for column, value in (("status", status), ("subject_type", subject_type), ("subject_id", subject_id), ("rule_id", rule_id)):
        if value is not None:
            query = query.filter(getattr(RuleAlert, column) == value)
    rows = query.order_by(RuleAlert.created_at.desc(), RuleAlert.id).all()
    series = _series_properties(db, rows)
    visible = [item for item in (visible_alert(access, a, series[a.rule_version_id]) for a in rows) if item is not None]
    start = max(0, offset)
    return {"alerts": visible[start:start + max(1, min(limit, 500))], "total": len(visible)}


@router.get("/{ontology_id}/alerts/{alert_id}")
def get_alert(ontology_id: str, alert_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    try:
        service = svc(ontology_id, db, user)
        alert = service.alert(alert_id)
    except ObjectQueryError as exc:
        raise http_error(exc) from exc
    shown = visible_alert(service.access, alert, _series_properties(db, [alert])[alert.rule_version_id])
    if shown is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Alert not found"})
    events = db.query(RuleAlertEvent).filter_by(alert_id=alert.id).order_by(RuleAlertEvent.created_at).all()
    return {"alert": shown,
            "events": [{"kind": e.kind, "actor_id": e.actor_id, "detail": e.detail, "evaluation_id": e.evaluation_id,
                        "created_at": e.created_at.isoformat()} for e in events]}


@router.post("/{ontology_id}/alerts/{alert_id}:acknowledge")
def acknowledge(ontology_id: str, alert_id: str, body: AlertTransitionRequest, db: Session = Depends(get_db), user=Depends(require_editor)):
    try:
        return alert_summary(svc(ontology_id, db, user).transition(alert_id, "acknowledged", body.note, body.action_run_id))
    except ObjectQueryError as exc:
        raise http_error(exc) from exc


@router.post("/{ontology_id}/alerts/{alert_id}:resolve")
def resolve(ontology_id: str, alert_id: str, body: AlertTransitionRequest, db: Session = Depends(get_db), user=Depends(require_editor)):
    try:
        return alert_summary(svc(ontology_id, db, user).transition(alert_id, "resolved", body.note, body.action_run_id))
    except ObjectQueryError as exc:
        raise http_error(exc) from exc
