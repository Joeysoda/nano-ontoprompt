"""Rule runtime API: proposals, versions, evaluation and alert lifecycle."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db, require_editor
from app.models.ontology import OntologyProject
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
from app.services.v2.object_query.core import FalkorReadAdapter, QueryCore, QueryPolicy
from app.services.v2.object_query.data_views import require_ready
from app.services.v2.object_query.errors import ObjectQueryError
from app.services.v2.object_query.metadata import load_sql_metadata
from app.services.v2.rule_runtime import STATUS, RuleService, alert_summary, proposal_summary, rule_summary, series_loader
from app.services.v2.scenarios import ScenarioError, resolve_scenario_context

router = APIRouter()


def http_error(exc: ObjectQueryError) -> HTTPException:
    return HTTPException(status_code=STATUS.get(exc.code, 422), detail=exc.as_dict())


def authorize(ontology_id: str, db: Session, user) -> None:
    item = db.get(OntologyProject, ontology_id)
    if item is None or (user.role != "admin" and item.created_by != user.id):
        raise HTTPException(status_code=404, detail="Ontology not found or inaccessible")


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
    authorize(ontology_id, db, user)
    return RuleService(db, ontology_id, user, load_sql_metadata(db, ontology_id))


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
        core = QueryCore(service.metadata, reader(view), QueryPolicy(principal=user.id))
        request = body.model_copy(update={"context": context})
        return service.evaluate(rule_id, request, core, series_loader(db, view))
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
    authorize(ontology_id, db, user)
    query = db.query(RuleAlert).filter_by(ontology_id=ontology_id)
    for column, value in (("status", status), ("subject_type", subject_type), ("subject_id", subject_id), ("rule_id", rule_id)):
        if value is not None:
            query = query.filter(getattr(RuleAlert, column) == value)
    total = query.count()
    rows = query.order_by(RuleAlert.created_at.desc(), RuleAlert.id).offset(max(0, offset)).limit(max(1, min(limit, 500))).all()
    return {"alerts": [alert_summary(a) for a in rows], "total": total}


@router.get("/{ontology_id}/alerts/{alert_id}")
def get_alert(ontology_id: str, alert_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    try:
        alert = svc(ontology_id, db, user).alert(alert_id)
    except ObjectQueryError as exc:
        raise http_error(exc) from exc
    events = db.query(RuleAlertEvent).filter_by(alert_id=alert.id).order_by(RuleAlertEvent.created_at).all()
    return {"alert": alert_summary(alert),
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
