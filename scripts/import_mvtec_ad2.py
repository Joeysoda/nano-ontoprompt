#!/usr/bin/env python3
"""Import a small, reproducible MVTec AD 2 image slice.

The official archive is downloaded by the user under its CC BY-NC-SA 4.0
terms.  This utility never commits images to Git and imports at most 32 files
(two normal and two anomalous files per scenario).
"""
from __future__ import annotations

import argparse
import io
import json
import mimetypes
import posixpath
import uuid
import zipfile
from collections import defaultdict
from pathlib import PurePosixPath

from app.database import SessionLocal
from app.models.ontology import OntologyProject
from app.models.user import User
from app.models.v2.dataset import Dataset, DatasetVersion, MediaItem
from app.services.storage_service import get_storage_service
from app.services.v2.dataset_service import DatasetService


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
DATASET_NAME = "MVTec AD 2 视觉检测样例"
ONTOLOGY_NAME = "MVTec AD 2 视觉检测本体"


def _scenario_and_kind(name: str) -> tuple[str, str]:
    parts = [p for p in PurePosixPath(name).parts if p not in {".", ""}]
    lower = [p.lower() for p in parts]
    normal_markers = {"good", "normal"}
    anomaly_markers = {"anomaly", "anomalous", "bad", "defect", "crack", "broken", "cut", "hole", "contamination", "color", "scratch", "deformation", "missing", "rough", "bent"}
    marker_index = next((i for i, value in enumerate(lower) if value in normal_markers | anomaly_markers), -1)
    if marker_index >= 0:
        scenario = parts[marker_index - 1] if marker_index else (parts[0] if parts else "unknown")
        kind = "normal" if lower[marker_index] in normal_markers else "anomaly"
    else:
        scenario = parts[0] if parts else "unknown"
        kind = "anomaly" if any(x in " ".join(lower) for x in ("defect", "crack", "anomaly", "bad")) else "normal"
    return scenario, kind


def select_members(names: list[str], max_per_kind: int = 2) -> list[tuple[str, str, str]]:
    grouped: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for name in sorted(names):
        suffix = PurePosixPath(name).suffix.lower()
        if suffix not in IMAGE_EXTENSIONS:
            continue
        scenario, kind = _scenario_and_kind(name)
        grouped[scenario][kind].append(name)
    selected: list[tuple[str, str, str]] = []
    for scenario in sorted(grouped):
        for kind in ("normal", "anomaly"):
            for name in grouped[scenario].get(kind, [])[:max_per_kind]:
                selected.append((name, scenario, kind))
    return selected[:32]


def import_archive(input_path: str) -> dict:
    db = SessionLocal()
    try:
        existing = db.query(Dataset).filter(Dataset.name == DATASET_NAME).first()
        ontology = db.query(OntologyProject).filter(OntologyProject.name == ONTOLOGY_NAME).first()
        admin = db.query(User).filter(User.role == "admin").first()
        if not admin:
            raise RuntimeError("未找到管理员账号")
        if not ontology:
            ontology = OntologyProject(id=str(uuid.uuid4()), name=ONTOLOGY_NAME, domain="工业视觉检测",
                                       description="MVTec AD 2 公开工业缺陷图片的证据优先本体样例",
                                       build_mode="multimodal", created_by=admin.id)
            db.add(ontology)
            db.commit()
            db.refresh(ontology)
        if existing:
            count = db.query(MediaItem).join(DatasetVersion, MediaItem.dataset_version_id == DatasetVersion.id).filter(DatasetVersion.dataset_id == existing.id).count()
            return {"dataset_id": existing.id, "ontology_id": ontology.id, "media_count": count, "created": False}
        with zipfile.ZipFile(input_path) as archive:
            candidates = [n for n in archive.namelist() if not n.endswith("/")]
            selected = select_members(candidates)
            if not selected:
                raise RuntimeError("压缩包中没有可识别的 PNG/JPG/JPEG/WEBP 图片")
            svc = DatasetService(db)
            dataset = svc.create_dataset(DATASET_NAME, "unstructured")
            manifest = []
            storage = get_storage_service()
            version = None
            # create_version stores a manifest in raw-datasets and gives all
            # media a stable dataset/version provenance anchor.
            version = svc.create_version(dataset.id, b"{}", rowcount=len(selected))
            placeholder_uri = version.storage_uri
            for index, (member, scenario, kind) in enumerate(selected):
                content = archive.read(member)
                filename = posixpath.basename(member)
                safe_name = f"{index:03d}-{scenario}-{kind}-{filename}"
                mime = mimetypes.guess_type(filename)[0] or "image/png"
                uri = storage.put_bytes("media", f"datasets/{dataset.id}/v{version.version_no}/{safe_name}", content, content_type=mime)
                item = MediaItem(dataset_version_id=version.id, media_type="image", storage_uri=uri, ocr_status="pending")
                db.add(item)
                manifest.append({"filename": filename, "archive_path": member, "scenario": scenario, "label": kind, "storage_uri": uri})
            version_manifest = json.dumps({"dataset": DATASET_NAME, "license": "CC BY-NC-SA 4.0", "source": "MVTec AD 2 official archive", "items": manifest}, ensure_ascii=False).encode()
            manifest_uri = storage.put_bytes("raw-datasets", f"datasets/{dataset.id}/v{version.version_no}/manifest.json", version_manifest, content_type="application/json")
            if placeholder_uri:
                try:
                    storage.delete_object(placeholder_uri)
                except Exception:
                    pass
            version.storage_uri = manifest_uri
            dataset.schema_json = {"media_type": "image", "fields": ["scenario", "label", "archive_path"], "license": "CC BY-NC-SA 4.0"}
            db.commit()
            return {"dataset_id": dataset.id, "ontology_id": ontology.id, "media_count": len(selected), "created": True, "manifest_uri": manifest_uri}
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="MVTec AD 2 官方 zip 路径")
    args = parser.parse_args()
    print(json.dumps(import_archive(args.input), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
