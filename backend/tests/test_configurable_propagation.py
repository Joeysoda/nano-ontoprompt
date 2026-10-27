import pytest
from app.services.v2.configurable_propagation import PropagationError, propagate
from app.services.v2.logic_assets import ASSET_DEFINITIONS, execute

def test_same_engine_supports_supply_and_quality_graphs_by_configuration():
    supply = propagate([{"id":"material"},{"id":"plant"},{"id":"order"}],
        [{"source":"material","target":"plant","factor":2},{"source":"plant","target":"order","factor":3,"delay":4}], [{"node":"material","value":5}])
    quality = propagate([{"id":"lot"},{"id":"shipment"}], [{"source":"lot","target":"shipment","factor":0.25}], [{"node":"lot","value":8}])
    assert supply["impacts"][-1] == {"node":"plant","value":10.0}
    assert {r["node"]:r["value"] for r in supply["impacts"]}["order"] == 30
    assert quality["impacts"][-1]["value"] == 2

def test_cycles_and_invalid_numbers_are_rejected():
    with pytest.raises(PropagationError, match="cycle"):
        propagate([{"id":"a"},{"id":"b"}], [{"source":"a","target":"b"},{"source":"b","target":"a"}], [{"node":"a","value":1}])


def test_propagation_is_available_through_the_versioned_logic_asset_contract():
    asset = next(row for row in ASSET_DEFINITIONS if row["asset_key"] == "configurable_propagation_v1")
    result = execute(asset, {
        "nodes": [{"id": "supplier"}, {"id": "plant"}, {"id": "order"}],
        "edges": [
            {"id": "supplies", "source": "supplier", "target": "plant", "factor": 0.5},
            {"id": "fulfills", "source": "plant", "target": "order", "factor": 4},
        ],
        "seeds": [{"node": "supplier", "value": 6}],
        "max_depth": 10,
        "minimum": 0,
    })
    assert result["version"] == "1.0.0"
    assert {row["node"]: row["value"] for row in result["output"]["impacts"]} == {
        "supplier": 6.0, "plant": 3.0, "order": 12.0,
    }
    assert [row["edge_id"] for row in result["output"]["paths"]] == ["supplies", "fulfills"]
