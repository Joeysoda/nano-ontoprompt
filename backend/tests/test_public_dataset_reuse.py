"""One published domain Function reused against two independent public datasets.

This is a file-level integration check, not a database/browser claim.  The
only dataset-specific pieces are explicit row mappings; platform and Function
code are identical for both sources.
"""
from csv import DictReader
from pathlib import Path

import pytest

from app.services.v2.logic_assets import ASSET_DEFINITIONS, execute
from app.services.v2.row_mapping import map_rows


ROOT = Path(__file__).resolve().parents[2]
SUMMARY_ASSET = next(item for item in ASSET_DEFINITIONS if item["asset_key"] == "observation_summary_v1")


def object_rows(path: Path, *, object_type: str, id_field: str, numeric_field: str, limit: int | None = None):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        values = list(DictReader(handle))
    if limit is not None:
        values = values[:limit]
    for row in values:
        # CSV ingestion is responsible for producing typed ontology properties;
        # row_mapping deliberately does not guess or coerce source types.
        row[numeric_field] = float(row[numeric_field])
    return [
        {"ontology_id": "public-data-test", "object_type": object_type,
         "object_id": row[id_field], "properties": row}
        for row in values
    ]


@pytest.mark.parametrize(
    "path,object_type,id_field,source_field,limit,expected_count",
    [
        (ROOT / "backend/tests/data/Steel_industry_data.csv", "EnergyReading", "date", "Usage_kWh", 100, 100),
        (ROOT / "data/cmapss_fd001_demo/sensor_readings.csv", "EngineCycle", "reading_id", "sensor_2", None, 100),
    ],
)
def test_same_observation_model_runs_on_independent_public_datasets(
    path, object_type, id_field, source_field, limit, expected_count,
):
    source = object_rows(path, object_type=object_type, id_field=id_field, numeric_field=source_field, limit=limit)
    adapted = map_rows(
        {"fields": [{"target": "value", "source": ["properties", source_field],
                     "data_type": "number", "required": True, "nullable": False}]},
        source,
        max_rows=1000,
    )
    values = [row["value"] for row in adapted["rows"]]
    result = execute(SUMMARY_ASSET, {"values": values, "anomaly_threshold": 5.0})

    assert result["output"]["count"] == expected_count
    assert result["output"]["minimum"] == min(float(row["properties"][source_field]) for row in source)
    assert result["output"]["maximum"] == max(float(row["properties"][source_field]) for row in source)
    assert [item["source"]["object_id"] for item in adapted["lineage"]] == [row["object_id"] for row in source]
