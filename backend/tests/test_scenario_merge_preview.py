from types import SimpleNamespace

from app.models.v2.scenario import ScenarioAudit, ScenarioChangeSet, ScenarioRevision
from app.models.entity_instance import EntityInstance
from app.routers.v2 import scenario_workbench as workbench


class Query:
    def __init__(self, row):
        self.row = row

    def filter_by(self, **_kwargs):
        return self

    def first(self):
        return self.row


class Database:
    def __init__(self, revision, changeset):
        self.revision = revision
        self.changeset = changeset

    def query(self, model):
        return Query(self.revision if model is ScenarioRevision else
                     SimpleNamespace(id="T1") if model is EntityInstance else None)

    def get(self, model, _id):
        return self.changeset if model is ScenarioChangeSet else None


def test_merge_preview_checks_live_values_and_sequential_action_edits(monkeypatch):
    edits = [
        {"op": "set_property", "target": {"concrete_type": "Ticket", "object_id": "T1"},
         "property": "priority", "expected_old_value": "P2", "value": "P1"},
        {"op": "set_property", "target": {"concrete_type": "Ticket", "object_id": "T1"},
         "property": "priority", "expected_old_value": "P1", "value": "P0"},
    ]
    revision = SimpleNamespace(revision=1, changeset_id="change")
    changeset = SimpleNamespace(ordered_edits=edits, validation_report={"replace_all": True})
    db = Database(revision, changeset)
    scenario = SimpleNamespace(id="scenario", ontology_id="ontology", base_view_id="base", head_revision=1)
    service = SimpleNamespace(db=db, graph=SimpleNamespace(_graph=lambda _id: object()))
    monkeypatch.setattr(workbench, "_snapshot", lambda *_args: SimpleNamespace(
        objects={("Ticket", "T1"): {"priority": "P2"}}, edges=[]))

    live = SimpleNamespace(objects={("Ticket", "T1"): {"priority": "P2"}}, edges=[])
    monkeypatch.setattr(workbench, "FalkorReadAdapter", lambda _graph: SimpleNamespace(read=lambda _id: live))
    result, selected = workbench._merge_state(service, scenario)
    assert result["can_merge"] is True
    assert result["applied_edits"] == 2
    assert selected == edits

    live.objects[("Ticket", "T1")]["priority"] = "P3"
    result, _ = workbench._merge_state(service, scenario)
    assert result["can_merge"] is False
    assert result["conflicts"][0]["reason"] == "Main Ontology value changed since this Scenario was created"
