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
