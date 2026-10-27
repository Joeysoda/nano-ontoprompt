import pytest

from app.services.v2.logic_assets import ASSET_DEFINITIONS, execute
from app.services.v2.process_analysis import ProcessAnalysisError, analyze


def test_same_process_analysis_supports_maintenance_and_quality_logs():
    maintenance = analyze([
        {"event_id": "1", "case_id": "wo-1", "activity": "opened", "timestamp": 0},
        {"event_id": "2", "case_id": "wo-1", "activity": "repair", "timestamp": 5},
        {"event_id": "3", "case_id": "wo-1", "activity": "closed", "timestamp": 9},
    ])
    quality = analyze([
        {"event_id": "q1", "case_id": "lot-1", "activity": "inspect", "timestamp": "2026-01-01T00:00:00Z"},
        {"event_id": "q2", "case_id": "lot-1", "activity": "rework", "timestamp": "2026-01-01T00:01:00Z"},
        {"event_id": "q3", "case_id": "lot-1", "activity": "inspect", "timestamp": "2026-01-01T00:02:00Z"},
    ])
    assert maintenance["average_case_duration"] == 9
    assert quality["rework_case_count"] == 1
    assert quality["transitions"][0]["count"] == 1


def test_process_analysis_executes_through_the_logic_asset_contract():
    asset = next(row for row in ASSET_DEFINITIONS if row["asset_key"] == "process_analysis_v1")
    output = execute(asset, {"events": [
        {"event_id": "1", "case_id": "c", "activity": "a", "timestamp": 1},
        {"event_id": "2", "case_id": "c", "activity": "b", "timestamp": 3},
    ]})["output"]
    assert output["case_count"] == 1 and output["activity_metrics"][0]["average_dwell"] == 2


def test_process_analysis_rejects_ambiguous_events():
    with pytest.raises(ProcessAnalysisError, match="unique"):
        analyze([{"event_id": "same", "case_id": "a", "activity": "x", "timestamp": 1},
                 {"event_id": "same", "case_id": "b", "activity": "y", "timestamp": 2}])
