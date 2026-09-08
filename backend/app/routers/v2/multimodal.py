"""Evidence-first multimodal ingestion and MiniMax M3 construction runs."""
from __future__ import annotations

import mimetypes
import csv
import hashlib
import io
import json
import math
import os
import struct
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZipFile

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.deps import get_current_user, require_editor
from app.models.ontology import OntologyProject
from app.models.model_config import ModelConfig
from app.models.v2.construction import ConstructionRun
from app.models.v2.construction_draft import ConstructionDraft  # noqa: F401 - register invocation FK target
from app.models.v2.dataset import Dataset, DatasetVersion, MediaItem, MultimodalSample
from app.models.v2.multimodal_install import MultimodalInstallTask
from app.models.v2.workbench_task import ModelInvocation
from app.models.v2.multimodal import ExtractedFragment
from app.services import encryption_service
from app.services.model_config_selector import llm_call_kwargs
from app.services.storage_service import get_storage_service
from app.services.v2.construction_service import add_evidence, create_run, serialize_run, update_run
from app.services.v2.graph.falkordb_service import FalkorDBService
from app.services.v2.ontology_materializer import attach_revision_to_materialized_rows, materialize_ontology
from app.services.v2.datasets.ibadas_installer import (
    IBADAS_DATASET_NAME,
    IBADAS_LICENSE,
    IBADAS_REPO,
    IBADAS_REVISION,
    IBADAS_SCENES,
    IBADAS_SOURCE_ID,
    IBADAS_SOURCE_URL,
    find_ibadas_dataset,
    ibadas_assets_intact,
    install_ibadas_dataset,
)

router = APIRouter(prefix="/multimodal", dependencies=[Depends(get_current_user)])
MODEL_NAME = "MiniMax-M3"
ROLE_LABEL = {"rgb": "RGB", "depth": "深度", "mask": "异常掩码", "mask_visible": "可见掩码", "point_cloud": "点云", "metadata": "元数据", "pdf": "PDF 证据", "docx": "DOCX 证据"}
MAX_IMPORTED_ASSET_BYTES = 12 * 1024 * 1024


def _pick_m3_config(db: Session, model_id: str | None = None):
    """Select an exact non-local MiniMax-M3 slot for standard construction."""
    query = db.query(ModelConfig).filter(ModelConfig.config_type == "llm")
    candidates = []
    if model_id:
        selected = query.filter(ModelConfig.id == model_id).first()
        if selected:
            candidates.append(selected)
    if not candidates:
        candidates = query.order_by(ModelConfig.updated_at.desc()).all()
    return next((item for item in candidates if MODEL_NAME in [str(name) for name in (item.models or [])] and str(item.provider or "").lower() not in {"ollama", "local"}), None)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class FragmentCreate(BaseModel):
    media_item_id: str
    dataset_version_id: str
    fragment_type: str = Field(pattern="^(text|ocr|table|metadata)$")
    content: str
    locator: dict = {}
    extractor: str = Field(pattern="^(markitdown|ocr|llm|rule|bridge_fallback)$")
    status: str = "completed"
    error: str | None = None


class MultimodalRunCreate(BaseModel):
    dataset_id: str
    ontology_id: str
    model_id: str | None = None
    sample_limit: int = Field(default=32, ge=1, le=32)
    prompt: str | None = None
    sample_ids: list[str] = Field(default_factory=list, max_length=32)
    selected_assets: list[str] = Field(default_factory=lambda: ["rgb", "depth", "mask", "point_cloud", "metadata"])
    privacy_level: str = Field(default="standard", pattern="^(standard|private)$")
    send_fields: list[str] = Field(default_factory=lambda: ["metadata", "labels", "image_stats", "point_stats"])


class IbedasInstallRequest(BaseModel):
    """Request body kept explicit so the UI can show the install contract."""

    source_id: str = IBADAS_SOURCE_ID


class MultimodalImportResult(BaseModel):
    task_id: str
    status: str


class _InstallCancelled(RuntimeError):
    pass


def _serialize_install_task(task: MultimodalInstallTask) -> dict[str, Any]:
    return {
        "id": task.id,
        "task_id": task.id,
        "source_id": task.source_id,
        "status": task.status,
        "progress": task.progress or {},
        "dataset_id": task.dataset_id,
        "result": task.result_json or {},
        "error": task.error,
        "cancel_requested": bool(task.cancel_requested),
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
    }


def _touch_install_task(db: Session, task: MultimodalInstallTask, **values: Any) -> None:
    for key, value in values.items():
        setattr(task, key, value)
    task.updated_at = datetime.now(timezone.utc)
    db.commit()


def _ibadas_ready(dataset: Dataset | None, db: Session) -> bool:
    return ibadas_assets_intact(db, dataset)


def _celery_enabled() -> bool:
    return os.getenv("CELERY_ENABLED", "").lower() in {"1", "true", "yes"}


def _asset_payload(item: MediaItem) -> dict[str, Any]:
    """Serialize an evidence asset without assuming that every file is an image.

    I-BADAS stores depth and point-cloud arrays as NumPy files.  Returning the
    raw object URL as their ``preview_url`` led browsers to render an empty
    tile.  The explicit rendering URL below keeps the original asset available
    while giving the evidence workspace a safe, human-readable derivative.
    """
    base = f"/api/v2/multimodal/assets/{item.id}"
    return {
        "id": item.id,
        "role": item.asset_role,
        "media_type": item.media_type,
        "original_name": item.original_name,
        "mime_type": item.mime_type,
        "checksum": item.checksum,
        "storage_uri": item.storage_uri,
        "metadata": item.metadata_json or {},
        "content_url": f"{base}/content",
        "preview_url": f"{base}/render",
        "preview_info_url": f"{base}/preview",
        "pointcloud_url": f"{base}/pointcloud" if item.asset_role == "point_cloud" else None,
    }


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


def _encode_rgb_png(array: Any) -> bytes:
    """Encode a uint8 H×W×3 array without adding a Pillow runtime dependency."""
    import numpy as np

    rgb = np.ascontiguousarray(array, dtype=np.uint8)
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("预览数组必须是 H×W×3 RGB")
    height, width, _ = rgb.shape
    if height <= 0 or width <= 0:
        raise ValueError("预览数组为空")
    rows = b"".join(b"\x00" + rgb[index].tobytes() for index in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", header) + _png_chunk(b"IDAT", zlib.compress(rows, level=6)) + _png_chunk(b"IEND", b"")


def _depth_preview(item: MediaItem) -> tuple[bytes, dict[str, Any]]:
    """Build a deterministic pseudo-colour depth preview plus honest stats."""
    try:
        import numpy as np
    except Exception as exc:  # pragma: no cover - NumPy is a v2 dependency
        raise HTTPException(501, "当前环境未安装深度预览所需的 NumPy") from exc
    try:
        raw = get_storage_service().get_object(item.storage_uri)
        depth = np.asarray(np.load(io.BytesIO(raw), allow_pickle=False))
        while depth.ndim > 2:
            depth = depth[..., 0]
        if depth.ndim == 1:
            depth = depth.reshape((1, -1))
        if depth.ndim != 2:
            raise ValueError("深度数组不是二维栅格")
        depth = depth.astype("float32", copy=False)
        finite = np.isfinite(depth)
        total = int(depth.size)
        valid = int(finite.sum())
        if not valid:
            colour = np.zeros((*depth.shape, 3), dtype=np.uint8)
            stats = {"min": None, "max": None, "p01": None, "p50": None, "p99": None, "invalid_ratio": 1.0}
        else:
            values = depth[finite]
            p01, p50, p99 = [float(value) for value in np.percentile(values, [1, 50, 99])]
            lower, upper = p01, p99
            if upper <= lower:
                upper = lower + max(abs(lower) * 0.01, 1e-6)
            normalised = np.clip((depth - lower) / (upper - lower), 0.0, 1.0)
            # A compact blue→cyan→yellow pseudo-colour palette.  Invalid pixels
            # remain dark rather than being silently represented as measured data.
            red = np.clip(1.5 - np.abs(4.0 * normalised - 3.0), 0.0, 1.0)
            green = np.clip(1.5 - np.abs(4.0 * normalised - 2.0), 0.0, 1.0)
            blue = np.clip(1.5 - np.abs(4.0 * normalised - 1.0), 0.0, 1.0)
            colour = (np.stack([red, green, blue], axis=-1) * 255).astype(np.uint8)
            colour[~finite] = 18
            stats = {
                "min": float(values.min()),
                "max": float(values.max()),
                "p01": p01,
                "p50": p50,
                "p99": p99,
                "invalid_ratio": round(1.0 - valid / total, 6) if total else 1.0,
            }
        return _encode_rgb_png(colour), {
            "shape": [int(depth.shape[0]), int(depth.shape[1])],
            "valid_values": valid,
            "total_values": total,
            "unit": (item.metadata_json or {}).get("unit") or "来源未声明",
            **stats,
        }
    except HTTPException:
        raise
    except FileNotFoundError as exc:
        raise HTTPException(404, "对象存储中找不到该深度资产") from exc
    except Exception as exc:
        raise HTTPException(422, f"深度文件无法解析：{str(exc)[:240]}") from exc


def _sample_payload(sample: MultimodalSample) -> dict[str, Any]:
    return {
        "id": sample.id,
        "sample_key": sample.sample_key,
        "scene_id": sample.scene_id,
        "split": sample.split,
        "label": sample.label,
        "labels": sample.labels or [],
        "metadata": sample.metadata_json or {},
    }


@router.get("/catalog")
def multimodal_catalog(db: Session = Depends(get_db)):
    """Catalog cards shown before the first click on the multimodal flow."""
    installed = find_ibadas_dataset(db)
    latest_task = db.query(MultimodalInstallTask).filter(
        MultimodalInstallTask.source_id == IBADAS_SOURCE_ID,
    ).order_by(MultimodalInstallTask.created_at.desc()).first()
    datasets = db.query(Dataset).filter(Dataset.data_class == "multimodal").order_by(Dataset.created_at.desc()).all()
    return {
        "sources": [{
            "id": IBADAS_SOURCE_ID,
            "name": IBADAS_DATASET_NAME,
            "repository": IBADAS_REPO,
            "revision": IBADAS_REVISION,
            "source_url": IBADAS_SOURCE_URL,
            "license": IBADAS_LICENSE,
            "scene_count": len(IBADAS_SCENES),
            "sample_count": 12,
            "modalities": ["RGB", "深度 depth", "掩码 mask", "点云 point cloud", "JSON/CSV 元数据"],
            "download_policy": "仅安装 12 组演示样例，不下载约 40.3 GB 全量数据",
            "installed": _ibadas_ready(installed, db),
            "dataset_id": installed.id if installed else None,
            "install_task": _serialize_install_task(latest_task) if latest_task else None,
        }],
        "datasets": [{
            "id": dataset.id,
            "name": dataset.name,
            "data_class": dataset.data_class,
            "privacy_level": dataset.privacy_level,
            "version_id": dataset.latest_version_id,
            "manifest": dataset.schema_json or {},
        } for dataset in datasets],
    }


@router.post("/catalog/i-badas/install", status_code=202, response_model=MultimodalImportResult)
def install_i_badas_catalog(
    body: IbedasInstallRequest | None = None,
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db),
    _=Depends(require_editor),
):
    """Queue the pinned twelve-group installer; never fetch the full archive."""
    source_id = (body.source_id if body else IBADAS_SOURCE_ID).strip()
    if source_id != IBADAS_SOURCE_ID:
        raise HTTPException(400, "当前仅支持固定的 I-BADAS 12 组桌面样例")
    active = db.query(MultimodalInstallTask).filter(
        MultimodalInstallTask.source_id == source_id,
        MultimodalInstallTask.status.in_(["queued", "running", "cancel_requested"]),
    ).order_by(MultimodalInstallTask.created_at.desc()).first()
    if active:
        return {"task_id": active.id, "status": active.status}
    installed = find_ibadas_dataset(db)
    if _ibadas_ready(installed, db):
        latest = db.query(MultimodalInstallTask).filter(MultimodalInstallTask.source_id == source_id).order_by(MultimodalInstallTask.updated_at.desc()).first()
        if latest:
            return {"task_id": latest.id, "status": "completed"}
        task = MultimodalInstallTask(source_id=source_id, status="completed", dataset_id=installed.id, progress={"stage": "安装完成", "completed": 12, "total": 12})
        db.add(task)
        db.commit()
        return {"task_id": task.id, "status": task.status}
    task = db.query(MultimodalInstallTask).filter(MultimodalInstallTask.source_id == source_id).order_by(MultimodalInstallTask.updated_at.desc()).first()
    if task and task.status in {"failed", "cancelled"}:
        task.status = "queued"
        task.cancel_requested = False
        task.error = None
        task.progress = {"stage": "恢复安装", "completed": 0, "total": 12}
        if installed:
            task.dataset_id = installed.id
    else:
        task = MultimodalInstallTask(source_id=source_id, status="queued", dataset_id=installed.id if installed else None, progress={"stage": "等待开始", "completed": 0, "total": 12})
        db.add(task)
    db.commit()
    db.refresh(task)
    if _celery_enabled():
        try:
            from app.tasks.v2.workbench import run_ibadas_install_task
            run_ibadas_install_task.delay(task.id)
        except Exception as exc:
            task.status = "failed"
            task.error = f"Celery 任务派发失败：{str(exc)[:500]}"
            db.commit()
            raise HTTPException(503, detail={"error": "CELERY_UNAVAILABLE", "message": task.error, "next": "启动 Redis 与 Celery worker 后重试"}) from exc
    elif background_tasks is not None:
        background_tasks.add_task(_execute_ibadas_install, task.id)
    return {"task_id": task.id, "status": task.status}


@router.get("/catalog/install/{task_id}")
def get_multimodal_install_task(task_id: str, db: Session = Depends(get_db)):
    task = db.query(MultimodalInstallTask).filter(MultimodalInstallTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "安装任务不存在")
    return _serialize_install_task(task)


@router.post("/catalog/install/{task_id}/cancel")
def cancel_multimodal_install_task(task_id: str, db: Session = Depends(get_db), _=Depends(require_editor)):
    task = db.query(MultimodalInstallTask).filter(MultimodalInstallTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "安装任务不存在")
    if task.status in {"queued", "running"}:
        task.cancel_requested = True
        task.status = "cancel_requested"
        task.updated_at = datetime.now(timezone.utc)
        db.commit()
    return _serialize_install_task(task)


def _execute_ibadas_install(task_id: str) -> None:
    db = SessionLocal()
    try:
        # Multiple delivery attempts can exist after an API reload.  Claim
        # the durable row under a PostgreSQL lock so only the first worker
        # performs network I/O; later messages become harmless no-ops.
        task = db.query(MultimodalInstallTask).filter(MultimodalInstallTask.id == task_id).with_for_update().first()
        if not task:
            return
        if task.status in {"running", "completed"}:
            return
        if task.status == "cancel_requested" or task.cancel_requested:
            _touch_install_task(db, task, status="cancelled", error="用户取消了样例安装", progress=task.progress or {})
            return
        _touch_install_task(db, task, status="running", progress={"stage": "连接数据源", "completed": 0, "total": 12})

        def progress(event: dict[str, Any]) -> None:
            # The installer owns its business session.  A progress update must
            # never call commit() on it, otherwise partially downloaded sample
            # rows become durable by accident.
            progress_db = SessionLocal()
            try:
                current = progress_db.query(MultimodalInstallTask).filter(MultimodalInstallTask.id == task_id).first()
                if current and current.cancel_requested:
                    raise _InstallCancelled("用户取消了样例安装")
                if current:
                    current.progress = {key: value for key, value in event.items() if key != "dataset_id"}
                    if event.get("dataset_id"):
                        current.dataset_id = str(event["dataset_id"])
                    current.updated_at = datetime.now(timezone.utc)
                    progress_db.commit()
            finally:
                progress_db.close()

        result = install_ibadas_dataset(db, progress=progress)
        task = db.query(MultimodalInstallTask).filter(MultimodalInstallTask.id == task_id).first()
        if task:
            _touch_install_task(db, task, status="completed", dataset_id=result.get("dataset_id"), result_json=result, progress={"stage": "安装完成", "completed": 12, "total": 12}, error=None)
    except _InstallCancelled as exc:
        db.rollback()
        task = db.query(MultimodalInstallTask).filter(MultimodalInstallTask.id == task_id).first()
        if task:
            _touch_install_task(db, task, status="cancelled", error=str(exc), progress=task.progress or {})
    except Exception as exc:
        db.rollback()
        task = db.query(MultimodalInstallTask).filter(MultimodalInstallTask.id == task_id).first()
        if task:
            _touch_install_task(db, task, status="failed", error=str(exc)[:2000], progress=task.progress or {})
    finally:
        db.close()


def _safe_archive_entries(raw: bytes) -> tuple[ZipFile, dict[str, Any], list[str]]:
    try:
        archive = ZipFile(io.BytesIO(raw))
    except BadZipFile as exc:
        raise HTTPException(400, "导入文件不是有效 ZIP") from exc
    names = archive.namelist()
    if len(names) != len(set(names)):
        archive.close()
        raise HTTPException(400, "ZIP 中存在重复路径")
    for name in names:
        path = Path(name)
        if path.is_absolute() or ".." in path.parts:
            archive.close()
            raise HTTPException(400, f"ZIP 路径穿越被拒绝: {name}")
    manifest_name = next((name for name in ("manifest.json", "manifest.csv") if name in names), None)
    if not manifest_name:
        archive.close()
        raise HTTPException(400, "ZIP 必须包含 manifest.json 或 manifest.csv")
    if manifest_name.endswith(".json"):
        try:
            manifest = json.loads(archive.read(manifest_name).decode("utf-8"))
        except Exception as exc:
            archive.close()
            raise HTTPException(400, "manifest.json 无法解析") from exc
    else:
        rows = list(csv.DictReader(io.StringIO(archive.read(manifest_name).decode("utf-8-sig"))))
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            sample_id = str(row.get("sample_id") or row.get("sample_key") or "").strip()
            if not sample_id:
                archive.close()
                raise HTTPException(400, "manifest.csv 缺少 sample_id")
            sample = grouped.setdefault(sample_id, {"sample_id": sample_id, "label": row.get("label"), "assets": []})
            sample["assets"].append({"role": row.get("asset_role") or row.get("role"), "path": row.get("path"), "mime_type": row.get("mime_type"), "sha256": row.get("sha256")})
        manifest = {"dataset": {"name": "导入的多模态数据"}, "privacy_level": "standard", "samples": list(grouped.values())}
    if not isinstance(manifest, dict) or not isinstance(manifest.get("samples"), list) or not manifest["samples"]:
        archive.close()
        raise HTTPException(400, "manifest 必须包含非空 samples 数组")
    return archive, manifest, names


def _normalise_manifest(manifest: dict[str, Any], archive_names: list[str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    privacy = str(manifest.get("privacy_level") or manifest.get("dataset", {}).get("privacy_level") or "standard").lower()
    if privacy not in {"standard", "private"}:
        raise HTTPException(400, "privacy_level 只能是 standard 或 private")
    dataset_meta = manifest.get("dataset") if isinstance(manifest.get("dataset"), dict) else {}
    name = str(dataset_meta.get("name") or manifest.get("name") or "导入的多模态数据").strip()[:200]
    rows: list[dict[str, Any]] = []
    sample_ids: set[str] = set()
    known_paths = set(archive_names)
    for raw_sample in manifest["samples"]:
        if not isinstance(raw_sample, dict):
            raise HTTPException(400, "sample 条目必须是对象")
        sample_id = str(raw_sample.get("sample_id") or raw_sample.get("sample_key") or "").strip()
        if not sample_id or sample_id in sample_ids:
            raise HTTPException(400, "sample_id 缺失或重复")
        sample_ids.add(sample_id)
        assets = raw_sample.get("assets")
        if not isinstance(assets, list) or not assets:
            raise HTTPException(400, f"样本 {sample_id} 没有资产")
        normal_assets: list[dict[str, Any]] = []
        roles: set[str] = set()
        for raw_asset in assets:
            if not isinstance(raw_asset, dict):
                raise HTTPException(400, f"样本 {sample_id} 的资产格式无效")
            role = str(raw_asset.get("asset_role") or raw_asset.get("role") or "primary").strip()
            path = str(raw_asset.get("path") or raw_asset.get("relative_path") or "").strip()
            if not path or path not in known_paths or path == "manifest.json" or path == "manifest.csv":
                raise HTTPException(400, f"样本 {sample_id} 的资产路径不存在: {path}")
            if role in roles:
                raise HTTPException(400, f"样本 {sample_id} 存在重复 asset_role: {role}")
            roles.add(role)
            normal_assets.append({"role": role, "path": path, "mime_type": raw_asset.get("mime_type"), "sha256": raw_asset.get("sha256")})
        rows.append({"sample_id": sample_id, "scene_id": raw_sample.get("scene_id"), "split": raw_sample.get("split") or "import", "label": raw_sample.get("label"), "labels": raw_sample.get("labels") if isinstance(raw_sample.get("labels"), list) else [], "metadata": raw_sample.get("metadata") if isinstance(raw_sample.get("metadata"), dict) else {}, "assets": normal_assets})
    return {"name": name, "privacy_level": privacy, "source_url": dataset_meta.get("source_url"), "license": dataset_meta.get("license"), "sample_count": len(rows)}, rows


def _execute_archive_import(task_id: str, raw: bytes) -> None:
    db = SessionLocal()
    archive = None
    try:
        task = db.query(MultimodalInstallTask).filter(MultimodalInstallTask.id == task_id).first()
        archive, manifest, names = _safe_archive_entries(raw)
        dataset_meta, samples = _normalise_manifest(manifest, names)
        if task:
            _touch_install_task(db, task, status="running", progress={"stage": "校验并写入资产", "completed": 0, "total": len(samples)})
        dataset = Dataset(name=dataset_meta["name"], kind="unstructured", data_class="multimodal", privacy_level=dataset_meta["privacy_level"], readiness="installing", schema_json={"source_id": task.source_id if task else "zip_import", **dataset_meta})
        db.add(dataset)
        db.flush()
        storage = get_storage_service()
        placeholder = b"{}"
        manifest_uri = storage.put_bytes("raw-datasets", f"datasets/{dataset.id}/v1/manifest.json", placeholder, content_type="application/json")
        version = DatasetVersion(dataset_id=dataset.id, version_no=1, rowcount=len(samples), storage_uri=manifest_uri, checksum=hashlib.sha256(placeholder).hexdigest())
        db.add(version)
        db.flush()
        output_samples: list[dict[str, Any]] = []
        for index, item in enumerate(samples, start=1):
            sample_row = MultimodalSample(dataset_version_id=version.id, sample_key=item["sample_id"], scene_id=item["scene_id"], split=item["split"], label=item["label"], labels=item["labels"], metadata_json=item["metadata"])
            db.add(sample_row)
            db.flush()
            out_assets: list[dict[str, Any]] = []
            for asset in item["assets"]:
                role = asset["role"]
                payload = archive.read(asset["path"])
                if role != "point_cloud" and len(payload) > MAX_IMPORTED_ASSET_BYTES:
                    raise HTTPException(400, f"资产超过安全上限 12MB: {asset['path']}")
                digest = hashlib.sha256(payload).hexdigest()
                if asset["sha256"] and str(asset["sha256"]).lower() != digest:
                    raise HTTPException(400, f"资产哈希不匹配: {asset['path']}")
                mime = str(asset["mime_type"] or mimetypes.guess_type(asset["path"])[0] or "application/octet-stream")
                uri = storage.put_bytes("media", f"datasets/{dataset.id}/ibadas/{sample_row.id}/{Path(asset['path']).name}", payload, content_type=mime)
                db.add(MediaItem(dataset_version_id=version.id, sample_id=sample_row.id, asset_role=role, media_type=role, storage_uri=uri, original_name=Path(asset["path"]).name, mime_type=mime, checksum=digest, source_path=asset["path"], metadata_json={"source_path": asset["path"]}))
                out_assets.append({"role": role, "source_path": asset["path"], "storage_uri": uri, "size": len(payload), "sha256": digest, "mime_type": mime})
            output_samples.append({"sample_id": sample_row.id, "sample_key": item["sample_id"], "scene_id": item["scene_id"], "label": item["label"], "assets": out_assets})
            if task:
                task.progress = {"stage": "校验并写入资产", "completed": index, "total": len(samples)}
                task.updated_at = datetime.now(timezone.utc)
                db.commit()
        final_manifest = {"source_id": task.source_id if task else "zip_import", **dataset_meta, "samples": output_samples}
        final_bytes = json.dumps(final_manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
        version.storage_uri = storage.put_bytes("raw-datasets", f"datasets/{dataset.id}/v1/manifest.json", final_bytes, content_type="application/json")
        version.checksum = hashlib.sha256(final_bytes).hexdigest()
        dataset.latest_version_id = version.id
        dataset.readiness = "ready"
        dataset.schema_json = {**final_manifest, "manifest_sha256": version.checksum}
        db.commit()
        if task:
            _touch_install_task(db, task, status="completed", dataset_id=dataset.id, result_json={"dataset_id": dataset.id, "version_id": version.id, "manifest": dataset.schema_json}, progress={"stage": "导入完成", "completed": len(samples), "total": len(samples)})
    except HTTPException as exc:
        db.rollback()
        task = db.query(MultimodalInstallTask).filter(MultimodalInstallTask.id == task_id).first()
        if task:
            _touch_install_task(db, task, status="failed", error=str(exc.detail), progress=task.progress or {})
    except Exception as exc:
        db.rollback()
        task = db.query(MultimodalInstallTask).filter(MultimodalInstallTask.id == task_id).first()
        if task:
            _touch_install_task(db, task, status="failed", error=str(exc)[:2000], progress=task.progress or {})
    finally:
        if archive:
            archive.close()
        db.close()


@router.post("/imports", status_code=202, response_model=MultimodalImportResult)
async def import_multimodal_archive(file: UploadFile = File(...), background_tasks: BackgroundTasks = None, db: Session = Depends(get_db), _=Depends(require_editor)):
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "导入文件为空")
    task = MultimodalInstallTask(source_id="zip_import", status="queued", progress={"stage": "等待校验", "completed": 0, "total": 0})
    db.add(task)
    db.commit()
    db.refresh(task)
    if background_tasks is not None:
        background_tasks.add_task(_execute_archive_import, task.id, raw)
    return {"task_id": task.id, "status": task.status}


@router.get("/datasets/{dataset_id}/samples")
def list_multimodal_samples(
    dataset_id: str,
    scene_id: str | None = None,
    label: str | None = None,
    sample: str | None = None,
    version_id: str | None = None,
    db: Session = Depends(get_db),
):
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(404, "Dataset not found")
    resolved_version = version_id or dataset.latest_version_id
    if not resolved_version:
        return {"dataset_id": dataset_id, "version_id": None, "samples": [], "count": 0}
    query = db.query(MultimodalSample).filter(MultimodalSample.dataset_version_id == resolved_version)
    if scene_id:
        query = query.filter(MultimodalSample.scene_id == scene_id)
    if label:
        query = query.filter(MultimodalSample.label == label)
    if sample:
        query = query.filter(MultimodalSample.sample_key.ilike(f"%{sample}%"))
    rows = query.order_by(MultimodalSample.scene_id.asc(), MultimodalSample.label.asc(), MultimodalSample.sample_key.asc()).all()
    result: list[dict[str, Any]] = []
    for row in rows:
        media = db.query(MediaItem).filter(MediaItem.sample_id == row.id, MediaItem.dataset_version_id == resolved_version).order_by(MediaItem.asset_role.asc()).all()
        result.append({"id": row.id, "sample_id": row.id, **_sample_payload(row), "assets": [_asset_payload(item) for item in media]})
    return {"dataset_id": dataset_id, "version_id": resolved_version, "samples": result, "count": len(result)}


@router.get("/samples/{sample_id}/preview")
def preview_multimodal_sample(sample_id: str, db: Session = Depends(get_db)):
    sample = db.query(MultimodalSample).filter(MultimodalSample.id == sample_id).first()
    if not sample:
        raise HTTPException(404, "样例不存在")
    media = db.query(MediaItem).filter(MediaItem.sample_id == sample.id, MediaItem.dataset_version_id == sample.dataset_version_id).order_by(MediaItem.asset_role.asc()).all()
    return {"sample": _sample_payload(sample), "assets": [_asset_payload(item) for item in media]}


@router.get("/samples/{sample_id}/evidence")
def multimodal_sample_evidence(sample_id: str, db: Session = Depends(get_db)):
    """Return one synchronized evidence workspace payload for a sample.

    This is deliberately separate from the paged samples list: decoding depth
    arrays and metadata is only done once a user opens a concrete sample.
    """
    sample = db.query(MultimodalSample).filter(MultimodalSample.id == sample_id).first()
    if not sample:
        raise HTTPException(404, "样例不存在")
    media = db.query(MediaItem).filter(
        MediaItem.sample_id == sample.id,
        MediaItem.dataset_version_id == sample.dataset_version_id,
    ).order_by(MediaItem.asset_role.asc(), MediaItem.original_name.asc()).all()
    assets = [_asset_payload(item) for item in media]
    metadata_sources: list[dict[str, Any]] = []
    for item in media:
        if item.asset_role != "metadata":
            continue
        record: dict[str, Any] = {"asset_id": item.id, "name": item.original_name or item.source_path or item.id}
        try:
            raw = get_storage_service().get_object(item.storage_uri)
            decoded = json.loads(raw.decode("utf-8"))
            record["value"] = decoded
        except Exception as exc:
            record["error"] = f"元数据文件无法读取：{str(exc)[:180]}"
        metadata_sources.append(record)
    depth = next((item for item in media if item.asset_role == "depth"), None)
    depth_stats = None
    depth_error = None
    if depth is not None:
        try:
            _, depth_stats = _depth_preview(depth)
        except HTTPException as exc:
            depth_error = str(exc.detail)
    roles = {item.asset_role for item in media}
    return {
        "sample": _sample_payload(sample),
        "assets": assets,
        "metadata": {"sample": sample.metadata_json or {}, "sources": metadata_sources},
        "depth": {"asset_id": depth.id, "stats": depth_stats, "error": depth_error} if depth else None,
        "completeness": {"roles": sorted(roles), "missing_required_roles": sorted({"rgb", "depth", "mask", "point_cloud", "metadata"} - roles)},
    }


@router.get("/assets/{media_id}/content")
def get_multimodal_asset(media_id: str, db: Session = Depends(get_db)):
    item = db.query(MediaItem).filter(MediaItem.id == media_id).first()
    if not item:
        raise HTTPException(404, "资产不存在")
    try:
        content = get_storage_service().get_object(item.storage_uri)
    except FileNotFoundError as exc:
        raise HTTPException(404, "对象存储中找不到该资产") from exc
    return Response(content=content, media_type=item.mime_type or mimetypes.guess_type(item.original_name or "")[0] or "application/octet-stream", headers={"X-Asset-SHA256": item.checksum or ""})


@router.get("/assets/{media_id}/preview")
def get_multimodal_asset_preview(media_id: str, db: Session = Depends(get_db)):
    """Describe the browser-safe render variant and deterministic statistics."""
    item = db.query(MediaItem).filter(MediaItem.id == media_id).first()
    if not item:
        raise HTTPException(404, "资产不存在")
    payload = _asset_payload(item)
    if item.asset_role == "depth":
        _, stats = _depth_preview(item)
        payload["depth_stats"] = stats
    return payload


@router.get("/assets/{media_id}/render")
def render_multimodal_asset(media_id: str, db: Session = Depends(get_db)):
    """Serve a displayable form of an evidence asset without mutating storage."""
    item = db.query(MediaItem).filter(MediaItem.id == media_id).first()
    if not item:
        raise HTTPException(404, "资产不存在")
    if item.asset_role == "depth":
        png, stats = _depth_preview(item)
        return Response(
            content=png,
            media_type="image/png",
            # HTTP header values must be Latin-1.  The user-facing unit fallback
            # in ``stats`` is Chinese, so keep this optional diagnostic header
            # ASCII-only; the full Unicode-safe payload remains available from
            # the adjacent ``/preview`` endpoint.
            headers={
                "X-Asset-SHA256": item.checksum or "",
                "X-Depth-Stats": json.dumps(stats, ensure_ascii=True, separators=(",", ":")),
            },
        )
    return get_multimodal_asset(media_id, db)


@router.get("/assets/{media_id}/pointcloud")
def get_pointcloud_preview(media_id: str, limit: int = Query(50000, ge=100, le=50000), db: Session = Depends(get_db)):
    """Return a bounded numeric preview for the point-cloud canvas.

    The stored asset remains the source of truth.  This endpoint only reads
    NumPy ``.npy`` arrays (the pinned I-BADAS format), drops non-finite rows,
    and applies a stable stride so the browser never receives an unbounded
    point set.  No geometry is invented when the optional NumPy dependency is
    missing; the caller receives a clear 501 instead.
    """
    item = db.query(MediaItem).filter(MediaItem.id == media_id).first()
    if not item:
        raise HTTPException(404, "资产不存在")
    if item.asset_role != "point_cloud":
        raise HTTPException(400, "该资产不是点云")
    try:
        import numpy as np
    except Exception as exc:  # pragma: no cover - optional dependency path
        raise HTTPException(501, "当前环境未安装点云预览所需的 NumPy") from exc
    try:
        raw = get_storage_service().get_object(item.storage_uri)
        points = np.load(io.BytesIO(raw), allow_pickle=False)
        points = np.asarray(points)
        if points.ndim == 1:
            points = points.reshape((-1, 1))
        elif points.ndim > 2:
            points = points.reshape((-1, points.shape[-1]))
        if points.shape[1] < 3:
            raise ValueError("点云数组至少需要 x/y/z 三列")
        points = points[:, :3].astype("float32", copy=False)
        points = points[np.isfinite(points).all(axis=1)]
        source_count = int(points.shape[0])
        stride = max(1, math.ceil(source_count / int(limit)))
        sampled = points[::stride]
        bounds_min = sampled.min(axis=0).tolist() if len(sampled) else [0.0, 0.0, 0.0]
        bounds_max = sampled.max(axis=0).tolist() if len(sampled) else [0.0, 0.0, 0.0]
        return {"media_id": item.id, "sample_id": item.sample_id, "source_checksum": item.checksum, "source_point_count": source_count, "point_count": int(len(sampled)), "stride": stride, "bounds": {"min": bounds_min, "max": bounds_max}, "points": sampled.tolist()}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(422, f"点云文件无法解析：{str(exc)[:240]}") from exc


@router.get("/status")
def multimodal_status(probe: bool = Query(False), db: Session = Depends(get_db)):
    """Return safe M3 availability metadata without exposing credentials."""
    config = _pick_m3_config(db)
    available = False
    probe_error = None
    if probe and config:
        try:
            kwargs = llm_call_kwargs(config)
            if not kwargs or not kwargs.get("api_key"):
                raise RuntimeError("凭据无法解密")
            from app.services.llm_service import _call_llm
            _call_llm(**kwargs, messages=[{"role": "system", "content": "只返回 OK"}, {"role": "user", "content": "ping"}], json_mode=False)
            available = True
        except Exception as exc:
            available = False
            probe_error = str(exc)[:300]
    return {
        "configured": bool(config),
        "available": available,
        "gateway_required": bool(os.getenv("LITELLM_API_BASE")),
        "gateway_reachable": bool(available),
        "upstream_authorized": bool(available),
        "model": MODEL_NAME,
        "model_id": config.id if available else None,
        "api_base": config.api_base if available else None,
        "message": "MiniMax M3 可用" if available else (probe_error or "MiniMax M3 尚未配置或当前 Key 不具备权限"),
    }


@router.get("/sources")
def multimodal_sources(db: Session = Depends(get_db)):
    rows: list[dict[str, Any]] = []
    datasets = db.query(Dataset).filter(Dataset.kind == "unstructured").order_by(Dataset.created_at.desc()).all()
    for dataset in datasets:
        versions = db.query(DatasetVersion).filter(DatasetVersion.dataset_id == dataset.id).order_by(DatasetVersion.version_no.desc()).all()
        media = db.query(MediaItem).filter(MediaItem.dataset_version_id == versions[0].id).all() if versions else []
        rows.append({
            "id": dataset.id,
            "name": dataset.name,
            "kind": dataset.kind,
            "source": "uploaded" if not dataset.name.lower().startswith("mvtec") else "mvtec_ad2",
            "version_id": versions[0].id if versions else None,
            "media_count": len(media),
            "media": [{"id": x.id, "media_type": x.media_type, "storage_uri": x.storage_uri, "ocr_status": x.ocr_status} for x in media],
        })
    manifest = Path(__file__).resolve().parents[3] / "data" / "mvtec_ad2" / "manifest.json"
    if manifest.exists():
        rows.insert(0, {"id": "mvtec-ad2-builtin", "name": "MVTec AD 2 样例", "kind": "unstructured", "source": "mvtec_ad2", "media_count": 0, "media": [], "manifest": str(manifest)})
    return {"sources": rows, "count": len(rows), "note": "官方 MVTec 图片需先通过导入脚本安装；未安装时不会伪造样例"}


@router.post("/fragments")
def create_fragment(body: FragmentCreate, db: Session = Depends(get_db)):
    media = db.query(MediaItem).filter(MediaItem.id == body.media_item_id, MediaItem.dataset_version_id == body.dataset_version_id).first()
    if not media:
        raise HTTPException(404, "Media item or dataset version not found")
    fragment = ExtractedFragment(**body.model_dump())
    db.add(fragment)
    db.commit()
    db.refresh(fragment)
    return _serialize(fragment)


@router.get("/fragments/{media_item_id}")
def list_fragments(media_item_id: str, db: Session = Depends(get_db)):
    items = db.query(ExtractedFragment).filter(ExtractedFragment.media_item_id == media_item_id).order_by(ExtractedFragment.created_at.asc()).all()
    return {"media_item_id": media_item_id, "fragments": [_serialize(item) for item in items], "count": len(items)}


@router.post("/runs", status_code=202)
def create_multimodal_run(body: MultimodalRunCreate, background_tasks: BackgroundTasks, db: Session = Depends(get_db), _=Depends(require_editor)):
    dataset = db.query(Dataset).filter(Dataset.id == body.dataset_id).first()
    if not dataset:
        raise HTTPException(404, "Dataset not found")
    if dataset.data_class != "multimodal":
        raise HTTPException(409, "多模态构建只能使用 data_class=multimodal 的数据集")
    ontology = db.query(OntologyProject).filter(OntologyProject.id == body.ontology_id).first()
    if not ontology:
        raise HTTPException(404, "Ontology not found")
    if getattr(ontology, "data_class", "regular") != "multimodal":
        raise HTTPException(409, "多模态数据只能追加到同类多模态本体")
    # A private dataset is a hard boundary: even if a caller posts
    # ``privacy_level=standard``, the server never selects or invokes a cloud
    # model for it.
    privacy_level = "private" if dataset.privacy_level == "private" else body.privacy_level
    dataset.privacy_level = privacy_level
    config = None if privacy_level == "private" else _pick_m3_config(db, body.model_id)
    model_name = str((config.models or [MODEL_NAME])[0]) if config else ("规则 + 人工" if privacy_level == "private" else None)
    run = create_run(db, ontology_id=body.ontology_id, dataset_id=body.dataset_id, mode="multimodal", model_name=model_name,
                     config={"sample_limit": body.sample_limit, "prompt": body.prompt or "", "sample_ids": body.sample_ids, "selected_assets": body.selected_assets, "privacy_level": privacy_level, "send_fields": body.send_fields})
    if _celery_enabled():
        try:
            from app.tasks.v2.workbench import run_multimodal_construction_task
            run_multimodal_construction_task.delay(run.id, body.model_id, body.sample_limit, body.prompt, body.sample_ids, body.selected_assets, privacy_level, body.send_fields)
        except Exception as exc:
            update_run(db, run, status="failed", error=f"Celery 任务派发失败：{str(exc)[:500]}")
            raise HTTPException(503, detail={"error": "CELERY_UNAVAILABLE", "message": "构建任务未启动，请启动 Celery worker 后重试"}) from exc
    else:
        background_tasks.add_task(_execute_multimodal_run, run.id, body.model_id, body.sample_limit, body.prompt, body.sample_ids, body.selected_assets, privacy_level, body.send_fields)
    return serialize_run(run)


def _execute_multimodal_run(run_id: str, model_id: str | None, sample_limit: int, prompt: str | None, sample_ids: list[str], selected_assets: list[str], privacy_level: str, send_fields: list[str]) -> None:
    db = SessionLocal()
    try:
        run = db.query(ConstructionRun).filter(ConstructionRun.id == run_id).first()
        if not run:
            return
        config = None if privacy_level == "private" else _pick_m3_config(db, model_id)
        if privacy_level != "private" and not config:
            update_run(db, run, status="waiting_for_model", error="MiniMax M3 未配置或不可用；任务停在等待模型，不会自动切换本地模型")
            return
        versions = db.query(DatasetVersion).filter(DatasetVersion.dataset_id == run.dataset_id).order_by(DatasetVersion.version_no.desc()).all()
        if not versions:
            update_run(db, run, status="failed", progress={"completed": 0, "total": 0}, error="Dataset 没有版本")
            return
        version = versions[0]
        media_query = db.query(MediaItem).filter(MediaItem.dataset_version_id == version.id)
        if sample_ids:
            media_query = media_query.filter(MediaItem.sample_id.in_(sample_ids))
        if selected_assets:
            media_query = media_query.filter(MediaItem.asset_role.in_(selected_assets))
        media = media_query.order_by(MediaItem.created_at.asc()).limit(sample_limit * max(1, len(selected_assets))).all()
        update_run(db, run, status="running", progress={"completed": 0, "total": len(media)})
        if not media:
            update_run(db, run, status="failed", progress={"completed": 0, "total": 0}, error="Dataset 没有可处理的媒体文件")
            return
        storage = get_storage_service()
        graph = FalkorDBService()
        nodes: list[dict] = []
        relations: list[dict] = []
        completed = 0
        failures = 0
        for item in media:
            current_run = db.query(ConstructionRun).filter(ConstructionRun.id == run_id).first()
            if current_run and current_run.cancel_requested:
                update_run(db, current_run, status="cancelled", error="用户取消了构建任务")
                return
            item.ocr_status = "processing"
            db.commit()
            filename = Path(item.storage_uri.split("/")[-1]).name
            invocation: ModelInvocation | None = None
            invocation_started = time.monotonic()
            try:
                sample_row = db.query(MultimodalSample).filter(MultimodalSample.id == item.sample_id).first() if item.sample_id else None
                official_label = (sample_row.label if sample_row else None) or ("anomaly" if "anomaly" in filename.lower() else ("normal" if "normal" in filename.lower() else "unknown"))
                if privacy_level == "private":
                    text = f"规则摘要：{ROLE_LABEL.get(item.asset_role, item.asset_role)} · {sample_row.sample_key if sample_row else filename} · 标签 {official_label}"
                    extractor = "rule"
                    model_for_evidence = None
                    confidence_method = "official_label_and_source_id"
                else:
                    raw = storage.get_object(item.storage_uri)
                    # Binary depth, masks and point clouds are never sent to
                    # M3.  The deterministic processor sends only the selected
                    # textual/structural fields for non-RGB assets.
                    if item.asset_role not in {"rgb", "image"}:
                        text = f"结构摘要：角色 {item.asset_role}；文件 {filename}；样例 {sample_row.sample_key if sample_row else 'unknown'}"
                        # This summary is produced by the deterministic
                        # modality processor.  It must not be presented as
                        # an M3 result merely because the overall run is in
                        # standard privacy mode.
                        extractor = "rule"
                        model_for_evidence = None
                        confidence_method = "deterministic_modality_summary"
                    else:
                        # Keep the durable log limited to visible text and an
                        # asset reference.  The image itself is sent to M3 for
                        # this explicitly selected RGB item, but its Base64
                        # bytes never enter the invocation record.
                        request_payload = json.dumps({
                            "run_id": run.id,
                            "dataset_version_id": version.id,
                            "media_id": item.id,
                            "sample_id": item.sample_id,
                            "asset_role": item.asset_role,
                            "storage_uri": item.storage_uri,
                            "source_path": item.source_path,
                            "checksum": item.checksum,
                            "mime_type": item.mime_type,
                            "prompt": prompt or f"提取 {filename} 中可见文本、缺陷标签、设备编号和定位信息；没有证据的字段写 unknown。",
                            "send_fields": send_fields,
                        }, ensure_ascii=False, sort_keys=True, default=str)
                        invocation = ModelInvocation(
                            construction_run_id=run.id,
                            route_alias=MODEL_NAME,
                            provider=str(config.provider or "cloud"),
                            model_name=str((config.models or [MODEL_NAME])[0]),
                            status="running",
                            phase="ontology_mapping",
                            request_ciphertext=encryption_service.encrypt(request_payload),
                            request_hash=hashlib.sha256(request_payload.encode("utf-8")).hexdigest(),
                            metadata_json={
                                "purpose": "multimodal_rgb_extraction",
                                "run_id": run.id,
                                "media_id": item.id,
                                "sample_id": item.sample_id,
                                "asset_role": item.asset_role,
                                "asset_checksum": item.checksum,
                                "asset_storage_uri": item.storage_uri,
                                "send_fields": send_fields,
                                "image_bytes_logged": False,
                            },
                        )
                        db.add(invocation)
                        db.commit()
                        text = _call_minimax(config, raw, filename, prompt)
                        extractor = "llm"
                        model_for_evidence = MODEL_NAME
                        confidence_method = "model_self_report"
                        response_payload = text or ""
                        invocation.status = "completed"
                        invocation.response_ciphertext = encryption_service.encrypt(response_payload)
                        invocation.response_hash = hashlib.sha256(response_payload.encode("utf-8")).hexdigest()
                        invocation.duration_ms = int((time.monotonic() - invocation_started) * 1000)
                        invocation.completed_at = datetime.now(timezone.utc)
                        db.commit()
                    if not text.strip():
                        raise RuntimeError("MiniMax M3 返回空内容")
                fragment = ExtractedFragment(media_item_id=item.id, dataset_version_id=version.id, fragment_type="text",
                                             content=text, locator={"filename": filename, "storage_uri": item.storage_uri, "sample_id": item.sample_id, "send_fields": send_fields}, extractor=extractor, status="completed")
                db.add(fragment)
                db.flush()
                add_evidence(db, run=run, assertion_id=f"fragment:{fragment.id}", assertion_kind="property", extractor=extractor,
                             source_media_id=item.id, source_sample_id=item.sample_id, source_dataset_version=version.id, model_name=model_for_evidence,
                             confidence_method=confidence_method, evidence_text=text[:4000], content={"media_uri": item.storage_uri, "filename": filename, "selected_assets": selected_assets, "send_fields": send_fields})
                media_id = f"media:{item.id}"
                fragment_id = f"fragment:{fragment.id}"
                inspection_id = f"inspection:{item.id}"
                nodes.extend([
                    {"id": media_id, "entity_type": "MediaAsset", "properties": {"storage_uri": item.storage_uri, "media_type": item.media_type, "asset_role": item.asset_role, "filename": filename, "sample_id": item.sample_id}},
                    {"id": fragment_id, "entity_type": "ExtractedFragment", "properties": {"content": text[:8000], "extractor": extractor, "source_media_id": item.id, "source_sample_id": item.sample_id}},
                    {"id": inspection_id, "entity_type": "InspectionEvent", "properties": {"official_label": official_label, "source_media_id": item.id, "source_sample_id": item.sample_id}},
                ])
                relations.extend([
                    {"source": media_id, "target": fragment_id, "type": "DESCRIBES", "properties": {"extractor": extractor, "model_name": model_for_evidence}},
                    {"source": media_id, "target": inspection_id, "type": "OBSERVED_IN", "properties": {"extractor": "rule", "label_source": "official_sample_label"}},
                ])
                if official_label == "anomaly":
                    anomaly_id = f"anomaly:{item.id}"
                    nodes.append({"id": anomaly_id, "entity_type": "AnomalyEvent", "properties": {"label": "anomaly", "source_media_id": item.id}})
                    relations.append({"source": inspection_id, "target": anomaly_id, "type": "HAS_ANOMALY", "properties": {"extractor": "rule"}})
                item.ocr_status = "done"
                completed += 1
            except Exception as exc:
                failures += 1
                if invocation is not None:
                    stored_invocation = db.query(ModelInvocation).filter(ModelInvocation.id == invocation.id).first()
                    if stored_invocation:
                        stored_invocation.status = "failed"
                        stored_invocation.error = str(exc)[:1000]
                        stored_invocation.duration_ms = int((time.monotonic() - invocation_started) * 1000)
                        stored_invocation.completed_at = datetime.now(timezone.utc)
                item.ocr_status = "failed"
                db.commit()
                db.add(ExtractedFragment(media_item_id=item.id, dataset_version_id=version.id, fragment_type="text", content="",
                                         locator={"filename": filename, "storage_uri": item.storage_uri, "sample_id": item.sample_id}, extractor="rule" if privacy_level == "private" else "llm", status="failed", error=str(exc)[:1000]))
                db.commit()
            update_run(db, run, progress={"completed": completed + failures, "total": len(media), "failed": failures})
        written_nodes = graph.upsert_instances(run.ontology_id, nodes) if nodes else 0
        written_edges = graph.upsert_relations(run.ontology_id, relations) if relations else 0
        # Publish the same selected samples into the canonical ontology plane.
        # Earlier code only wrote FalkorDB extraction fragments, which made the
        # ontology, entity catalogue and audit appear empty despite a completed
        # multimodal run.
        update_run(db, run, progress={"stage": "正式构建", "completed": completed, "total": len(media), "failed": failures})
        mapping = ((run.config or {}).get("mapping") or {}).get("ontology_mapping") or ((run.config or {}).get("mapping") or {})
        selected_sample_ids = sorted({str(item.sample_id) for item in media if item.sample_id})
        samples_by_id = {
            item.id: item
            for item in db.query(MultimodalSample)
            .filter(MultimodalSample.dataset_version_id == version.id)
            .filter(MultimodalSample.id.in_(selected_sample_ids) if selected_sample_ids else False)
            .all()
        }
        assets_by_sample: dict[str, list[dict[str, Any]]] = {}
        for item in media:
            if not item.sample_id:
                continue
            assets_by_sample.setdefault(item.sample_id, []).append({
                "id": item.id, "role": item.asset_role, "mime_type": item.mime_type,
                "checksum": item.checksum, "source_path": item.source_path,
            })
        canonical_instances = []
        for sample_id in selected_sample_ids:
            sample = samples_by_id.get(sample_id)
            if not sample:
                continue
            canonical_instances.append({
                "id": sample.id, "sample_id": sample.id, "sample_key": sample.sample_key,
                "scene_id": sample.scene_id, "label": sample.label, "labels": sample.labels or [],
                "metadata": sample.metadata_json or {}, "assets": assets_by_sample.get(sample.id, []),
            })
        dataset_record = db.query(Dataset).filter(Dataset.id == run.dataset_id).first()
        materialized = materialize_ontology(
            db,
            run=run,
            mapping=mapping,
            instances=canonical_instances,
            data_class="multimodal",
            source_file=version.source_path or version.storage_uri,
            source_version=str(version.version_no),
            instance_entity="MultimodalSample",
            model_name=None if privacy_level == "private" else MODEL_NAME,
            require_rule=IBADAS_DATASET_NAME.casefold() in str(dataset_record.name if dataset_record else "").casefold(),
        )
        # Asset records are first-class ontology instances as well.  Keeping
        # them separate from their parent sample lets the entity page and
        # inspector show actual RGB/depth/mask/point-cloud evidence rather
        # than only a nested JSON list on a sample row.
        asset_instances = [
            {**asset, "sample_id": sample_id}
            for sample_id, assets in assets_by_sample.items()
            for asset in assets
        ]
        if asset_instances:
            asset_materialized = materialize_ontology(
                db,
                run=run,
                mapping=mapping,
                instances=asset_instances,
                data_class="multimodal",
                source_file=version.source_path or version.storage_uri,
                source_version=str(version.version_no),
                instance_entity="MediaAsset",
                model_name=None if privacy_level == "private" else MODEL_NAME,
                require_rule=False,
            )
            materialized.instance_count += asset_materialized.instance_count
            materialized.evidence_count += asset_materialized.evidence_count
        revision_id = None
        try:
            from app.services.v2.revision_service import create_revision
            revision = create_revision(db, run.ontology_id, source_run_id=run.id, summary={"media_processed": len(media), "successful": completed, "failed": failures, "nodes_written": written_nodes, "edges_written": written_edges, "entity_types": materialized.entity_type_count, "instances": materialized.instance_count, "relations": materialized.relation_count, "logic_rules": materialized.rule_count, "privacy_level": privacy_level})
            revision_id = revision.id
            run.revision_id = revision.id
            attach_revision_to_materialized_rows(db, run=run, revision_id=revision.id)
            project = db.query(OntologyProject).filter(OntologyProject.id == run.ontology_id).first()
            if project:
                project.status = "created"
            db.commit()
        except Exception:
            # A graph build should remain inspectable even if an older
            # development database has not applied the revision migration.
            # The run records the omission rather than claiming a revision.
            db.rollback()
        try:
            from app.services.v2.audit_runner import queue_local_audit
            queue_local_audit(db, ontology_id=run.ontology_id, revision_id=revision_id, construction_run_id=run.id)
        except Exception:
            # The construction result remains valid when audit scheduling is
            # unavailable; the task status/API will surface that omission.
            db.rollback()
        update_run(db, run, status="completed" if completed else "failed", progress={"completed": len(media), "total": len(media), "failed": failures},
                   metrics={"media_processed": len(media), "successful": completed, "failed": failures, "nodes_written": written_nodes, "edges_written": written_edges, "entity_types": materialized.entity_type_count, "instances": materialized.instance_count, "relations": materialized.relation_count, "logic_rules": materialized.rule_count,
                            "extractor": "rule" if privacy_level == "private" else "llm", "model": None if privacy_level == "private" else MODEL_NAME, "confidence_method": "official_label_and_source_id" if privacy_level == "private" else "model_self_report", "privacy_level": privacy_level, "revision_id": revision_id},
                   error=("部分媒体处理失败" if failures and completed else ("全部媒体处理失败" if failures else None)))
    except Exception as exc:
        db.rollback()
        run = db.query(ConstructionRun).filter(ConstructionRun.id == run_id).first()
        if run:
            update_run(db, run, status="failed", error=str(exc)[:2000])
    finally:
        db.close()


def _call_minimax(config, raw: bytes, filename: str, prompt: str | None) -> str:
    import base64
    from app.services.llm_service import _call_llm
    call_kwargs = llm_call_kwargs(config)
    if not call_kwargs:
        raise RuntimeError("MiniMax M3 凭据无法解密")
    mime = mimetypes.guess_type(filename)[0] or "image/png"
    data_url = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
    messages = [
        {"role": "system", "content": "You are an industrial inspection evidence extractor. Return concise Markdown; never invent equipment IDs or relationships."},
        {"role": "user", "content": [{"type": "text", "text": prompt or f"提取 {filename} 中可见文本、缺陷标签、设备编号和定位信息；没有证据的字段写 unknown。"}, {"type": "image_url", "image_url": {"url": data_url}}]},
    ]
    return _call_llm(call_kwargs["provider"], call_kwargs["api_key"], call_kwargs["api_base"], call_kwargs["model"], messages, json_mode=False)


def _serialize(item: ExtractedFragment) -> dict:
    return {"id": item.id, "media_item_id": item.media_item_id, "dataset_version_id": item.dataset_version_id,
            "fragment_type": item.fragment_type, "content": item.content, "locator": item.locator or {},
            "extractor": item.extractor, "status": item.status, "error": item.error,
            "created_at": item.created_at.isoformat() if item.created_at else None}
