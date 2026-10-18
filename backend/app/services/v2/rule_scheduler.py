"""Scheduled rule evaluation (Automate-style time-based condition).

Semantics follow the public Automate contract: paused rules are not run on a
schedule (manual evaluation still works), muted rules are evaluated and
recorded but produce no suggested Actions.  Each scheduled run commits an
evaluation as the rule owner against a pinned view, so the evaluation
idempotency key still prevents duplicate alerts when the view is unchanged.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy.orm import Session

from app.models.user import User
from app.models.v2.query_view import QueryDataView
from app.models.v2.rule_runtime import RuleResource
from app.schemas.v2.object_query import ExecutionContext
from app.schemas.v2.rule_runtime import EvaluateRuleRequest
from app.services.v2.object_query.core import QueryCore, QueryPolicy
from app.services.v2.object_query.errors import ObjectQueryError
from app.services.v2.object_query.metadata import load_sql_metadata
from app.services.v2.rule_runtime import RuleService, series_loader


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def due_rules(db: Session, at: datetime) -> list[RuleResource]:
    rows = db.query(RuleResource).filter(RuleResource.schedule_minutes.isnot(None), RuleResource.current_version > 0,
                                         RuleResource.status.in_(("active", "muted"))).all()
    return [r for r in rows if r.last_scheduled_at is None or _aware(r.last_scheduled_at) + timedelta(minutes=r.schedule_minutes) <= at]


def latest_view(db: Session, ontology_id: str, owner: User, at: datetime) -> QueryDataView | None:
    query = db.query(QueryDataView).filter(QueryDataView.ontology_id == ontology_id, QueryDataView.status == "ready")
    if owner.role != "admin":
        query = query.filter(QueryDataView.created_by == owner.id)
    for view in query.order_by(QueryDataView.created_at.desc()).all():
        if view.retention_until is None or _aware(view.retention_until) > at:
            return view
    return None


def run_due(db: Session, *, reader: Callable, live_view_builder: Callable | None = None,
            at: datetime | None = None) -> list[dict]:
    """Evaluate every due rule once. ``reader(view)`` returns a graph read adapter."""
    at = at or datetime.now(timezone.utc)
    results = []
    for rule in due_rules(db, at):
        rule_id, ontology_id = rule.id, rule.ontology_id
        outcome = {"rule_id": rule_id}
        try:
            owner = db.get(User, rule.owner_id)
            if owner is None or not owner.is_active:
                raise ObjectQueryError("owner_unavailable", "owner_id", "Rule owner is no longer active")
            if rule.schedule_source == "live_snapshot":
                if live_view_builder is None:
                    raise ObjectQueryError("graph_unavailable", "schedule", "Live snapshots are unavailable")
                view = live_view_builder(db, ontology_id, owner)
            else:
                view = latest_view(db, ontology_id, owner, at)
                if view is None:
                    raise ObjectQueryError("no_ready_view", "schedule", "No ready data view is available to the rule owner")
            service = RuleService(db, ontology_id, owner, load_sql_metadata(db, ontology_id))
            context = ExecutionContext(ontology_id=ontology_id, consistency="snapshot", data_view_id=view.id)
            core = QueryCore(service.metadata, reader(view), QueryPolicy(principal=owner.id))
            summary = service.evaluate(rule_id, EvaluateRuleRequest(context=context, mode="commit"), core, series_loader(db, view))
            outcome.update(status="evaluated", evaluation_id=summary["id"], replayed=summary.get("replayed", False),
                           triggered_count=summary["triggered_count"], data_view_id=view.id)
            error = None
        except ObjectQueryError as exc:
            db.rollback()
            outcome.update(status="failed", code=exc.code)
            error = {"code": exc.code, "message": exc.issue.message}
        rule = db.get(RuleResource, rule_id)
        rule.last_scheduled_at, rule.schedule_error = at, error
        db.commit()
        results.append(outcome)
    return results
