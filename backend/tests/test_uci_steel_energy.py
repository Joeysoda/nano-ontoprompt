from decimal import Decimal
from math import isclose
from pathlib import Path

from app.services.v2.logic_assets import EXECUTORS
from app.services.v2.uci_steel_energy import load_energy_profile, tariff_sensitivity


DATASET = Path(__file__).parent / "data" / "Steel_industry_data.csv"


def test_uci_steel_energy_known_answers_and_tariff_only_changes_cost():
    profile = load_energy_profile(DATASET)
    assert profile.row_count == 35_040
    assert profile.first_timestamp.isoformat() == "2018-01-01T00:00:00"
    assert profile.last_timestamp.isoformat() == "2018-12-31T23:45:00"
    assert profile.category_counts == {
        "Light_Load": 18_072,
        "Medium_Load": 9_696,
        "Maximum_Load": 7_272,
    }
    assert sum(profile.usage_kwh_by_category.values()) == Decimal("959636.71")

    result = tariff_sensitivity(
        profile,
        {"Light_Load": "0.10", "Medium_Load": "0.15", "Maximum_Load": "0.22"},
        maximum_load_rate_change="0.25",
    )
    assert result["baseline_cost"] == "166319.2812"
    assert result["candidate_cost"] == "190023.0360"
    assert result["cost_delta"] == "23703.7548"
    assert result["usage_kwh_before"] == result["usage_kwh_after"] == "959636.71"
    assert result["rates_are_illustrative"] is True
    assert result["dataset_sha256"] == profile.dataset_sha256


def test_uci_steel_energy_rejects_unreviewed_checksum():
    try:
        load_energy_profile(b"changed")
    except ValueError as exc:
        assert "checksum" in str(exc)
    else:
        raise AssertionError("Unreviewed dataset bytes must not be accepted")


def test_registered_logic_asset_uses_reviewed_dataset_and_preserves_physical_usage():
    result = EXECUTORS["uci_steel_energy_tariff_v1"]({
        "rates_per_kwh": {"Light_Load": 0.10, "Medium_Load": 0.15, "Maximum_Load": 0.22},
        "maximum_load_rate_change": 0.25,
    })
    assert isclose(result["cost_delta"], 23_703.7548, abs_tol=1e-8)
    assert result["usage_kwh_before"] == result["usage_kwh_after"] == 959_636.71
    assert result["usage_delta_kwh"] == 0
    assert result["dataset_sha256"] == "9b1cee6f9cb9cd9df2b95814ca90a9a2ff15b7f5f1fba0fae3c643e82072eacc"


def test_capacity_template_schedule_respects_parallel_resources_deadlines_and_capacity():
    run = EXECUTORS["capacity_replan_v1"]({
        "tasks": [
            {"id": "OP-10", "duration": 45, "due_at": 50, "due_date": "2026-11-01", "resource": "line-1"},
            {"id": "OP-20", "duration": 75, "due_at": 110, "due_date": "2026-11-02", "resource": "line-1"},
            {"id": "OP-30", "duration": 60, "due_at": 65, "due_date": "2026-11-03", "resource": "line-2"},
        ],
        "capacity": 240,
    })
    assert run["makespan"] == 120
    assert run["feasible"] is True
    assert run["late_tasks"] == ["OP-20"]
    assert run["schedule"][2]["start"] == 0
    assert run["schedule"][2]["end"] == 60
    constrained = EXECUTORS["capacity_replan_v1"]({
        "tasks": [{"id": "OP-10", "duration": 45, "resource": "line-1"}],
        "capacity": 30,
    })
    assert constrained["feasible"] is False
