from unittest.mock import patch

from app.models.entity import Entity
from app.models.entity_instance import EntityInstance
from app.models.ontology import OntologyProject
from app.models.relation import Relation
from app.routers.v2 import graph as graph_router


class FakeFalkor:
    available = True

    def __init__(self, temporal=False):
        self.temporal = temporal
        self.calls = []

    def temporal_timeline(self, ontology_id, **kwargs):
        return {
            "available": True,
            "time_kind": "ordinal",
            "dates": ["0", "1", "2"],
            "buckets": [{"timestamp": "0", "count": 1}, {"timestamp": "1", "count": 1}, {"timestamp": "2", "count": 1}],
            "episodes": ["episode-1"],
        }

    def get_graph_data(self, ontology_id, **kwargs):
        self.calls.append(kwargs)
        nodes = [
            {"id": "equipment:1", "entity_type": "Equipment", "labels": ["Instance"], "properties": {"equipment_id": "EQ001"}, "event_seq": 0},
            {"id": "reading:1", "entity_type": "SensorReading", "labels": ["Instance"], "properties": {"reading_id": "R-1"}, "event_seq": 1},
        ]
        return {
            "nodes": nodes,
            "edges": [{"id": "e1", "source": "equipment:1", "target": "reading:1", "type": "HAS_READING", "properties": {}}],
            "total_instances": 2,
            "total_edges": 1,
            "returned": len(nodes),
            "offset": kwargs.get("offset", 0),
            "next_offset": None,
            "available": True,
            "graph_backend": "falkordb",
        }

    def get_instance_type_counts(self, ontology_id, **kwargs):
        return {"Equipment": 1, "SensorReading": 1}


def _ontology(db, data_class="regular"):
    ontology = OntologyProject(id="dm-ontology", name="数据模型测试", domain="test", data_class=data_class, created_by="user")
    equipment = Entity(id="entity-equipment", ontology_id=ontology.id, name_cn="设备", name_en="Equipment", properties={"property_definitions": [{"name": "equipment_id"}]})
    reading = Entity(id="entity-reading", ontology_id=ontology.id, name_cn="读数", name_en="SensorReading", properties={"property_definitions": [{"name": "reading_id"}]})
    db.add_all([ontology, equipment, reading])
    db.flush()
    db.add(Relation(id="relation-reading", ontology_id=ontology.id, source_entity=equipment.id, target_entity=reading.id, type="HAS_READING", properties={}))
    db.add(EntityInstance(id="sql-instance", entity_id=equipment.id, ontology_id=ontology.id, row_identity="EQ001", row_data={"equipment_id": "EQ001"}))
    db.commit()
    return ontology


def test_regular_data_model_is_paginated_and_keeps_type_groups(db):
    _ontology(db, "regular")
    fake = FakeFalkor()
    with patch.object(graph_router, "get_falkordb", return_value=fake):
        result = graph_router.get_data_model("dm-ontology", limit=2, offset=0, db=db)
    assert result["data_class"] == "regular"
    assert result["pagination"]["limit"] == 2
    assert result["pagination"]["total"] == 2
    assert {group["filter"] for group in result["type_groups"]} == {"Equipment", "SensorReading"}
    assert result["time"] is None


def test_temporal_data_model_uses_latest_ordinal_and_episode_filter(db):
    _ontology(db, "temporal")
    fake = FakeFalkor(temporal=True)
    with patch.object(graph_router, "get_falkordb", return_value=fake):
        result = graph_router.get_data_model("dm-ontology", limit=2, episode_id="episode-1", db=db)
    assert result["data_class"] == "temporal"
    assert result["time"]["kind"] == "ordinal"
    assert result["time"]["current"] == "2"
    assert "at" not in result["time"]["current"]
    assert fake.calls[-1]["episode_id"] == "episode-1"
    assert fake.calls[-1]["seq_to"] == 2
