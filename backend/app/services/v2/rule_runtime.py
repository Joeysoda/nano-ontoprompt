"""Typed rule runtime: proposal governance, deterministic evaluation, alerts.

Evaluation reads only a pinned data view (or a scenario context resolved to
one) through ``QueryCore``, so the rule input inherits Object Query
validation, traversal and data authorization.  Time-series windows read the
``TimeSeriesSync`` rows published for that same view.

Semantics that the public Palantir docs leave open are fixed here explicitly:

* windows are trailing point windows; irregular sampling is reported, not hidden
* an output row's identity is (subject, episode start); an episode that keeps
  growing in a later view therefore updates the same alert instead of opening
  a duplicate
* a row "recovers" when it disappears from the output or its episode closes
* scenario contexts are preview-only; alerts are never derived from edits that
  have not been merged to live data
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import isfinite
from statistics import median

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.v2.rule_runtime import RuleAlert, RuleAlertEvent, RuleEvaluation, RuleProposal, RuleResource, RuleVersion
from app.schemas.v2.object_query import LoadObjectSetRequest, ReadOptions
from app.schemas.v2.object_set import ObjectSetDefinition
from app.schemas.v2.rule_runtime import RuleLogic, SeriesWindowBlock
from app.services.v2.object_query.errors import ObjectQueryError
from app.services.v2.object_query.normalize import stable_hash

MAX_OUTPUT_ROWS = 10_000
STATUS = {
    "not_found": 404, "forbidden": 403, "stale_proposal": 409, "proposal_closed": 409,
    "duplicate_rule": 409, "rule_retired": 409, "scenario_commit_forbidden": 422,
    "graph_unavailable": 503, "view_expired": 410, "view_unavailable": 409,
    "invalid_transition": 409, "self_review_forbidden": 403, "action_run_mismatch": 409,
}


def fail(code: str, path: str, message: str, **details):
    raise ObjectQueryError(code, path, message, **details)


def now() -> datetime:
    return datetime.now(timezone.utc)


# ── Pure evaluation ──────────────────────────────────────────────


def compile_input(logic: RuleLogic) -> dict:
    """Fold filter blocks into the Object Set so QueryCore owns them."""
    expression = logic.input.model_dump(mode="json", exclude_none=True)
    for block in logic.blocks:
        if block.kind == "filter":
            expression = {"kind": "filter", "input": expression, "where": block.where.model_dump(mode="json", exclude_none=True)}
    return expression


def series_block(logic: RuleLogic) -> SeriesWindowBlock | None:
    return next((b for b in logic.blocks if b.kind == "series_window"), None)


def _clean_points(points: list[dict], path: str) -> list[tuple[str, float]]:
    by_time: dict[str, float] = {}
    for point in points:
        stamp, value = point.get("timestamp"), point.get("value")
        if not isinstance(stamp, str):
            fail("invalid_series", path, "Time series point requires a timestamp")
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not isfinite(value):
            fail("invalid_series", path, "Time series value must be a finite number", timestamp=stamp)
        key = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
        if key in by_time and by_time[key] != value:
            fail("conflicting_points", path, "Two different values share one timestamp", timestamp=key)
        by_time[key] = float(value)
    return sorted(by_time.items())


def _rolling(values: list[float], size: int, aggregate: str) -> list[float]:
    """Aggregate for each trailing window; index i covers values[i-size+1:i+1]."""
    if aggregate in {"avg", "sum"}:
        out, total = [], 0.0
        for i, value in enumerate(values):
            total += value
            if i >= size:
                total -= values[i - size]
            if i >= size - 1:
                out.append(total / size if aggregate == "avg" else total)
        return out
    better = (lambda a, b: a >= b) if aggregate == "max" else (lambda a, b: a <= b)
    window: deque[int] = deque()
    out = []
    for i, value in enumerate(values):
        while window and better(value, values[window[-1]]):
            window.pop()
        window.append(i)
        if window[0] <= i - size:
            window.popleft()
        if i >= size - 1:
            out.append(values[window[0]])
    return out


def _breaches(value: float, op: str, threshold: float) -> bool:
    return {"gt": value > threshold, "gte": value >= threshold, "lt": value < threshold, "lte": value <= threshold}[op]


def window_episodes(points: list[dict], block: SeriesWindowBlock, path: str = "series") -> tuple[list[dict], dict]:
    clean = _clean_points(points, path)
    quality = {"points": len(clean), "duplicate_points": len(points) - len(clean), "irregular_intervals": 0}
    if len(clean) >= 3:
        stamps = [datetime.fromisoformat(t) for t, _ in clean]
        gaps = [(b - a).total_seconds() for a, b in zip(stamps, stamps[1:])]
        typical = median(gaps)
        quality["typical_interval_seconds"] = typical
        quality["irregular_intervals"] = sum(1 for gap in gaps if typical and gap > 1.5 * typical)
    if len(clean) < block.window_points:
        return [], quality
    values = [v for _, v in clean]
    aggregates = _rolling(values, block.window_points, block.aggregate)
    episodes, start = [], None
    last = len(aggregates) - 1
    for j, value in enumerate(aggregates + [None]):
        hit = value is not None and _breaches(value, block.op, block.threshold)
        if hit and start is None:
            start = j
        if not hit and start is not None:
            end = j - 1
            if end - start + 1 >= block.min_consecutive_windows:
                run = aggregates[start:end + 1]
                peak = max(run) if block.op in {"gt", "gte"} else min(run)
                episodes.append({
                    "window_start": clean[start][0],
                    "window_end": clean[end + block.window_points - 1][0],
                    "breaching_windows": end - start + 1,
                    "peak": round(peak, 6),
                    "ongoing": end == last,
                })
            start = None
    quality["watermark"] = clean[-1][0]
    return episodes, quality


@dataclass
class EvaluationResult:
    rows: list[dict]
    input_count: int
    quality: dict = field(default_factory=dict)

    def digest(self) -> str:
        return stable_hash(sorted(([r["key"], r.get("window_end"), r.get("peak")] for r in self.rows), key=str))


def evaluate_logic(core, logic: RuleLogic, context, series_loader) -> EvaluationResult:
    request = LoadObjectSetRequest(expression=compile_input(logic), context=context, read=ReadOptions(page_size=200))
    records, _ = core.materialize(request)
    block = series_block(logic)
    for name in logic.output.evidence_properties + ([block.series_property] if block else []):
        for typ in {r.object_type for r in records}:
            core.policy.field_allowed(typ, name)
    rows, quality = [], {"objects_without_series": 0, "series": {}}
    for record in records:
        evidence = {name: record.properties.get(name) for name in logic.output.evidence_properties}
        base = {"subject_type": record.object_type, "subject_id": record.object_id, "evidence": evidence}
        if block is None:
            rows.append({**base, "key": f"{record.object_type}:{record.object_id}", "ongoing": True})
            continue
        series_id = record.properties.get(block.series_property)
        points = series_loader(series_id) if isinstance(series_id, str) else None
        if points is None:
            quality["objects_without_series"] += 1
            continue
        episodes, series_quality = window_episodes(points, block, f"series.{series_id}")
        quality["series"][series_id] = series_quality
        for episode in episodes:
            rows.append({**base, **episode, "series_id": series_id,
                         "key": f"{record.object_type}:{record.object_id}:{episode['window_start']}"})
        if len(rows) > MAX_OUTPUT_ROWS:
            fail("query_too_complex", "output", f"Rule produced more than {MAX_OUTPUT_ROWS} output rows")
    if len(rows) > MAX_OUTPUT_ROWS:
        fail("query_too_complex", "output", f"Rule produced more than {MAX_OUTPUT_ROWS} output rows")
    return EvaluationResult(rows, len(records), quality)


# ── Persistence and governance ───────────────────────────────────


def rule_summary(rule: RuleResource) -> dict:
    return {"id": rule.id, "api_name": rule.api_name, "display_name": rule.display_name, "description": rule.description,
            "status": rule.status, "current_version": rule.current_version, "owner_id": rule.owner_id,
            "created_at": rule.created_at.isoformat() if rule.created_at else None}


def proposal_summary(p: RuleProposal) -> dict:
    return {"id": p.id, "rule_id": p.rule_id, "base_version": p.base_version, "status": p.status, "author_id": p.author_id,
            "reviewer_id": p.reviewer_id, "self_approved": bool(p.self_approved), "description": p.description,
            "review_comment": p.review_comment, "old_logic": p.old_logic, "new_logic": p.new_logic}


def alert_summary(a: RuleAlert) -> dict:
    stamp = lambda v: v.isoformat() if v else None  # noqa: E731
    return {"id": a.id, "rule_id": a.rule_id, "rule_version_id": a.rule_version_id, "status": a.status, "severity": a.severity,
            "subject": {"object_type": a.subject_type, "object_id": a.subject_id},
            "window_start": a.window_start, "window_end": a.window_end, "properties": a.properties, "evidence": a.evidence,
            "suggested_action": a.suggested_action, "occurrence_count": a.occurrence_count,
            "recovered_at": stamp(a.recovered_at), "acknowledged_by": a.acknowledged_by, "acknowledged_at": stamp(a.acknowledged_at),
            "resolved_by": a.resolved_by, "resolved_at": stamp(a.resolved_at), "resolution_note": a.resolution_note,
            "resolution_action_run_id": a.resolution_action_run_id,
            "created_at": stamp(a.created_at)}


class RuleService:
    def __init__(self, db: Session, ontology_id: str, user, metadata):
        self.db, self.ontology_id, self.user, self.metadata = db, ontology_id, user, metadata

    def _event(self, rule_id, kind, *, alert_id=None, evaluation_id=None, **detail):
        self.db.add(RuleAlertEvent(rule_id=rule_id, alert_id=alert_id, evaluation_id=evaluation_id, kind=kind,
                                   actor_id=self.user.id, detail=detail))

    def rule(self, rule_id: str) -> RuleResource:
        rule = self.db.get(RuleResource, rule_id)
        if rule is None or rule.ontology_id != self.ontology_id:
            fail("not_found", "rule_id", "Rule not found")
        return rule

    def validate_logic(self, logic: RuleLogic) -> str:
        """Reject unknown types, properties and actions before any version exists."""
        from app.services.v2.object_query.resources import ResourceService
        _, validation, _ = ResourceService(self.db, self.ontology_id, self.user, self.metadata).prepare(
            ObjectSetDefinition(expression=compile_input(logic)))
        type_name = validation.result_type.api_name
        properties = self.metadata.resolve_type("object", type_name, "logic.input").properties
        block = series_block(logic)
        for name, path in [(n, "logic.output.evidence_properties") for n in logic.output.evidence_properties] + (
                [(block.series_property, "logic.blocks.series_property")] if block else []):
            if name not in properties:
                fail("unknown_property", path, f"Property '{name}' is not declared on '{type_name}'")
        if block and properties[block.series_property].data_type != "string":
            fail("metadata_mismatch", "logic.blocks.series_property", "Time Series Property must be a string series ID")
        if logic.action:
            from app.models.v2.action import OntologyActionType
            action = self.db.get(OntologyActionType, logic.action.action_id)
            if action is None or action.ontology_id != self.ontology_id:
                fail("unknown_action", "logic.action.action_id", "Bound Action type does not exist in this ontology")
            name = logic.action.object_parameter
            if name and not any(isinstance(p, dict) and p.get("name") == name and p.get("type") == "object"
                                and p.get("object_type") in (None, type_name) for p in (action.parameters or [])):
                fail("metadata_mismatch", "logic.action.object_parameter",
                     f"Action has no object parameter '{name}' accepting {type_name}")
        return type_name

    def create(self, request) -> tuple[RuleResource, RuleProposal]:
        self.validate_logic(request.logic)
        rule = RuleResource(ontology_id=self.ontology_id, api_name=request.api_name, display_name=request.display_name,
                            description=request.description, owner_id=self.user.id)
        self.db.add(rule)
        try:
            self.db.flush()
        except IntegrityError:
            self.db.rollback()
            fail("duplicate_rule", "api_name", "Rule api_name already exists in this ontology")
        proposal = RuleProposal(rule_id=rule.id, base_version=0, old_logic=None, new_logic=request.logic.model_dump(mode="json"),
                                description=request.description, author_id=self.user.id)
        self.db.add(proposal)
        self.db.flush()
        self._event(rule.id, "rule_created", api_name=rule.api_name)
        self._event(rule.id, "proposal_opened", proposal_id=proposal.id, base_version=0)
        self.db.commit()
        return rule, proposal

    def propose(self, rule_id: str, request) -> RuleProposal:
        rule = self.rule(rule_id)
        if rule.status == "retired":
            fail("rule_retired", "rule_id", "Retired rules cannot change")
        if request.base_version != rule.current_version:
            fail("stale_proposal", "base_version", "Proposal is based on an outdated rule version",
                 current_version=rule.current_version)
        self.validate_logic(request.logic)
        old = self._version(rule, rule.current_version).logic if rule.current_version else None
        proposal = RuleProposal(rule_id=rule.id, base_version=request.base_version, old_logic=old,
                                new_logic=request.logic.model_dump(mode="json"), description=request.description,
                                author_id=self.user.id)
        self.db.add(proposal)
        self.db.flush()
        self._event(rule.id, "proposal_opened", proposal_id=proposal.id, base_version=request.base_version)
        self.db.commit()
        return proposal

    def review(self, rule_id: str, proposal_id: str, request) -> RuleProposal:
        rule = self.rule(rule_id)
        proposal = self.db.get(RuleProposal, proposal_id)
        if proposal is None or proposal.rule_id != rule.id:
            fail("not_found", "proposal_id", "Proposal not found")
        if proposal.status != "open":
            fail("proposal_closed", "proposal_id", f"Proposal is already {proposal.status}")
        self_review = proposal.author_id == self.user.id
        if request.decision == "approve" and self_review and self.user.role != "admin":
            fail("self_review_forbidden", "proposal_id", "Authors cannot approve their own rule proposals")
        proposal.reviewer_id, proposal.review_comment, proposal.reviewed_at = self.user.id, request.comment, now()
        if request.decision == "reject":
            proposal.status = "rejected"
            self._event(rule.id, "proposal_rejected", proposal_id=proposal.id)
            self.db.commit()
            return proposal
        if proposal.base_version != rule.current_version:
            proposal.status = "stale"
            self.db.commit()
            fail("stale_proposal", "proposal_id", "Rule changed after this proposal was opened; rebase it",
                 current_version=rule.current_version)
        logic = RuleLogic.model_validate(proposal.new_logic)
        self.validate_logic(logic)
        version = RuleVersion(rule_id=rule.id, version=rule.current_version + 1, logic=proposal.new_logic,
                              logic_digest=stable_hash(proposal.new_logic), proposal_id=proposal.id)
        self.db.add(version)
        rule.current_version, rule.updated_at = version.version, now()
        proposal.status, proposal.self_approved = "approved", int(self_review)
        self._event(rule.id, "proposal_approved", proposal_id=proposal.id, version=version.version, self_approved=self_review)
        self.db.commit()
        return proposal

    def set_status(self, rule_id: str, status: str) -> RuleResource:
        rule = self.rule(rule_id)
        if rule.status == "retired":
            fail("rule_retired", "rule_id", "Retired rules cannot change")
        previous, rule.status, rule.updated_at = rule.status, status, now()
        self._event(rule.id, "status_changed", previous=previous, status=status)
        self.db.commit()
        return rule

    def _version(self, rule: RuleResource, version: int) -> RuleVersion:
        row = self.db.query(RuleVersion).filter_by(rule_id=rule.id, version=version).first()
        if row is None:
            fail("not_found", "version", "Rule version not found")
        return row

    def evaluate(self, rule_id: str, request, core, series_loader) -> dict:
        rule = self.rule(rule_id)
        if rule.status == "retired":
            fail("rule_retired", "rule_id", "Retired rules cannot be evaluated")
        if rule.current_version == 0:
            fail("not_found", "version", "Rule has no approved version yet")
        version = self._version(rule, request.version or rule.current_version)
        logic = RuleLogic.model_validate(version.logic)
        context = request.context
        if request.mode == "commit" and context.scenario_id:
            fail("scenario_commit_forbidden", "context.scenario_id",
                 "Scenario evaluations are preview-only; merge the scenario before raising live alerts")
        idempotency_key = stable_hash({"version": version.id, "view": context.data_view_id, "policy": core.policy.digest()})
        if request.mode == "commit":
            existing = self.db.query(RuleEvaluation).filter_by(rule_id=rule.id, idempotency_key=idempotency_key).first()
            if existing:
                return {**self._evaluation_summary(existing), "replayed": True}
        try:
            result = evaluate_logic(core, logic, context, series_loader)
        except ObjectQueryError as exc:
            if request.mode == "commit":
                row = RuleEvaluation(rule_id=rule.id, rule_version_id=version.id, data_view_id=context.data_view_id,
                                     idempotency_key=stable_hash({"failed": idempotency_key, "at": now().isoformat()}),
                                     status="failed", rule_status=rule.status, error=exc.as_dict(), principal_id=self.user.id)
                self.db.add(row)
                self.db.flush()
                self._event(rule.id, "evaluation_failed", evaluation_id=row.id, code=exc.code)
                self.db.commit()
            raise
        dedupe = {row["key"]: stable_hash({"rule": rule.id, "key": row["key"]}) for row in result.rows}
        existing_alerts = {a.dedupe_key: a for a in self.db.query(RuleAlert).filter(
            RuleAlert.rule_id == rule.id, RuleAlert.dedupe_key.in_(list(dedupe.values()) or [""])).all()}
        previous = (self.db.query(RuleEvaluation).filter_by(rule_id=rule.id, status="succeeded")
                    .order_by(RuleEvaluation.created_at.desc()).first())
        previous_keys = set(previous.output_keys) if previous else set()
        current_keys = set(dedupe.values())
        triggered = [r for r in result.rows if dedupe[r["key"]] not in existing_alerts]
        recurring = [r for r in result.rows if dedupe[r["key"]] in existing_alerts]
        ongoing_keys = {dedupe[r["key"]] for r in result.rows if r["ongoing"]}
        recovered_keys = (previous_keys - current_keys) | {
            dedupe[r["key"]] for r in recurring if not r["ongoing"] and existing_alerts[dedupe[r["key"]]].recovered_at is None}
        summary = {"mode": request.mode, "rule_version": version.version, "data_view_id": context.data_view_id,
                   "rule_status": rule.status, "input_count": result.input_count, "output_count": len(result.rows),
                   "triggered_count": len(triggered), "recurring_count": len(recurring),
                   "recovered_count": len(recovered_keys), "result_digest": result.digest(), "quality": result.quality}
        if request.mode == "preview":
            return {**summary, "rows": result.rows[:200], "rows_truncated": len(result.rows) > 200}
        evaluation = RuleEvaluation(rule_id=rule.id, rule_version_id=version.id, data_view_id=context.data_view_id,
                                    idempotency_key=idempotency_key, status="succeeded", rule_status=rule.status,
                                    input_count=result.input_count, output_count=len(result.rows),
                                    triggered_count=len(triggered), recovered_count=len(recovered_keys),
                                    output_keys=sorted(ongoing_keys), result_digest=summary["result_digest"],
                                    evidence={"quality": result.quality}, principal_id=self.user.id)
        self.db.add(evaluation)
        self.db.flush()
        effects_enabled = rule.status != "muted"
        for row in triggered:
            alert = RuleAlert(ontology_id=self.ontology_id, rule_id=rule.id, rule_version_id=version.id, dedupe_key=dedupe[row["key"]],
                              subject_type=row["subject_type"], subject_id=row["subject_id"], window_start=row.get("window_start"),
                              window_end=row.get("window_end"), severity=logic.output.severity, properties=dict(logic.output.properties),
                              evidence=self._row_evidence(row), suggested_action=self._suggested(logic, row) if effects_enabled else None,
                              first_evaluation_id=evaluation.id, last_evaluation_id=evaluation.id,
                              recovered_at=None if row["ongoing"] else now())
            self.db.add(alert)
            self.db.flush()
            self._event(rule.id, "triggered", alert_id=alert.id, evaluation_id=evaluation.id, key=row["key"])
            if not row["ongoing"]:
                self._event(rule.id, "recovered", alert_id=alert.id, evaluation_id=evaluation.id, reason="episode_closed")
        for row in recurring:
            alert = existing_alerts[dedupe[row["key"]]]
            if alert.last_evaluation_id == evaluation.id:
                continue
            changed = alert.window_end != row.get("window_end")
            alert.last_evaluation_id, alert.updated_at = evaluation.id, now()
            alert.occurrence_count += 1
            alert.window_end, alert.evidence = row.get("window_end"), self._row_evidence(row)
            if row["ongoing"] and alert.recovered_at is not None and alert.status != "resolved":
                alert.recovered_at = None
                self._event(rule.id, "recurred", alert_id=alert.id, evaluation_id=evaluation.id)
            elif changed:
                self._event(rule.id, "recurred", alert_id=alert.id, evaluation_id=evaluation.id, window_end=alert.window_end)
        if recovered_keys:
            for alert in self.db.query(RuleAlert).filter(RuleAlert.rule_id == rule.id, RuleAlert.dedupe_key.in_(list(recovered_keys))).all():
                if alert.recovered_at is None:
                    alert.recovered_at, alert.updated_at = now(), now()
                    self._event(rule.id, "recovered", alert_id=alert.id, evaluation_id=evaluation.id)
        self.db.commit()
        return {**self._evaluation_summary(evaluation), "recurring_count": len(recurring), "quality": result.quality, "replayed": False}

    @staticmethod
    def _row_evidence(row: dict) -> dict:
        return {k: row[k] for k in ("evidence", "series_id", "peak", "breaching_windows", "ongoing") if k in row}

    @staticmethod
    def _suggested(logic: RuleLogic, row: dict) -> dict | None:
        if not logic.action:
            return None
        parameters = dict(logic.action.parameters)
        if logic.action.object_parameter:
            parameters[logic.action.object_parameter] = {"object_type": row["subject_type"], "object_id": row["subject_id"]}
        return {"action_id": logic.action.action_id,
                "request": {"target_object_id": row["subject_id"], "parameters": parameters}}

    @staticmethod
    def _evaluation_summary(e: RuleEvaluation) -> dict:
        return {"id": e.id, "rule_id": e.rule_id, "rule_version_id": e.rule_version_id, "data_view_id": e.data_view_id,
                "status": e.status, "rule_status": e.rule_status, "input_count": e.input_count, "output_count": e.output_count,
                "triggered_count": e.triggered_count, "recovered_count": e.recovered_count, "result_digest": e.result_digest,
                "error": e.error, "mode": "commit"}

    def alert(self, alert_id: str) -> RuleAlert:
        alert = self.db.get(RuleAlert, alert_id)
        if alert is None or alert.ontology_id != self.ontology_id:
            fail("not_found", "alert_id", "Alert not found")
        return alert

    def transition(self, alert_id: str, target: str, note: str, action_run_id: str | None = None) -> RuleAlert:
        alert = self.alert(alert_id)
        allowed = {"acknowledged": {"open"}, "resolved": {"open", "acknowledged"}}[target]
        if alert.status not in allowed:
            fail("invalid_transition", "status", f"Cannot move alert from {alert.status} to {target}")
        if action_run_id is not None:
            from app.models.v2.action import OntologyActionRun
            run = self.db.get(OntologyActionRun, action_run_id)
            if target != "resolved":
                fail("invalid_transition", "action_run_id", "Only resolution can reference an Action run")
            if run is None or run.ontology_id != self.ontology_id:
                fail("not_found", "action_run_id", "Action run not found")
            if run.status != "completed" or run.target_object_id != alert.subject_id:
                fail("action_run_mismatch", "action_run_id", "Action run must be completed and target the alert subject")
            alert.resolution_action_run_id = run.id
        alert.status, alert.updated_at = target, now()
        if target == "acknowledged":
            alert.acknowledged_by, alert.acknowledged_at = self.user.id, now()
        else:
            alert.resolved_by, alert.resolved_at, alert.resolution_note = self.user.id, now(), note
        self._event(alert.rule_id, target, alert_id=alert.id, note=note, action_run_id=action_run_id)
        self.db.commit()
        return alert
