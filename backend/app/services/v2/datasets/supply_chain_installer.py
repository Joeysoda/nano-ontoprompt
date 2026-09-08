"""NASA C-MAPSS FD001 subset used by the regular-data workbench.

The module keeps its historical filename for import compatibility.  The old
invented supply-chain fixture is not deleted from a user's database; it is
marked technical and removed from the normal source directory instead.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models.v2.dataset import Dataset
from app.services.v2.dataset_service import DatasetService


CMAPSS_FD001_SOURCE_ID = "cmapss_fd001_regular_subset"
CMAPSS_FD001_NAME = "NASA C-MAPSS FD001（100 条读数）"
NASA_CMAPSS_URL = "https://phm-datasets.s3.amazonaws.com/NASA/6.+Turbofan+Engine+Degradation+Simulation+Data+Set.zip"
NASA_CMAPSS_MEMBER = "train_FD001.txt"


def _fixture_paths() -> list[Path]:
    configured = os.environ.get("CMAPSS_FD001_DEMO_DIR")
    values = [Path(configured)] if configured else []
    # The local compose override mounts the teacher-provided, verified subset
    # here.  The package path is retained for a future bundled fixture.
    values.extend([Path("/teacher-cmapss"), Path(__file__).resolve().parents[4] / "data" / "cmapss_fd001_demo"])
    return values


def _load_rows() -> tuple[list[dict[str, str]], dict[str, Any]] | None:
    for root in _fixture_paths():
        equipment_path = root / "equipment.csv"
        readings_path = root / "sensor_readings.csv"
        manifest_path = root / "manifest.json"
        if not equipment_path.is_file() or not readings_path.is_file():
            continue
        with equipment_path.open("r", encoding="utf-8-sig", newline="") as stream:
            equipment = {row["equipment_id"]: row for row in csv.DictReader(stream)}
        with readings_path.open("r", encoding="utf-8-sig", newline="") as stream:
            readings = [dict(row) for row in csv.DictReader(stream)]
        if len(equipment) != 5 or len(readings) != 100:
            raise ValueError("C-MAPSS FD001 本地子集必须包含 5 台设备和 100 条读数")
        rows = [{**equipment.get(row.get("equipment_id", ""), {}), **row} for row in readings]
        manifest: dict[str, Any] = {}
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        raw_hash = hashlib.sha256(readings_path.read_bytes()).hexdigest()
        return rows, {
            "fixture_dir": str(root), "equipment_rows": len(equipment), "sensor_reading_rows": len(readings),
            "source_member": manifest.get("source_member", NASA_CMAPSS_MEMBER),
            "selection_algorithm": "前 5 台设备，每台前 20 个 cycle（按原始文件顺序）",
            "source_sha256": raw_hash,
        }
    return None


def _hide_legacy_supply_chain(db: Session) -> None:
    changed = False
    for dataset in db.query(Dataset).all():
        if (dataset.schema_json or {}).get("source_id") != "supply_chain_demo":
            continue
        schema = dict(dataset.schema_json or {})
        if not schema.get("technical"):
            schema["technical"] = True
            schema["hidden_reason"] = "已由 NASA C-MAPSS FD001 常规案例替代"
            dataset.schema_json = schema
            changed = True
    if changed:
        db.commit()


def ensure_cmapss_fd001_demo(db: Session) -> Dataset | None:
    """Install the verified local subset when its source files are available."""
    _hide_legacy_supply_chain(db)
    existing = next((item for item in db.query(Dataset).all() if (item.schema_json or {}).get("source_id") == CMAPSS_FD001_SOURCE_ID), None)
    if existing:
        changed = False
        if existing.data_class != "regular":
            existing.data_class = "regular"; changed = True
        if existing.name != CMAPSS_FD001_NAME:
            existing.name = CMAPSS_FD001_NAME; changed = True
        if changed: db.commit()
        return existing
    prepared = _load_rows()
    if not prepared:
        return None
    rows, provenance = prepared
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    checksum = hashlib.sha256(payload).hexdigest()
    service = DatasetService(db)
    dataset = service.create_dataset(
        CMAPSS_FD001_NAME,
        "structured",
        data_class="regular",
        privacy_level="standard",
        schema_json={
            "source_id": CMAPSS_FD001_SOURCE_ID,
            "source_url": NASA_CMAPSS_URL,
            "source_member": provenance["source_member"],
            "license": "NASA public dataset",
            "selection_algorithm": provenance["selection_algorithm"],
            "sha256": checksum,
            "source_sha256": provenance["source_sha256"],
            "tables": {"equipment": {"rows": provenance["equipment_rows"], "role": "主体维表"}, "sensor_readings": {"rows": provenance["sensor_reading_rows"], "role": "关联读数与统计证据"}},
            "rowcount": len(rows),
            "data_class": "regular",
        },
    )
    version = service.create_version(dataset.id, payload, rowcount=len(rows))
    version.source_path = f"{NASA_CMAPSS_URL}#{provenance['source_member']}"
    db.commit()
    return dataset


# Compatibility for older startup modules and tests.  It now creates the real
# C-MAPSS subset and never recreates the fictional supply-chain source.
def ensure_supply_chain_demo(db: Session) -> Dataset | None:
    return ensure_cmapss_fd001_demo(db)
