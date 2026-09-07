"""Small, reproducible I-BADAS installer for the desktop workbench.

The upstream dataset is intentionally not vendored.  This module resolves a
single pinned Hugging Face revision, chooses twelve complete RGB-D/point-cloud
sample groups, verifies every downloaded object, and stores the result in the
same local/MinIO abstraction used by the rest of v2.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.models.v2.dataset import Dataset, DatasetVersion, MediaItem, MultimodalSample
from app.services.storage_service import get_storage_service

IBADAS_SOURCE_ID = "ibadas_12_demo"
IBADAS_DATASET_NAME = "I-BADAS 工业 RGB-D 多模态样例（12组）"
IBADAS_REPO = "moiaraya/i-badas"
IBADAS_REVISION = "f7e3812f1984144ae7f388697b4d23e139e30c29"
IBADAS_API_BASE = f"https://huggingface.co/api/datasets/{IBADAS_REPO}/tree/{IBADAS_REVISION}"
IBADAS_RESOLVE_BASE = f"https://huggingface.co/datasets/{IBADAS_REPO}/resolve/{IBADAS_REVISION}"
IBADAS_SOURCE_URL = f"https://huggingface.co/datasets/{IBADAS_REPO}"
IBADAS_LICENSE = "CC BY-NC 4.0"
IBADAS_SCENES = tuple(f"{index:06d}" for index in range(1, 7))
MAX_POINT_COUNT = 50_000

Asset = dict[str, Any]
Progress = Callable[[dict[str, Any]], None] | None


def _request_json(url: str, timeout: int = 45) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "OntoPrompt-workbench/ibadas"})
    last: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            last = exc
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    raise last or RuntimeError("I-BADAS 请求失败")


def _request_json_page(url: str, timeout: int = 45) -> tuple[Any, str | None]:
    request = urllib.request.Request(url, headers={"User-Agent": "OntoPrompt-workbench/ibadas"})
    last: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8")), response.headers.get("Link")
        except Exception as exc:
            last = exc
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    raise last or RuntimeError("I-BADAS 目录请求失败")


def _download(url: str, timeout: int = 90) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "OntoPrompt-workbench/ibadas"})
    last: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                chunks: list[bytes] = []
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    chunks.append(chunk)
                return b"".join(chunks)
        except Exception as exc:
            last = exc
            if attempt < 2:
                time.sleep(2.0 * (attempt + 1))
    raise last or RuntimeError("I-BADAS 下载失败")


def _download_point_cloud_to_temp(url: str, timeout: int = 180) -> tuple[str, int, str]:
    """Stream a large NPY asset to disk before deterministic reduction.

    Point clouds are intentionally exempt from the image/metadata guard.  The
    complete upstream object is hashed first; only the bounded downsample is
    then stored in the workbench object store.
    """
    request = urllib.request.Request(url, headers={"User-Agent": "OntoPrompt-workbench/ibadas"})
    digest = hashlib.sha256()
    total = 0
    handle = tempfile.NamedTemporaryFile(prefix="ibadas-point-", suffix=".npy", delete=False)
    try:
        last: Exception | None = None
        for attempt in range(3):
            try:
                handle.seek(0)
                handle.truncate(0)
                digest = hashlib.sha256()
                total = 0
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        handle.write(chunk)
                        digest.update(chunk)
                        total += len(chunk)
                break
            except Exception as exc:
                last = exc
                if attempt < 2:
                    time.sleep(3.0 * (attempt + 1))
        else:
            raise last or RuntimeError("I-BADAS 点云下载失败")
        handle.close()
        if total == 0:
            raise ValueError("I-BADAS 点云文件为空")
        return handle.name, total, digest.hexdigest()
    except Exception:
        handle.close()
        try:
            os.unlink(handle.name)
        except FileNotFoundError:
            pass
        raise


def _tree(scene: str) -> list[dict[str, Any]]:
    # The Hub caps a tree page at 1,000 entries and exposes an RFC 5988
    # ``Link: ...; rel=next`` cursor.  Following it is required for complete
    # anomaly coverage; using an oversized limit returns HTTP 400.
    query = urllib.parse.urlencode({"recursive": "1", "expand": "false", "limit": "1000"})
    url = f"{IBADAS_API_BASE}/test/{scene}?{query}"
    rows: list[dict[str, Any]] = []
    pages = 0
    while url and pages < 200:
        payload, link = _request_json_page(url)
        if not isinstance(payload, list):
            raise ValueError(f"I-BADAS 场景 {scene} 的目录响应无效")
        rows.extend(row for row in payload if isinstance(row, dict) and row.get("type") == "file" and row.get("path"))
        match = re.search(r"<([^>]+)>\s*;\s*rel=\"next\"", link or "")
        url = match.group(1) if match else ""
        pages += 1
    if url:
        raise ValueError(f"I-BADAS 场景 {scene} 目录分页超过安全上限")
    return rows


def _number(path: str, *, point: bool = False) -> str | None:
    stem = PurePosixPath(path).stem
    if point and stem.startswith("point_"):
        stem = stem[6:]
    return stem if re.fullmatch(r"\d+", stem) else None


def _role_index(rows: list[dict[str, Any]], prefix: str, *, point: bool = False) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in rows:
        path = str(row["path"])
        if not path.startswith(prefix):
            continue
        key = _number(path, point=point)
        if key:
            result[key] = path
    return result


def _annotation_labels(raw: bytes) -> dict[str, set[str]]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return {}
    categories = {str(item.get("id")): str(item.get("name")) for item in payload.get("categories", []) if isinstance(item, dict)}
    image_labels: dict[int, set[str]] = {}
    for image in payload.get("images", []):
        if isinstance(image, dict) and image.get("id") is not None:
            labels = {str(image.get("classification") or "")}
            image_labels[int(image["id"])] = {label for label in labels if label}
    for annotation in payload.get("annotations", []):
        if not isinstance(annotation, dict) or annotation.get("image_id") is None:
            continue
        labels = image_labels.setdefault(int(annotation["image_id"]), set())
        category = categories.get(str(annotation.get("category_id")))
        if category:
            labels.add(category)
    result: dict[str, set[str]] = {}
    for image in payload.get("images", []):
        if not isinstance(image, dict):
            continue
        match = re.search(r"/rgb/(\d+)\.png$", str(image.get("file_name") or ""))
        if match:
            result[match.group(1)] = image_labels.get(int(image.get("id", -1)), set())
    return result


def _scene_candidates(scene: str, rows: list[dict[str, Any]], annotation_raw: bytes) -> dict[str, Any]:
    indexes = {
        "good_rgb": _role_index(rows, f"test/{scene}/good/rgb/"),
        "good_depth": _role_index(rows, f"test/{scene}/good/depth/"),
        "good_mask": _role_index(rows, f"test/{scene}/good/mask_visib/"),
        "good_cloud": _role_index(rows, f"test/{scene}/good/point_clouds/", point=True),
        "anomaly_rgb": _role_index(rows, f"test/{scene}/anomaly/rgb/"),
        "anomaly_depth": _role_index(rows, f"test/{scene}/anomaly/depth/"),
        "anomaly_mask": _role_index(rows, f"test/{scene}/anomaly/mask_all/"),
        "anomaly_visible": _role_index(rows, f"test/{scene}/anomaly/mask_visib/"),
        "anomaly_cloud": _role_index(rows, f"test/{scene}/anomaly/point_clouds/", point=True),
    }
    good = sorted(set.intersection(*(set(indexes[name]) for name in ("good_rgb", "good_depth", "good_mask", "good_cloud"))))
    anomaly = sorted(set.intersection(*(set(indexes[name]) for name in ("anomaly_rgb", "anomaly_depth", "anomaly_mask", "anomaly_visible", "anomaly_cloud"))))
    if not good or not anomaly:
        raise ValueError(f"I-BADAS 场景 {scene} 没有完整的 good/anomaly RGB-D 点云配对")
    return {"scene": scene, "rows": rows, "indexes": indexes, "good": good, "anomaly": anomaly, "labels": _annotation_labels(annotation_raw), "annotation_raw": annotation_raw}


def _choose_samples(scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chosen: list[dict[str, Any]] = []
    for item in scenes:
        chosen.append({"scene": item["scene"], "quality": "good", "sample": item["good"][0], "labels": ["good"]})

    # Choose one anomaly per scene using a deterministic greedy coverage pass.
    # Ties are resolved by the numeric image id, so a new run cannot silently
    # reshuffle the demo when the upstream annotation order changes.
    uncovered: set[str] = set()
    for item in scenes:
        for sample in item["anomaly"]:
            uncovered.update(item["labels"].get(sample, set()))
    for item in scenes:
        candidates = item["anomaly"]
        selected = max(candidates, key=lambda sample: (len(item["labels"].get(sample, set()) & uncovered), len(item["labels"].get(sample, set())), -int(sample)))
        labels = sorted(item["labels"].get(selected, set())) or ["anomaly"]
        chosen.append({"scene": item["scene"], "quality": "anomaly", "sample": selected, "labels": labels})
        uncovered.difference_update(labels)
    return chosen


def _metadata_paths(scene: str, quality: str, rows: list[dict[str, Any]]) -> list[str]:
    prefix = f"test/{scene}/{quality}/"
    names = {PurePosixPath(str(row["path"])).name: str(row["path"]) for row in rows if str(row["path"]).startswith(prefix)}
    result = []
    for name in ("scene_camera.json", "scene_info.json"):
        if name in names:
            result.append(names[name])
    # The COCO file and pose file live at the scene root and are useful source
    # evidence for both quality labels.
    for name in ("annotations.coco.json", "poses.json"):
        path = f"test/{scene}/{name}"
        if any(str(row["path"]) == path for row in rows):
            result.append(path)
    return result


def find_ibadas_dataset(db: Session) -> Dataset | None:
    for dataset in db.query(Dataset).order_by(Dataset.created_at.desc()).all():
        if (dataset.schema_json or {}).get("source_id") == IBADAS_SOURCE_ID:
            return dataset
        # Old interrupted installs only persisted the display name.  Reusing
        # that record repairs the installation in place instead of creating a
        # second version whose global sample IDs collide.
        if dataset.name == IBADAS_DATASET_NAME and dataset.data_class == "multimodal":
            return dataset
    return None


def _serialize_asset(role: str, source_path: str, raw: bytes, uri: str) -> Asset:
    return {
        "role": role,
        "source_path": source_path,
        "storage_uri": uri,
        "size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "mime_type": {"rgb": "image/png", "depth": "application/octet-stream", "mask": "image/png", "mask_visible": "image/png", "point_cloud": "application/octet-stream", "metadata": "application/json"}.get(role, "application/octet-stream"),
    }


def _downsample_point_cloud(raw: bytes) -> tuple[bytes, dict[str, Any]]:
    """Read an NPY point cloud and deterministically cap it at 50k points."""
    try:
        import numpy as np

        points = np.load(io.BytesIO(raw), allow_pickle=False)
        points = np.asarray(points)
        if points.ndim == 1:
            points = points.reshape((-1, 1))
        elif points.ndim > 2:
            points = points.reshape((-1, points.shape[-1]))
        source_count = int(points.shape[0])
        step = max(1, (source_count + MAX_POINT_COUNT - 1) // MAX_POINT_COUNT)
        sampled = points[::step]
        output = io.BytesIO()
        np.save(output, sampled, allow_pickle=False)
        return output.getvalue(), {"source_point_count": source_count, "point_count": int(sampled.shape[0]), "downsample_step": step}
    except Exception as exc:
        # A point-cloud preview remains available even when optional NumPy is
        # absent; the manifest records that no deterministic reduction ran.
        return raw, {"source_point_count": None, "point_count": None, "downsampled": False, "processing_error": str(exc)[:200]}


def _downsample_point_cloud_file(path: str) -> tuple[bytes, dict[str, Any]]:
    try:
        import numpy as np

        points = np.load(path, allow_pickle=False, mmap_mode="r")
        points = np.asarray(points)
        if points.ndim == 1:
            points = points.reshape((-1, 1))
        elif points.ndim > 2:
            points = points.reshape((-1, points.shape[-1]))
        source_count = int(points.shape[0])
        step = max(1, (source_count + MAX_POINT_COUNT - 1) // MAX_POINT_COUNT)
        sampled = points[::step]
        output = io.BytesIO()
        np.save(output, sampled, allow_pickle=False)
        return output.getvalue(), {"source_point_count": source_count, "point_count": int(sampled.shape[0]), "downsample_step": step}
    except Exception as exc:
        with open(path, "rb") as handle:
            raw = handle.read()
        return raw, {"source_point_count": None, "point_count": None, "downsampled": False, "processing_error": str(exc)[:200]}


def _asset_from_row(item: MediaItem) -> Asset:
    metadata = item.metadata_json or {}
    return {
        "role": item.asset_role,
        "source_path": metadata.get("source_path") or item.source_path or item.storage_uri,
        "storage_uri": item.storage_uri,
        "size": metadata.get("stored_size"),
        "sha256": item.checksum,
        "mime_type": item.mime_type,
        "source_sha256": metadata.get("source_sha256"),
        "source_size": metadata.get("source_size"),
        **{key: metadata[key] for key in ("source_point_count", "point_count", "downsample_step", "downsampled") if key in metadata},
    }


def _stored_asset_matches(storage, item: MediaItem) -> bool:
    """Return true only for an object whose bytes match its recorded hash.

    Early I-BADAS attempts used a source-derived object key for RGB and mask
    files.  Two assets could therefore point at one object even though their
    database hashes differed.  Existence alone is not a valid resume signal:
    verify the bytes before an installer skips a previously recorded asset.
    """
    if not item.storage_uri or not item.checksum:
        return False
    try:
        raw = storage.get_object(item.storage_uri)
    except Exception:
        return False
    return hashlib.sha256(raw).hexdigest() == item.checksum


def ibadas_assets_intact(db: Session, dataset: Dataset | None) -> bool:
    """Fast readiness check for the fixed twelve-sample desktop package.

    It intentionally catches legacy shared object keys without downloading the
    entire catalogue on each page load.  The installer subsequently performs
    full byte-hash validation before it reuses any individual asset.
    """
    if not dataset or dataset.readiness != "ready" or not dataset.latest_version_id:
        return False
    version = db.query(DatasetVersion).filter(DatasetVersion.id == dataset.latest_version_id).first()
    if not version or version.rowcount != 12 or (dataset.schema_json or {}).get("status") != "ready":
        return False
    samples = db.query(MultimodalSample).filter(MultimodalSample.dataset_version_id == version.id).all()
    assets = db.query(MediaItem).filter(MediaItem.dataset_version_id == version.id).all()
    if len(samples) != 12:
        return False
    seen_identity: set[tuple[str | None, str, str | None]] = set()
    checksums_by_uri: dict[str, set[str]] = {}
    storage = get_storage_service()
    for asset in assets:
        source_path = (asset.metadata_json or {}).get("source_path") or asset.source_path
        identity = (asset.sample_id, asset.asset_role, source_path)
        if identity in seen_identity:
            return False
        seen_identity.add(identity)
        checksums_by_uri.setdefault(asset.storage_uri, set()).add(asset.checksum or "")
        try:
            if not storage.object_exists(asset.storage_uri):
                return False
        except Exception:
            return False
    assets_by_sample: dict[str, set[str]] = {}
    for asset in assets:
        if asset.sample_id:
            assets_by_sample.setdefault(asset.sample_id, set()).add(asset.asset_role)
    required = {"rgb", "depth", "mask", "point_cloud", "metadata"}
    for sample in samples:
        roles = assets_by_sample.get(sample.id, set())
        if not required.issubset(roles):
            return False
        if sample.label == "anomaly" and "mask_visible" not in roles:
            return False
    # A shared URI is valid only when it really represents the identical
    # content.  Different asset hashes under one URI require a resume repair.
    return all(len(checksums) == 1 and "" not in checksums for checksums in checksums_by_uri.values())


def _asset_paths(selection: dict[str, Any], spec: dict[str, Any]) -> list[tuple[str, str]]:
    scene = selection["scene"]
    quality = selection["quality"]
    sample = selection["sample"]
    indexes = spec["indexes"]
    paths: list[tuple[str, str]] = [
        ("rgb", indexes[f"{quality}_rgb"][sample]),
        ("depth", indexes[f"{quality}_depth"][sample]),
        ("mask", indexes[f"{quality}_mask"][sample]),
        ("point_cloud", indexes[f"{quality}_cloud"][sample]),
    ]
    if quality == "anomaly":
        paths.insert(3, ("mask_visible", indexes["anomaly_visible"][sample]))
    paths.extend(("metadata", path) for path in _metadata_paths(scene, quality, spec["rows"]))
    return paths


def _prepare_installation(db: Session, storage) -> tuple[Dataset, DatasetVersion]:
    dataset = find_ibadas_dataset(db)
    seed_manifest = {
        "source_id": IBADAS_SOURCE_ID,
        "source_url": IBADAS_SOURCE_URL,
        "repository": IBADAS_REPO,
        "revision": IBADAS_REVISION,
        "license": IBADAS_LICENSE,
        "status": "installing",
        "sample_count": 12,
        "modalities": ["rgb", "depth", "mask", "point_cloud", "metadata"],
    }
    if dataset is None:
        dataset = Dataset(name=IBADAS_DATASET_NAME, kind="unstructured", data_class="multimodal", privacy_level="standard", readiness="installing", schema_json=seed_manifest)
        db.add(dataset)
        db.flush()
    else:
        dataset.name = IBADAS_DATASET_NAME
        dataset.kind = "unstructured"
        dataset.data_class = "multimodal"
        dataset.readiness = "installing"
        dataset.schema_json = {**seed_manifest, **(dataset.schema_json or {}), "source_id": IBADAS_SOURCE_ID, "status": "installing"}
    version = db.query(DatasetVersion).filter(DatasetVersion.id == dataset.latest_version_id).first() if dataset.latest_version_id else None
    if version is None:
        manifest_key = f"datasets/{dataset.id}/v1/manifest.json"
        placeholder = b"{}"
        uri = storage.put_bytes("raw-datasets", manifest_key, placeholder, content_type="application/json")
        version = DatasetVersion(dataset_id=dataset.id, version_no=1, rowcount=0, storage_uri=uri, checksum=hashlib.sha256(placeholder).hexdigest())
        db.add(version)
        db.flush()
        dataset.latest_version_id = version.id
    db.commit()
    db.refresh(dataset)
    db.refresh(version)
    return dataset, version


def install_ibadas_dataset(db: Session, progress: Progress = None) -> dict[str, Any]:
    """Resume or install the pinned twelve-group I-BADAS sample in place.

    The dataset identity and each fully downloaded sample group are committed
    explicitly.  Progress is reported by the caller through a separate session,
    so a UI heartbeat can never commit half-written media rows.
    """
    storage = get_storage_service()
    dataset, version = _prepare_installation(db, storage)
    if progress:
        progress({"stage": "恢复安装记录", "completed": 0, "total": 12, "dataset_id": dataset.id})

    scene_specs: list[dict[str, Any]] = []
    for index, scene in enumerate(IBADAS_SCENES, start=1):
        if progress:
            progress({"stage": "读取上游清单", "completed": index - 1, "total": len(IBADAS_SCENES), "dataset_id": dataset.id})
        rows = _tree(scene)
        annotation_path = f"test/{scene}/annotations.coco.json"
        annotation_raw = _download(f"{IBADAS_RESOLVE_BASE}/{annotation_path}?download=true")
        scene_specs.append(_scene_candidates(scene, rows, annotation_raw))
        if progress:
            progress({"stage": "读取上游清单", "completed": index, "total": len(IBADAS_SCENES), "dataset_id": dataset.id})

    selections = _choose_samples(scene_specs)
    if progress:
        progress({"stage": "确认 12 组样例", "completed": len(selections), "total": len(selections), "dataset_id": dataset.id})
    specs = {item["scene"]: item for item in scene_specs}
    total_assets = sum(len(_asset_paths(selection, specs[selection["scene"]])) for selection in selections)
    completed_assets = 0
    manifest_samples: list[dict[str, Any]] = []

    for sample_index, selection in enumerate(selections, start=1):
        scene, quality, sample = selection["scene"], selection["quality"], selection["sample"]
        spec = specs[scene]
        sample_key = f"ibadas:{scene}:{quality}:{sample}"
        sample_row = db.query(MultimodalSample).filter(
            MultimodalSample.dataset_version_id == version.id,
            MultimodalSample.sample_key == sample_key,
        ).first()
        if sample_row is None:
            # Never use the human-readable sample key as a global primary key.
            sample_row = MultimodalSample(
                dataset_version_id=version.id, sample_key=sample_key, scene_id=scene,
                split="test", label=quality, labels=selection["labels"],
                metadata_json={"source_revision": IBADAS_REVISION, "quality": quality, "image_id": sample},
            )
            db.add(sample_row)
            db.flush()
        else:
            sample_row.scene_id, sample_row.split, sample_row.label = scene, "test", quality
            sample_row.labels = selection["labels"]
            sample_row.metadata_json = {**(sample_row.metadata_json or {}), "source_revision": IBADAS_REVISION, "quality": quality, "image_id": sample}

        expected = _asset_paths(selection, spec)
        current_items = db.query(MediaItem).filter(MediaItem.dataset_version_id == version.id, MediaItem.sample_id == sample_row.id).all()
        by_identity = {(item.asset_role, (item.metadata_json or {}).get("source_path") or item.source_path): item for item in current_items}
        manifest_sample: dict[str, Any] = {"sample_id": sample_key, "scene_id": scene, "quality": quality, "image_id": sample, "labels": selection["labels"], "assets": []}

        for role, source_path in expected:
            current = by_identity.get((role, source_path))
            if current and _stored_asset_matches(storage, current):
                manifest_sample["assets"].append(_asset_from_row(current))
                completed_assets += 1
                continue

            temp_path: str | None = None
            try:
                if role == "point_cloud":
                    temp_path, source_size, source_sha256 = _download_point_cloud_to_temp(f"{IBADAS_RESOLVE_BASE}/{source_path}?download=true")
                    raw, point_cloud_meta = _downsample_point_cloud_file(temp_path)
                else:
                    raw = spec["annotation_raw"] if source_path.endswith("annotations.coco.json") else _download(f"{IBADAS_RESOLVE_BASE}/{source_path}?download=true")
                    source_size, source_sha256, point_cloud_meta = len(raw), hashlib.sha256(raw).hexdigest(), {}
                filename = PurePosixPath(source_path).name
                mime = _serialize_asset(role, source_path, raw, "")["mime_type"]
                uri = storage.put_bytes("media", f"datasets/{dataset.id}/ibadas/{sample_row.id}/{role}-{filename}", raw, content_type=mime)
                asset = _serialize_asset(role, source_path, raw, uri)
                asset.update({"source_sha256": source_sha256, "source_size": source_size, **point_cloud_meta})
                if current:
                    current.storage_uri, current.original_name, current.mime_type, current.checksum = uri, filename, mime, asset["sha256"]
                    current.source_path = source_path
                    current.metadata_json = {"source_path": source_path, "scene_id": scene, "image_id": sample, "quality": quality, "stored_size": len(raw), "source_sha256": source_sha256, "source_size": source_size, **point_cloud_meta}
                else:
                    current = MediaItem(dataset_version_id=version.id, sample_id=sample_row.id, asset_role=role, media_type=role, storage_uri=uri, original_name=filename, mime_type=mime, checksum=asset["sha256"], source_path=source_path, metadata_json={"source_path": source_path, "scene_id": scene, "image_id": sample, "quality": quality, "stored_size": len(raw), "source_sha256": source_sha256, "source_size": source_size, **point_cloud_meta})
                    db.add(current)
                manifest_sample["assets"].append(asset)
                completed_assets += 1
            finally:
                if temp_path:
                    try:
                        os.unlink(temp_path)
                    except FileNotFoundError:
                        pass
            if progress:
                progress({"stage": "下载资产", "completed": completed_assets, "total": total_assets, "dataset_id": dataset.id})

        # Atomic at the sample-group boundary.  A cancellation or network error
        # after this point can resume from verified assets without publishing
        # the dataset as ready.
        db.commit()
        manifest_samples.append(manifest_sample)
        if progress:
            progress({"stage": "校验完整样例", "completed": sample_index, "total": len(selections), "dataset_id": dataset.id})

    missing: list[str] = []
    for selection in selections:
        key = f"ibadas:{selection['scene']}:{selection['quality']}:{selection['sample']}"
        row = db.query(MultimodalSample).filter(MultimodalSample.dataset_version_id == version.id, MultimodalSample.sample_key == key).first()
        available = {
            (item.asset_role, (item.metadata_json or {}).get("source_path") or item.source_path)
            for item in db.query(MediaItem).filter(
                MediaItem.dataset_version_id == version.id,
                MediaItem.sample_id == (row.id if row else ""),
            ).all()
            if _stored_asset_matches(storage, item)
        }
        for identity in _asset_paths(selection, specs[selection["scene"]]):
            if identity not in available:
                missing.append(f"{key}:{identity[0]}")
    if missing:
        dataset.readiness = "installing"
        db.commit()
        raise RuntimeError(f"I-BADAS 完整性校验失败，缺少 {', '.join(missing[:5])}")

    manifest = {
        "source_id": IBADAS_SOURCE_ID, "source_url": IBADAS_SOURCE_URL,
        "repository": IBADAS_REPO, "revision": IBADAS_REVISION,
        "license": IBADAS_LICENSE, "installed_at": datetime.now(timezone.utc).isoformat(),
        "sample_count": len(selections), "modalities": ["rgb", "depth", "mask", "point_cloud", "metadata"],
        "samples": manifest_samples,
    }
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    manifest_key = f"datasets/{dataset.id}/v{version.version_no}/manifest.json"
    version.rowcount = len(selections)
    version.storage_uri = storage.put_bytes("raw-datasets", manifest_key, manifest_bytes, content_type="application/json")
    version.checksum = hashlib.sha256(manifest_bytes).hexdigest()
    dataset.latest_version_id = version.id
    dataset.readiness = "ready"
    dataset.schema_json = {**manifest, "status": "ready", "manifest_sha256": version.checksum, "total_bytes": sum(int(asset.get("size") or 0) for sample in manifest_samples for asset in sample["assets"])}
    db.commit()
    db.refresh(dataset)
    if progress:
        progress({"stage": "发布版本", "completed": len(selections), "total": len(selections), "dataset_id": dataset.id})
    return {"source_id": IBADAS_SOURCE_ID, "dataset_id": dataset.id, "version_id": version.id, "installed": True, "reused": False, "manifest": dataset.schema_json}
