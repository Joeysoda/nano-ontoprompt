from types import SimpleNamespace

from app.models.entity import Entity
from app.models.entity_instance import EntityInstance
from app.models.ontology import OntologyProject
from app.models.v2.action import OntologyActionRun, OntologyActionType
from app.routers.v2 import logic_actions
from app.services.v2 import live_edits


class FakeGraph:
    def __init__(self):
        self.props = {"status": "approved"}

    def query(self, statement, params):
        if "SET n += $patch" in statement:
            self.props.update(params["patch"])


def test_revert_completed_property_action_uses_same_live_edit_adapter(db, admin_user, monkeypatch):
    ontology_id = "ont-revert"
    graph = FakeGraph()
    service = SimpleNamespace(available=True, _graph=lambda ontology: graph)
    adapter = lambda graph: SimpleNamespace(read=lambda ontology: SimpleNamespace(
        objects={("Order", "order-1"): {"status": graph.props["status"]}}, edges=[]))
    monkeypatch.setattr(logic_actions, "FalkorDBService", lambda: service)
    monkeypatch.setattr(logic_actions, "FalkorReadAdapter", adapter)
    monkeypatch.setattr(live_edits, "FalkorReadAdapter", adapter)
    db.add(OntologyProject(id=ontology_id, name="Test", domain="Test", created_by=admin_user.id))
    db.add(Entity(id="order-type", ontology_id=ontology_id, name_cn="Order", name_en="Order", type="EntityType", properties={}))
    db.add(EntityInstance(id="order-1", ontology_id=ontology_id, entity_id="order-type", row_identity="order-1", row_data={"status": "approved"}))
    db.add(OntologyActionType(id="approve", ontology_id=ontology_id, name="Approve", action_category="crud", effects=[], status="published", enabled=True))
    original = OntologyActionRun(id="run-1", ontology_id=ontology_id, action_type_id="approve",
                                 target_object_id="order-1", parameters={}, execution_context={"ontology_id": ontology_id, "consistency": "live"},
                                 status="completed", before_snapshot={"order-1": {"status": "pending"}},
                                 after_snapshot={"order-1": {"status": "approved"}},
                                 side_effect_results=[{"op": "set_property", "target_id": "order-1"}], executed_by=admin_user.id)
    db.add(original)
    db.commit()

    result = logic_actions.revert_action_run(ontology_id, original.id, db, admin_user)
    db.refresh(original)
    row = db.get(EntityInstance, "order-1")
    assert result["status"] == "completed"
    assert result["edits"] == 1
    assert original.status == "reverted"
    assert row.row_data["status"] == graph.props["status"] == "pending"
