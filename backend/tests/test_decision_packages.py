from types import SimpleNamespace
import pytest
from app.services.v2.decision_packages import DecisionPackageError, execution_plan_digest, package_digest, transition


def package(**changes):
    values = dict(status="draft", etag=1, created_by="author", reviewed_by=None, content_digest="a" * 64)
    values.update(changes)
    return SimpleNamespace(**values)


def test_digest_is_mapping_order_independent_and_list_order_sensitive():
    args = dict(ontology_id="o", scenario_id="s", scenario_revision=2, run_id="r", title="t", rationale="why",
                actions=[{"sequence": 0, "parameters": {"b": 2, "a": 1}}], evidence=[])
    first = package_digest(**args)
    args["actions"][0]["parameters"] = {"a": 1, "b": 2}
    assert package_digest(**args) == first
    args["actions"].append({"sequence": 1})
    assert package_digest(**args) != first


def test_review_requires_another_actor_and_etag():
    item = package(status="in_review")
    with pytest.raises(DecisionPackageError, match="author cannot review") as error:
        transition(item, "approve", "author", expected_etag=1)
    assert error.value.code == "self_review_forbidden"
    with pytest.raises(DecisionPackageError) as error:
        transition(item, "approve", "reviewer", expected_etag=9)
    assert error.value.code == "stale_etag"
    _, target = transition(item, "approve", "reviewer", expected_etag=1)
    assert (target, item.status, item.etag, item.reviewed_by) == ("approved", "approved", 2, "reviewer")


def test_apply_failure_requires_reason_and_can_retry_then_revert():
    item = package(status="approved")
    transition(item, "begin_apply", "operator", expected_etag=1)
    with pytest.raises(DecisionPackageError) as error:
        transition(item, "apply_failed", "operator", expected_etag=2)
    assert error.value.code == "reason_required"
    transition(item, "apply_failed", "operator", expected_etag=2, reason="ERP timeout")
    transition(item, "retry_apply", "operator", expected_etag=3)
    transition(item, "apply_succeeded", "operator", expected_etag=4)
    transition(item, "revert", "operator", expected_etag=5)
    assert item.status == "reverted"


def test_reviewed_execution_plan_freezes_external_delivery_routing():
    action = {"action_type_id": "release_order", "invocation": {"parameters": {}, "_external_delivery": {
        "connection_id": "rest-1", "resource": "/orders", "kind": "erp_writeback"}},
        "expected_edits": [{"op": "set_property"}], "external_effect_class": "irreversible"}
    reviewed = execution_plan_digest([action])
    action["invocation"]["_external_delivery"]["resource"] = "/different"
    assert execution_plan_digest([action]) != reviewed
