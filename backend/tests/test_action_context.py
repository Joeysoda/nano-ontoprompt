from types import SimpleNamespace

import pytest

from app.schemas.v2.object_query import ExecutionContext
from app.services.v2.action_context import ActionContextError, resolve_action_context


def test_missing_context_is_explicitly_live():
    resolved = resolve_action_context(None, "ontology-1", None, SimpleNamespace(id="u1", role="editor"))
    assert resolved.target == "live"
    assert resolved.context.ontology_id == "ontology-1"
    assert resolved.context.consistency == "live"


def test_snapshot_context_cannot_silently_write_live():
    with pytest.raises(ActionContextError) as caught:
        resolve_action_context(None, 'ontology-1', ExecutionContext(ontology_id='ontology-1', consistency='snapshot', data_view_id='view'), SimpleNamespace(id='u1'))
    assert caught.value.code == 'immutable_context'


def test_context_cannot_cross_ontology():
    with pytest.raises(ActionContextError, match="differs from route ontology") as caught:
        resolve_action_context(
            None,
            "ontology-1",
            ExecutionContext(ontology_id="ontology-2"),
            SimpleNamespace(id="u1", role="editor"),
        )
    assert caught.value.code == "ontology_context_mismatch"
    assert caught.value.path == "context.ontology_id"


def test_scenario_revision_requires_scenario_id():
    with pytest.raises(ActionContextError) as caught:
        resolve_action_context(
            None,
            "ontology-1",
            ExecutionContext(ontology_id="ontology-1", scenario_revision=2),
            SimpleNamespace(id="u1", role="editor"),
        )
    assert caught.value.code == "invalid_scenario_context"


def test_action_catalog_rejects_missing_ontology_over_http(client, auth_headers):
    response = client.get("/api/v2/ontologies/missing/actions", headers=auth_headers)
    assert response.status_code == 404
