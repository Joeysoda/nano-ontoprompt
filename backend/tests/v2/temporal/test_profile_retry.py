from unittest.mock import patch

import pytest

from app.models.v2.dataset import Dataset, DatasetVersion
from app.models.v2.temporal_profile import TemporalDatasetProfile
from app.routers.v2.temporal import (
    TemporalAnalysisRequest,
    create_temporal_analysis,
)


def _profile(db, *, status: str, llm_used: bool):
    dataset = Dataset(
        id="temporal-retry-dataset",
        name="FactoryNet retry fixture",
        kind="structured",
        data_class="temporal",
        privacy_level="standard",
    )
    version = DatasetVersion(
        id="temporal-retry-version",
        dataset_id=dataset.id,
        version_no=1,
        storage_uri="s3://datasets/temporal-retry.csv",
    )
    dataset.latest_version_id = version.id
    profile = TemporalDatasetProfile(
        id="temporal-retry-profile",
        dataset_id=dataset.id,
        dataset_version_id=version.id,
        status=status,
        deterministic_profile={"row_count": 10},
        llm_suggestion={"time_kind": "ordinal"},
        llm_used=llm_used,
        model_name="old-model",
        model_config_id="old-config",
        prompt_version="old-prompt",
        response_hash="old-response",
        error="old error",
    )
    db.add_all([dataset, version, profile])
    db.commit()
    return dataset.id, profile.id


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_standard_stale_or_failed_profile_is_requeued_for_m3(db, status):
    dataset_id, profile_id = _profile(db, status=status, llm_used=False)

    with patch("app.routers.v2.temporal._queue_temporal_profile") as queue:
        result = create_temporal_analysis(
            f"dataset:{dataset_id}",
            TemporalAnalysisRequest(privacy_level="standard"),
            None,
            db,
        )

    assert result["id"] == profile_id
    assert result["status"] == "queued"
    assert result["llm_used"] is False
    assert result["error"] is None
    assert result["llm_suggestion"] == {}
    assert result["model_name"] is None
    assert result["response_hash"] is None
    queue.assert_called_once_with(profile_id, None)


def test_standard_successful_m3_profile_is_reused_without_duplicate_task(db):
    dataset_id, profile_id = _profile(db, status="completed", llm_used=True)

    with patch("app.routers.v2.temporal._queue_temporal_profile") as queue:
        result = create_temporal_analysis(
            f"dataset:{dataset_id}",
            TemporalAnalysisRequest(privacy_level="standard"),
            None,
            db,
        )

    assert result["id"] == profile_id
    assert result["status"] == "completed"
    assert result["llm_used"] is True
    assert result["model_name"] == "old-model"
    queue.assert_not_called()
