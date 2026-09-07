"""Celery tasks for validated connection imports.

The old implementation was an empty stub.  The browser submits a resource
name and structured filters; the worker re-validates them, pulls records
through a connector and materialises a complete DatasetVersion.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from app.database import SessionLocal
from app.models.v2.connection import Connection
from app.models.v2.dataset import Dataset
from app.models.v2.workbench_task import DataImportTask
from app.services import encryption_service
from app.services.connection.registry import get_connector
from app.services.v2.dataset_service import DatasetService
from app.tasks.celery_app import celery_app


def _set_progress(db, task: DataImportTask, stage: str, percent: int, **extra) -> None:
    task.progress = {"stage": stage, "percent": max(0, min(100, percent)), **extra}
    db.commit()


def _load_config(connection: Connection) -> dict:
    raw = (connection.config or {}).get("_encrypted")
    if raw:
        return json.loads(encryption_service.decrypt(raw))
    return dict(connection.config or {})


def _json_bytes(records) -> bytes:
    if isinstance(records, bytes):
        return records
    return json.dumps(records, ensure_ascii=False, default=str).encode("utf-8")


def _run_data_import(task_id: str) -> dict:
    db = SessionLocal()
    task = None
    try:
        task = db.query(DataImportTask).filter(DataImportTask.id == task_id).first()
        if not task:
            return {"status": "missing", "task_id": task_id}
        if task.cancel_requested:
            task.status = "cancelled"
            db.commit()
            return {"status": "cancelled", "task_id": task_id}

        task.status = "running"
        task.error = None
        _set_progress(db, task, "读取连接配置", 10)
        connection = db.query(Connection).filter(Connection.id == task.connection_id).first()
        if not connection:
            raise ValueError("连接不存在")
        if connection.kind not in {"file", "mysql", "postgres"}:
            raise ValueError("该连接类型本轮只支持测试与预览，暂不导入")
        cfg = _load_config(connection)
        connector = get_connector(connection.kind, cfg)
        spec = dict(task.config_json or {})
        resource = str(spec.get("resource") or "").strip()
        if not resource:
            raise ValueError("未选择资源；请先选择表、文件或资源路径")

        if task.cancel_requested:
            task.status = "cancelled"
            db.commit()
            return {"status": "cancelled", "task_id": task_id}
        _set_progress(db, task, "读取并校验资源", 35, resource=resource)
        if connection.kind in {"mysql", "postgres"}:
            from app.services.connection.sql_connector import _validate_identifier
            _validate_identifier(resource, "resource")
            columns = spec.get("columns") or None
            filters = spec.get("filters") or []
            rows = connector.query_rows(resource, columns=columns, filters=filters, limit=None)
        else:
            rows = connector.pull_full(resource)
        if task.cancel_requested:
            task.status = "cancelled"
            db.commit()
            return {"status": "cancelled", "task_id": task_id}

        payload = _json_bytes(rows)
        _set_progress(db, task, "写入完整数据版本", 70, rowcount=len(rows) if isinstance(rows, list) else None)
        dataset = db.query(Dataset).filter(Dataset.id == task.dataset_id).first() if task.dataset_id else None
        if not dataset:
            dataset = DatasetService(db).create_dataset(
                str(spec.get("dataset_name") or f"{connection.name} / {resource}"),
                "structured",
                connection_id=connection.id,
                data_class=task.data_class,
                privacy_level=str(spec.get("privacy_level") or "standard"),
                schema_json={"source_resource": resource, "import_mode": spec.get("mode", "snapshot")},
            )
            task = db.query(DataImportTask).filter(DataImportTask.id == task_id).first()
            task.dataset_id = dataset.id
            db.commit()
        version = DatasetService(db).create_version(dataset.id, payload, len(rows) if isinstance(rows, list) else None)
        connection.status = "active"
        connection.last_sync_at = datetime.now(timezone.utc)
        task.status = "completed"
        task.progress = {"stage": "完成", "percent": 100, "dataset_id": dataset.id, "version_id": version.id, "rowcount": version.rowcount}
        task.error = None
        db.commit()
        return {"status": "completed", "task_id": task_id, "dataset_id": dataset.id, "version_id": version.id}
    except Exception as exc:
        if task is not None:
            task.status = "failed"
            task.error = str(exc)[:1000]
            task.progress = {**(task.progress or {}), "stage": "失败", "percent": task.progress.get("percent", 0) if task.progress else 0}
            db.commit()
        return {"status": "failed", "task_id": task_id, "error": str(exc)[:1000]}
    finally:
        db.close()


@celery_app.task(name="v2.connection_data_import")
def run_data_import_task(task_id: str) -> dict:
    return _run_data_import(task_id)


def sync_connection(connection_id: str, mode: str = "full") -> dict:
    """Compatibility helper used by older callers and beat jobs."""
    db = SessionLocal()
    try:
        task = db.query(DataImportTask).filter(DataImportTask.connection_id == connection_id).order_by(DataImportTask.created_at.desc()).first()
        if not task:
            return {"status": "not_configured", "connection_id": connection_id}
        if mode == "delta":
            spec = dict(task.config_json or {})
            spec["mode"] = "append"
            task.config_json = spec
            task.status = "queued"
            task.cancel_requested = False
            db.commit()
        return _run_data_import(task.id)
    finally:
        db.close()


def sync_all_connections() -> list[dict]:
    db = SessionLocal()
    try:
        ids = [item.id for item in db.query(Connection).filter(Connection.status == "active").all()]
    finally:
        db.close()
    return [sync_connection(connection_id) for connection_id in ids]
