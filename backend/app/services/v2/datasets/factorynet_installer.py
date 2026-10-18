"""FactoryNet CNC temporal demo installer."""
from __future__ import annotations

import hashlib
import io
import json
import urllib.request
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.v2.dataset import Dataset, DatasetVersion
from app.services.storage_service import get_storage_service
from app.services.v2.dataset_service import DatasetService

FACTORYNET_SOURCE_ID = "factorynet_cnc"
FACTORYNET_DATASET_NAME = "FactoryNet CNC 铣削时序数据"
FACTORYNET_FILE = "cnc_000.parquet"
FACTORYNET_URL = "https://huggingface.co/datasets/factorynet/factorynet/resolve/main/data/cnc_000.parquet?download=true"
FACTORYNET_SHA256 = "4754f0389c31a13fc84ace615a77d63aa3483ed49b4ab503d70b4c68a4519e6d"
FACTORYNET_LICENSE = "CC BY-NC-SA 4.0"


def _parquet_summary(raw: bytes) -> tuple[int, list[str], int]:
    import pyarrow.parquet as pq

    parquet = pq.ParquetFile(io.BytesIO(raw))
    return int(parquet.metadata.num_rows), parquet.schema_arrow.names, parquet.metadata.num_row_groups


def find_factorynet_dataset(db: Session) -> Dataset | None:
    return next((dataset for dataset in db.query(Dataset).all() if (dataset.schema_json or {}).get("source_id") == FACTORYNET_SOURCE_ID), None)


def _download_factorynet() -> tuple[bytes, int, list[str], int, str]:
    request = urllib.request.Request(FACTORYNET_URL, headers={"User-Agent": "Nano-OntoPrompt temporal demo"})
    with urllib.request.urlopen(request, timeout=120) as response:
        raw = response.read()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != FACTORYNET_SHA256:
        raise ValueError(f"FactoryNet 文件哈希不匹配: {digest}")
    records, columns, row_groups = _parquet_summary(raw)
    if records <= 0 or not columns:
        raise ValueError("FactoryNet 文件为空或没有列")
    return raw, records, columns, row_groups, digest


def install_factorynet_dataset(db: Session) -> dict:
    existing = find_factorynet_dataset(db)
    if existing and existing.latest_version_id:
        version = db.query(DatasetVersion).filter(DatasetVersion.id == existing.latest_version_id).first()
        manifest = dict(existing.schema_json or {})
        if version and version.storage_uri and get_storage_service().object_exists(version.storage_uri):
            return {"id": FACTORYNET_SOURCE_ID, "dataset_id": existing.id, "version_id": version.id,
                    "installed": True, "records": version.rowcount if version else manifest.get("records"),
                    "columns": manifest.get("columns", []), "manifest": manifest, "reused": True}
        if version:
            raw, records, columns, row_groups, digest = _download_factorynet()
            uri = get_storage_service().put_bytes(
                "raw-datasets", f"datasets/{existing.id}/v{version.version_no}/data.bin", raw,
                content_type="application/octet-stream",
            )
            version.storage_uri = uri
            version.rowcount = records
            version.checksum = digest
            existing.readiness = "ready"
            manifest.update({
                "source_id": FACTORYNET_SOURCE_ID, "source_url": FACTORYNET_URL,
                "filename": FACTORYNET_FILE, "format": "parquet", "sha256": digest,
                "records": records, "columns": columns, "row_groups": row_groups,
                "temporal_source": True, "storage_recovery": "repaired_from_pinned_factorynet_source",
            })
            existing.schema_json = manifest
            db.commit()
            return {"id": FACTORYNET_SOURCE_ID, "dataset_id": existing.id, "version_id": version.id,
                    "installed": True, "records": records, "columns": columns,
                    "manifest": manifest, "reused": True, "repaired": True}

    raw, records, columns, row_groups, digest = _download_factorynet()

    svc = DatasetService(db)
    dataset = svc.create_dataset(
        FACTORYNET_DATASET_NAME,
        "structured",
        data_class="temporal",
        schema_json={"source_id": FACTORYNET_SOURCE_ID, "temporal_source": True},
    )
    version = svc.create_version(dataset.id, raw, rowcount=records)
    # DatasetService assigns ``latest_version_id`` before the generated UUID
    # is materialized on some SQLAlchemy versions. Set it again after the
    # version has been flushed so source discovery can resolve the object.
    dataset.latest_version_id = version.id
    version.checksum = digest
    manifest = {
        "source_id": FACTORYNET_SOURCE_ID,
        "source_url": FACTORYNET_URL,
        "source_repository": "https://github.com/Forgis-Labs/FactoryNet",
        "filename": FACTORYNET_FILE,
        "format": "parquet",
        "sha256": digest,
        "license": FACTORYNET_LICENSE,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "records": records,
        "columns": columns,
        "row_groups": row_groups,
        "temporal_source": True,
        "description": "CNC 三轴铣削的 FactoryNet S-E-F-C 工业时序文件",
    }
    dataset.schema_json = manifest
    db.commit()
    return {"id": FACTORYNET_SOURCE_ID, "dataset_id": dataset.id, "version_id": version.id,
            "installed": True, "records": records, "columns": columns, "manifest": manifest}
