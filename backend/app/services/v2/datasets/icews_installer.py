"""Installation helper for the official ICEWS 2023 Dataverse file."""
from __future__ import annotations

import io
import json
import urllib.request
import zipfile
from datetime import date
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.models.v2.dataset import Dataset, DatasetVersion
from app.services.storage_service import get_storage_service
from .icews_adapter import (
    ICEWS_DATASET_NAME, ICEWS_DOI, ICEWS_DOWNLOAD_URL, ICEWS_EXPECTED_ROWS,
    ICEWS_FILE_ID, ICEWS_FILENAME, ICEWS_SHA256, ICEWS_SOURCE_ID,
    parse_icews_tsv, sha256_bytes, icews_summary, normalize_icews_rows,
)


def download_icews_archive(
    url: str = ICEWS_DOWNLOAD_URL,
    *,
    opener: Callable[..., Any] | None = None,
    timeout: int = 90,
) -> bytes:
    """Download the pinned Dataverse archive and verify its content hash."""
    request = urllib.request.Request(url, headers={"User-Agent": "OntoPrompt/icews-installer"})
    response_factory = opener or urllib.request.urlopen
    with response_factory(request, timeout=timeout) as response:
        archive = response.read()
    digest = sha256_bytes(archive)
    if digest != ICEWS_SHA256:
        raise ValueError(f"ICEWS archive SHA-256 mismatch: expected {ICEWS_SHA256}, got {digest}")
    return archive


def extract_icews_tsv(archive: bytes) -> tuple[bytes, str]:
    """Return the tab file from the verified archive."""
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        candidates = [name for name in bundle.namelist() if name.lower().endswith((".tab", ".tsv", ".txt"))]
        if not candidates:
            raise ValueError("ICEWS archive contains no TSV/TAB data file")
        # Prefer the pinned filename while tolerating Dataverse directory
        # prefixes and a harmless extension variation.
        preferred = [name for name in candidates if name.rsplit("/", 1)[-1] == ICEWS_FILENAME.replace(".zip", "")]
        name = preferred[0] if preferred else candidates[0]
        return bundle.read(name), name


def find_icews_dataset(db: Session) -> Dataset | None:
    for dataset in db.query(Dataset).all():
        manifest = dataset.schema_json or {}
        if manifest.get("source_id") == ICEWS_SOURCE_ID:
            return dataset
    return None


def install_icews_dataset(db: Session, archive: bytes | None = None) -> dict[str, Any]:
    """Install or reuse the pinned ICEWS dataset in MinIO/PostgreSQL.

    Installation is idempotent: an existing dataset with the same verified
    archive hash is returned without creating another version.
    """
    existing = find_icews_dataset(db)
    if existing:
        manifest = existing.schema_json or {}
        if manifest.get("archive_sha256") == ICEWS_SHA256:
            version = db.query(DatasetVersion).filter(DatasetVersion.id == existing.latest_version_id).first()
            return {"dataset_id": existing.id, "version_id": version.id if version else None, "source_id": ICEWS_SOURCE_ID, "installed": True, "reused": True, "manifest": manifest}

    archive = archive if archive is not None else download_icews_archive()
    archive_digest = sha256_bytes(archive)
    if archive_digest != ICEWS_SHA256:
        raise ValueError(f"ICEWS archive SHA-256 mismatch: expected {ICEWS_SHA256}, got {archive_digest}")
    tsv, member_name = extract_icews_tsv(archive)
    rows = parse_icews_tsv(tsv)
    normalized, issues = normalize_icews_rows(rows)
    if len(rows) != ICEWS_EXPECTED_ROWS:
        raise ValueError(f"ICEWS row count mismatch: expected {ICEWS_EXPECTED_ROWS}, got {len(rows)}")
    if issues:
        raise ValueError(f"ICEWS validation failed during installation: {len(issues)} invalid rows")
    summary = icews_summary(normalized)
    manifest = {
        "source_id": ICEWS_SOURCE_ID, "doi": ICEWS_DOI, "file_id": ICEWS_FILE_ID,
        "filename": ICEWS_FILENAME, "archive_sha256": archive_digest,
        "data_sha256": sha256_bytes(tsv), "downloaded_on": date.today().isoformat(),
        "member": member_name, "terms": "Harvard Dataverse terms of use; verify before redistribution",
        "expected_rows": ICEWS_EXPECTED_ROWS, "summary": summary,
    }
    dataset = Dataset(name=ICEWS_DATASET_NAME, kind="structured", schema_json=manifest)
    db.add(dataset)
    db.flush()
    # The archive is named ``*.tab.zip``; the object must represent the
    # extracted tab file rather than accidentally becoming ``*.tab.tab``.
    extracted_name = ICEWS_FILENAME[:-4] if ICEWS_FILENAME.lower().endswith(".zip") else ICEWS_FILENAME
    storage_uri = get_storage_service().put_bytes(
        "raw-datasets", f"datasets/{dataset.id}/v1/{extracted_name}", tsv,
        content_type="text/tab-separated-values",
    )
    version = DatasetVersion(dataset_id=dataset.id, version_no=1, rowcount=len(rows), storage_uri=storage_uri, checksum=sha256_bytes(tsv))
    db.add(version)
    db.flush()
    dataset.latest_version_id = version.id
    db.commit()
    db.refresh(dataset)
    return {"dataset_id": dataset.id, "version_id": version.id, "source_id": ICEWS_SOURCE_ID, "installed": True, "reused": False, "manifest": manifest}
