from types import SimpleNamespace

from app.routers.v2.construction_drafts import _deterministic_mapping
from app.services.v2.ontology_materializer import mapping_suggestions, normalise_mapping


def test_private_cmapss_mapping_uses_real_equipment_reading_contract():
    draft = SimpleNamespace(
        data_class="regular",
        selection_json={
            "fields": ["equipment_id", "equipment_type", "reading_id", "cycle", "sensor_1"],
        },
    )
    mapping = _deterministic_mapping(
        draft,
        descriptor={"dataset": {"name": "NASA C-MAPSS FD001（100 条读数）"}},
    )

    validated = normalise_mapping(mapping, data_class="regular")
    assert validated.errors == []
    assert {item["id"] for item in validated.mapping["entity_types"]} == {"equipment", "sensor_reading"}
    assert validated.mapping["relationships"][0]["from"] == "equipment"
    assert validated.mapping["relationships"][0]["to"] == "sensor_reading"
    assert validated.mapping["logic_rules"][0]["condition"]["time_kind"] == "Ordinal"


def test_private_mapping_cards_keep_rule_provenance():
    mapping = {
        "entity_types": [{"id": "equipment", "name": "Equipment", "confidence": 1.0}],
        "properties": [],
        "relationships": [],
        "logic_rules": [],
    }
    suggestions = mapping_suggestions(mapping, extractor="规则处理")
    assert suggestions[0]["extractor"] == "规则处理"


def test_private_multimodal_mapping_keeps_sample_asset_and_official_label_contract():
    draft = SimpleNamespace(
        data_class="multimodal",
        selection_json={"sample_ids": ["sample-1"], "selected_assets": ["rgb", "depth", "point_cloud"]},
    )
    mapping = _deterministic_mapping(
        draft,
        descriptor={
            "dataset": {"name": "I-BADAS 工业 RGB-D 多模态样例"},
            "multimodal_catalog": [{"modalities": ["rgb", "depth", "point_cloud"]}],
        },
    )

    validated = normalise_mapping(mapping, data_class="multimodal")
    assert validated.errors == []
    assert {item["id"] for item in validated.mapping["entity_types"]} == {
        "scene", "multimodalsample", "mediaasset", "anomalyevent",
    }
    assert {(item["from"], item["to"]) for item in validated.mapping["relationships"]} >= {
        ("multimodalsample", "mediaasset"),
        ("multimodalsample", "anomalyevent"),
    }
    assert validated.mapping["logic_rules"][0]["linked_entities"] == ["multimodalsample", "anomalyevent"]
