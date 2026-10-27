import pytest

from app.services.v2.linear_optimization import OptimizationError, optimize
from app.services.v2.logic_assets import ASSET_DEFINITIONS, execute


def model(profit_a=5, profit_b=2):
    return {
        "variables": [
            {"id": "a", "kind": "integer", "lower": 0, "upper": 10, "objective": profit_a},
            {"id": "b", "kind": "integer", "lower": 0, "upper": 10, "objective": profit_b},
        ],
        "constraints": [
            {"id": "capacity", "terms": {"a": 2, "b": 1}, "sense": "<=", "rhs": 8},
        ],
        "objective_sense": "max",
        "time_limit_seconds": 5,
    }


def test_generic_optimizer_changes_only_configuration_for_another_dataset():
    first = optimize(**model())
    second = optimize(**model(profit_a=1, profit_b=5))
    if first["status"] == "unavailable":
        pytest.skip("optional SciPy/HiGHS dependency is not installed")
    assert first["status"] == second["status"] == "optimal"
    assert {row["variable"]: round(row["value"]) for row in first["values"]} == {"a": 4, "b": 0}
    assert {row["variable"]: round(row["value"]) for row in second["values"]} == {"a": 0, "b": 8}


def test_optimizer_runs_through_versioned_logic_asset_contract():
    asset = next(row for row in ASSET_DEFINITIONS if row["asset_key"] == "linear_optimization_v1")
    result = execute(asset, model())["output"]
    if result["status"] == "unavailable":
        pytest.skip("optional SciPy/HiGHS dependency is not installed")
    assert result["status"] == "optimal"
    assert result["objective"] == pytest.approx(20)
    assert result["solver"] == "highs-via-scipy-milp"


def test_optimizer_rejects_implicit_or_unbounded_configuration():
    with pytest.raises(OptimizationError, match="unknown variable"):
        optimize([{"id": "x"}], [{"terms": {"missing": 1}, "sense": "<=", "rhs": 1}])
    with pytest.raises(OptimizationError, match="objective_sense"):
        optimize([{"id": "x"}], [], objective_sense="largest")
