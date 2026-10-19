"""Decision Package digest and governance state transitions."""
from hashlib import sha256
import json


TRANSITIONS = {
    "draft": {"submit": "in_review", "supersede": "superseded"},
    "in_review": {"approve": "approved", "reject": "rejected", "supersede": "superseded"},
    "rejected": {"revise": "draft", "supersede": "superseded"},
    "approved": {"begin_apply": "applying", "supersede": "superseded"},
    "applying": {"apply_succeeded": "applied", "apply_failed": "apply_failed"},
    "apply_failed": {"retry_apply": "applying", "supersede": "superseded"},
    "applied": {"revert": "reverted"},
    "reverted": {},
    "superseded": {},
}


class DecisionPackageError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 422):
        super().__init__(message)
        self.code, self.status_code = code, status_code


def stable_digest(payload) -> str:
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def package_digest(*, ontology_id, scenario_id, scenario_revision, run_id, title, rationale, actions, evidence) -> str:
    return stable_digest({"ontology_id": ontology_id, "scenario_id": scenario_id, "scenario_revision": scenario_revision,
                          "run_id": run_id, "title": title, "rationale": rationale, "actions": actions, "evidence": evidence})


def transition(package, event: str, actor_id: str, *, expected_etag: int, reason: str = "") -> tuple[str, str]:
    if package.etag != expected_etag:
        raise DecisionPackageError("stale_etag", "Decision package changed; reload before retrying", 409)
    target = TRANSITIONS.get(package.status, {}).get(event)
    if not target:
        raise DecisionPackageError("invalid_transition", f"Cannot {event} from {package.status}", 409)
    if event in {"approve", "reject"} and actor_id == package.created_by:
        raise DecisionPackageError("self_review_forbidden", "The package author cannot review their own package", 403)
    if event in {"reject", "apply_failed"} and not reason.strip():
        raise DecisionPackageError("reason_required", "A reason is required", 422)
    before = package.content_digest
    package.status = target
    package.etag += 1
    if event in {"approve", "reject"}:
        package.reviewed_by = actor_id
    return before, target


# --- Apply / revert orchestration -------------------------------------------------
#
# A package never writes live data itself: Apply stages the server-compiled
# edits as a Scenario revision and runs the existing Scenario merge, so conflict
# detection, graph compensation and audit are the same as for a manual merge.
# External effects (supplier notifications) go through an outbox after the
# merge commits, because they cannot share the database transaction.


class FakeProcurementConnector:
    """Local stand-in for an ERP/procurement API; idempotent by key.

    Deliberately not presented as a real integration.  ``fail_keys`` lets tests
    simulate a partial external failure.
    """
    name = "fake-erp"

    def __init__(self):
        self.fail_keys: set[str] = set()
        self.sent: dict[str, dict] = {}

    def send(self, key: str, payload: dict) -> dict:
        if key in self.fail_keys:
            raise ConnectionError("procurement endpoint unavailable")
        if key not in self.sent:
            self.sent[key] = {"connector": self.name, "external_id": f"ERP-{sha256(key.encode()).hexdigest()[:10]}",
                              "idempotency_key": key, "payload_digest": stable_digest(payload)}
        return self.sent[key]


procurement_connector = FakeProcurementConnector()


def _event(db, package, event_type, actor_id, request_id, *, before=None, reason=""):
    from app.models.v2.decision import DecisionPackageEvent
    sequence = db.query(DecisionPackageEvent).filter_by(package_id=package.id).count()
    db.add(DecisionPackageEvent(package_id=package.id, sequence=sequence, event_type=event_type, actor_id=actor_id,
                                reason=reason, request_id=request_id, before_digest=before, after_digest=package.content_digest))
    db.flush()


def find_candidate(db, run, candidate_id: str):
    """Return (node_key, artifact, candidate) for a candidate in a completed run."""
    from app.models.v2.scenario import ScenarioRunArtifact
    for node_key, node in sorted((run.result_manifest or {}).get("nodes", {}).items()):
        for row in (node.get("outputs") or {}).get("candidate_actions") or []:
            if row.get("id") == candidate_id:
                artifact = db.query(ScenarioRunArtifact).filter_by(run_id=run.id, artifact_type=f"node:{node_key}").first()
                if not artifact:
                    raise DecisionPackageError("artifact_incomplete", "The run has no artifact for this candidate", 409)
                return node_key, artifact, row
    raise DecisionPackageError("candidate_not_found", "Candidate is not part of this run", 404)


def pinned_snapshot(db, run, ontology_id):
    from app.models.v2.query_view import QueryDataView
    from app.models.v2.scenario_study import RunPlan
    from app.services.v2.graph.falkordb_service import FalkorDBService
    from app.services.v2.object_query.core import FalkorReadAdapter
    plan = db.get(RunPlan, run.run_plan_id) if run.run_plan_id else None
    view = db.get(QueryDataView, plan.data_view_id) if plan and plan.data_view_id else None
    if not view or view.status != "ready":
        raise DecisionPackageError("view_unavailable", "The run's pinned data view is unavailable", 409)
    graph = FalkorDBService()
    if not graph.available:
        raise DecisionPackageError("graph_unavailable", "FalkorDB is unavailable", 503)
    return FalkorReadAdapter(graph._graph(view.graph_key), graph_ontology_id=view.graph_key).read(ontology_id)


def compile_package_actions(db, run, ontology_id, candidate_id, actor):
    """Compile a run candidate into package actions; returns (actions, evidence, title).

    ``decision/v1`` results (any user Function) compile through the
    ontology's configured scenario Actions.  The built-in material-shortage
    example keeps its own compiler until it is rebuilt from user artifacts.
    """
    from app.models.v2.scenario import ScenarioRunArtifact
    from app.services.v2.decision_output import DecisionOutputError, compile_decision_candidate, find_decision_candidate, validate_decision
    found = find_decision_candidate(run, candidate_id)
    if found:
        node_key, port, decision, candidate = found
        artifact = db.query(ScenarioRunArtifact).filter_by(run_id=run.id, artifact_type=f"node:{node_key}").first()
        if not artifact:
            raise DecisionPackageError("artifact_incomplete", "The run has no artifact for this candidate", 409)
        try:
            validate_decision(decision)
            actions = compile_decision_candidate(db, ontology_id, run.scenario_id, pinned_snapshot(db, run, ontology_id), candidate, actor)
        except DecisionOutputError as exc:
            raise DecisionPackageError(exc.code, str(exc), 422) from exc
        except Exception as exc:  # contract validation of the stored result
            if getattr(exc, "errors", None) is not None:
                raise DecisionPackageError("invalid_decision_output", str(exc), 422) from exc
            raise
        evidence = [{"evidence_type": "artifact", "content_digest": artifact.content_digest.removeprefix("sha256:"),
                     "reference": {"artifact_id": artifact.id, "node_key": node_key, "port": port, "candidate_id": candidate["id"]}},
                    {"evidence_type": "metric", "content_digest": stable_digest(candidate),
                     "reference": {"candidate_id": candidate["id"], "metric": decision["metric"]["key"],
                                   "before": decision["metric"]["baseline"], "after": candidate["metric_after"],
                                   "cost": candidate.get("cost", 0), "harmed": candidate["harmed"]}}]
        return actions, evidence, candidate["label"]

    from app.services.v2.material_shortage_objects import CompileError, compile_candidate
    node_key, artifact, candidate = find_candidate(db, run, candidate_id)
    try:
        actions = compile_candidate(ontology_id, pinned_snapshot(db, run, ontology_id), candidate, run_id=run.id)
    except CompileError as exc:
        raise DecisionPackageError(exc.code, str(exc), 422) from exc
    outputs = run.result_manifest["nodes"][node_key]["outputs"]
    evidence = [{"evidence_type": "artifact", "content_digest": artifact.content_digest.removeprefix("sha256:"),
                 "reference": {"artifact_id": artifact.id, "node_key": node_key, "candidate_id": candidate["id"]}},
                {"evidence_type": "impact_path", "content_digest": outputs["impact_path"]["content_digest"],
                 "reference": {"artifact_id": artifact.id, "path": f"nodes.{node_key}.outputs.impact_path"}},
                {"evidence_type": "metric", "content_digest": candidate["content_digest"],
                 "reference": {"candidate_id": candidate["id"], "revenue_at_risk_before": outputs["revenue_at_risk"],
                               "revenue_at_risk_after": candidate["revenue_at_risk"], "incremental_cost": candidate["incremental_cost"],
                               "harmed_orders": [row["demand_id"] for row in candidate["harmed_orders"]]}}]
    return actions, evidence, candidate["label"]


def package_candidate_id(db, package) -> str:
    from app.models.v2.decision import DecisionPackageEvidence
    for row in db.query(DecisionPackageEvidence).filter_by(package_id=package.id).all():
        if row.reference.get("candidate_id"):
            return row.reference["candidate_id"]
    raise DecisionPackageError("artifact_incomplete", "Package does not reference a run candidate", 409)


def apply_package(db, package, user, *, expected_etag: int, request_id: str, connector=None) -> dict:
    """Stage, merge and record one approved package; returns the outbox delivery summary."""
    from app.models.v2.decision import DecisionOutbox, DecisionPackageAction, DecisionPackageEvent, DecisionRecord
    from app.models.v2.scenario import ScenarioChangeSet, ScenarioResource, ScenarioRun
    from app.routers.v2.scenario_workbench import merge_head
    from app.schemas.v2.scenario import Edit, RevisionRequest
    from app.services.v2.scenarios import ScenarioError, ScenarioService

    if db.query(DecisionPackageEvent).filter_by(package_id=package.id, request_id=request_id).first():
        return deliver_outbox(db, package, connector)
    if package.status not in {"approved", "apply_failed"}:
        raise DecisionPackageError("invalid_transition", f"Cannot apply from {package.status}", 409)
    if package.etag != expected_etag:
        raise DecisionPackageError("stale_etag", "Decision package changed; reload before retrying", 409)
    scenario = db.get(ScenarioResource, package.scenario_id)
    staged_id = f"package:{package.id}"
    # A failed earlier attempt may already have staged our revision on top of the reviewed one.
    staged = scenario is not None and scenario.head_revision == package.scenario_revision + 1 and \
        db.query(ScenarioChangeSet).filter_by(scenario_id=scenario.id, client_request_id=staged_id).first() is not None
    if not scenario or (scenario.head_revision != package.scenario_revision and not staged):
        raise DecisionPackageError("stale_scenario_revision", "Scenario revision changed after review", 409)

    run = db.get(ScenarioRun, package.run_id)
    candidate_id = package_candidate_id(db, package)
    actions, _, _ = compile_package_actions(db, run, package.ontology_id, candidate_id, user)
    if stable_digest([row["expected_edits"] for row in actions]) != package.preview_digest:
        raise DecisionPackageError("preview_digest_mismatch", "Recompiled edits differ from the reviewed preview", 409)

    before_digest = package.content_digest
    transition(package, "begin_apply" if package.status == "approved" else "retry_apply", user.id, expected_etag=expected_etag)
    _event(db, package, "begin_apply", user.id, f"{request_id}:begin", before=before_digest)
    db.commit()

    service = ScenarioService(db, package.ontology_id, user)
    edits = [Edit.model_validate(edit) for row in actions for edit in row["expected_edits"]]
    try:
        if not staged:
            service.revision(scenario, RevisionRequest(base_revision=scenario.head_revision, expected_etag=scenario.etag,
                                                       client_request_id=staged_id, edits=edits), replace_all=True)
        stored = db.query(DecisionPackageAction).filter_by(package_id=package.id).order_by(DecisionPackageAction.sequence).all()

        def record(before, applied):
            decision = DecisionRecord(ontology_id=package.ontology_id, payload={
                "kind": "decision_package_applied", "package_id": package.id, "run_id": package.run_id,
                "candidate_id": candidate_id, "title": package.title, "rationale": package.rationale,
                "scenario_id": scenario.id, "merged_revision": scenario.head_revision, "applied_by": user.id,
                "edits": applied, "before": before})
            db.add(decision)
            db.flush()
            package.applied_decision_id = decision.id
            for row in stored:
                if row.external_effect_class != "none":
                    db.add(DecisionOutbox(package_id=package.id, action_id=row.id,
                                          payload={"action_type": row.action_type_id, "invocation": row.invocation}))
            transition(package, "apply_succeeded", user.id, expected_etag=package.etag)
            _event(db, package, "apply_succeeded", user.id, request_id)

        merge_head(db, package.ontology_id, service, scenario, user, summary_extra={"decision_package_id": package.id}, before_commit=record)
    except (ScenarioError, DecisionPackageError) as exc:
        db.rollback()
        package = db.get(type(package), package.id)
        code = getattr(exc, "code", "merge_conflict")
        message = getattr(exc, "message", None) or str(exc)
        transition(package, "apply_failed", user.id, expected_etag=package.etag, reason=f"{code}: {message}")
        _event(db, package, "apply_failed", user.id, request_id, reason=f"{code}: {message}")
        db.commit()
        raise DecisionPackageError(code, message, 409) from exc
    return deliver_outbox(db, package, connector)


def deliver_outbox(db, package, connector=None) -> dict:
    """Send pending external effects; safe to call repeatedly."""
    from app.models.v2.decision import DecisionOutbox, DecisionPackageAction
    connector = connector or procurement_connector
    pending = db.query(DecisionOutbox).filter_by(package_id=package.id, delivered=False).order_by(DecisionOutbox.created_at, DecisionOutbox.id).all()
    failed = []
    for row in pending:
        row.attempts += 1
        try:
            receipt = connector.send(row.id, row.payload)
        except Exception as exc:  # recorded for retry; never reported as delivered
            failed.append({"outbox_id": row.id, "error": type(exc).__name__})
            db.commit()
            continue
        row.delivered = True
        db.get(DecisionPackageAction, row.action_id).receipt = receipt
        db.commit()
    total = db.query(DecisionOutbox).filter_by(package_id=package.id).count()
    delivered = db.query(DecisionOutbox).filter_by(package_id=package.id, delivered=True).count()
    return {"external_effects": total, "delivered": delivered, "failed": failed,
            "status": "complete" if delivered == total else "external_effect_partial"}


def revert_package(db, package, user, *, expected_etag: int, request_id: str) -> dict:
    """Undo the merged Ontology edits and list external effects that cannot be undone."""
    from app.models.v2.decision import DecisionPackageEvent, DecisionRecord
    from app.models.v2.scenario import ScenarioAudit
    from app.schemas.v2.scenario import Edit
    from app.services.v2.graph.falkordb_service import FalkorDBService
    from app.services.v2.live_edits import apply_live_edits
    from app.services.v2.scenarios import ScenarioError

    if db.query(DecisionPackageEvent).filter_by(package_id=package.id, request_id=request_id).first():
        return irreversible_effects(db, package)
    if package.etag != expected_etag:
        raise DecisionPackageError("stale_etag", "Decision package changed; reload before retrying", 409)
    if package.status != "applied" or not package.applied_decision_id:
        raise DecisionPackageError("not_revertible", f"Cannot revert from {package.status}", 409)
    record = db.get(DecisionRecord, package.applied_decision_id)
    before = record.payload.get("before") or {}
    inverse = []
    for edit in reversed(record.payload.get("edits") or []):
        target = edit.get("target") or {}
        if edit["op"] == "create_object":
            inverse.append({"op": "delete_object", "target": target})
        elif edit["op"] == "set_property":
            prior = before.get(target.get("object_id"), {})
            inverse.append({"op": "set_property" if edit["property"] in prior else "unset_property", "target": target,
                            "property": edit["property"], "value": prior.get(edit["property"]), "expected_old_value": edit.get("value")})
        else:
            raise DecisionPackageError("not_revertible", f"{edit['op']} edits cannot be reverted automatically", 422)
    typed = [Edit.model_validate({**row, "sequence": index}) for index, row in enumerate(inverse)]
    try:
        _, _, _, restore = apply_live_edits(db, package.ontology_id, typed, FalkorDBService())
    except ScenarioError as exc:
        db.rollback()
        raise DecisionPackageError("revert_conflict", exc.message, 409) from exc
    try:
        before_digest = package.content_digest
        transition(package, "revert", user.id, expected_etag=expected_etag)
        _event(db, package, "revert", user.id, request_id, before=before_digest)
        db.add(ScenarioAudit(scenario_id=package.scenario_id, revision=record.payload.get("merged_revision", 0), actor_id=user.id,
                             operation="revert", summary={"decision_package_id": package.id, "reverted_edits": len(typed)}))
        db.commit()
    except Exception:
        db.rollback()
        restore()
        raise
    return irreversible_effects(db, package)


def irreversible_effects(db, package) -> dict:
    from app.models.v2.decision import DecisionOutbox, DecisionPackageAction
    rows = db.query(DecisionOutbox).filter_by(package_id=package.id).all()
    return {"status": package.status, "irreversible_effects": [
        {"action_type": row.payload.get("action_type"), "delivered": row.delivered,
         "receipt": db.get(DecisionPackageAction, row.action_id).receipt} for row in rows]}
