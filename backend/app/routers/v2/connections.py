"""
v2 Connection 管理 API
POST   /api/v2/connections
GET    /api/v2/connections
GET    /api/v2/connections/{id}
POST   /api/v2/connections/{id}/test
DELETE /api/v2/connections/{id}
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Optional

from app.database import SessionLocal
from app.deps import get_current_user
from app.models.v2.connection import Connection
from app.models.v2.workbench_task import DataImportTask
from app.services.connection.registry import get_connector

router = APIRouter(dependencies=[Depends(get_current_user)])
data_imports_router = APIRouter(dependencies=[Depends(get_current_user)])

SUPPORTED_KINDS = {"file", "mysql", "postgres", "mongo", "rest"}
IMPORTABLE_KINDS = {"file", "mysql", "postgres"}


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Pydantic 模式 ─────────────────────────────────────────────

class ConnectionCreate(BaseModel):
    name: str
    kind: str  # file | mysql | postgres | mongo | rest
    config: dict  # 明文连接配置 (服务端加密)


class ConnectionResponse(BaseModel):
    id: str
    name: str
    kind: str
    status: str

    class Config:
        from_attributes = True


# ── 端点 ──────────────────────────────────────────────────────

@router.post("", response_model=ConnectionResponse, status_code=201)
def create_connection(body: ConnectionCreate, db: Session = Depends(get_db)):
    """创建连接。config 加密后存储。"""
    from app.services import encryption_service
    kind = _normalize_kind(body.kind)
    config = _normalize_config(body.config, kind)
    encrypted_config = {"_encrypted": encryption_service.encrypt(json.dumps(config))}

    conn = Connection(
        name=body.name,
        kind=kind,
        config=encrypted_config,
        status="inactive",
    )
    db.add(conn)
    db.commit()
    db.refresh(conn)
    return conn


@router.get("", response_model=list[ConnectionResponse])
def list_connections(db: Session = Depends(get_db)):
    return db.query(Connection).all()


@router.get("/templates")
def connection_templates():
    """返回可直接套用的连接示例；响应中不包含真实凭据。"""
    return {
        "templates": [
            {
                "kind": "file", "name": "本地文件 / File",
                "config": {"bucket": "raw-datasets", "prefix": "uploads/"},
                "fields": ["文件名", "CSV/TSV/Excel/JSON/Parquet"],
                "import_supported": True,
            },
            {
                "kind": "mysql", "name": "MySQL",
                "config": {"host": "127.0.0.1", "port": 3306, "user": "ontology_reader", "password": "", "database": "example"},
                "fields": ["主机", "端口", "用户", "密码", "数据库"],
                "import_supported": True,
            },
            {
                "kind": "postgres", "name": "PostgreSQL",
                "config": {"host": "127.0.0.1", "port": 5432, "user": "ontology_reader", "password": "", "database": "example"},
                "fields": ["主机", "端口", "用户", "密码", "数据库"],
                "import_supported": True,
            },
            {
                "kind": "mongo", "name": "MongoDB",
                "config": {"uri": "mongodb://127.0.0.1:27017", "database": "example", "collection": "records"},
                "fields": ["URI", "数据库", "集合"],
                "import_supported": False,
                "note": "本轮可测试、预览，暂不导入",
            },
            {
                "kind": "rest", "name": "REST API",
                "config": {
                    "base_url": "https://api.example.com/v1",
                    "endpoints": ["/records"],
                    "auth": {"type": "bearer", "token": ""},
                    "params": {},
                    "pagination": {"type": "page", "page_param": "page", "size_param": "page_size", "data_path": "data"},
                },
                "fields": ["base_url", "endpoints", "auth", "params", "pagination", "data_path"],
                "import_supported": False,
                "note": "本轮可测试、预览，暂不导入",
            },
        ]
    }


class TestConfigBody(BaseModel):
    type: str
    config: dict = {}


class PreviewBody(BaseModel):
    resource: str
    columns: list[str] = Field(default_factory=list, max_length=100)
    filters: list[dict] = Field(default_factory=list, max_length=20)
    limit: int = Field(default=25, ge=1, le=200)


class ImportBody(PreviewBody):
    dataset_name: str = Field(min_length=1, max_length=200)
    data_class: str = Field(default="regular", pattern="^(regular|temporal)$")
    privacy_level: str = Field(default="standard", pattern="^(standard|private)$")
    mode: str = Field(default="snapshot", pattern="^(snapshot|append)$")
    watermark_column: str | None = None
    dedupe_key: str | None = None


def _connection_or_404(connection_id: str, db: Session) -> Connection:
    conn = db.query(Connection).filter(Connection.id == connection_id).first()
    if not conn:
        raise HTTPException(status_code=404, detail="连接不存在")
    return conn


def _safe_error(exc: Exception) -> str:
    text = str(exc)
    text = re.sub(r"(password=)[^\s&]+", r"\1***", text, flags=re.IGNORECASE)
    text = re.sub(r"(://[^:/\s]+:)[^@/\s]+@", r"\1***@", text)
    return text[:500]


def _normalize_kind(kind: str) -> str:
    aliases = {"postgresql": "postgres", "mongodb": "mongo", "rest_api": "rest"}
    value = aliases.get(kind.lower().strip(), kind.lower().strip())
    if value not in SUPPORTED_KINDS:
        raise HTTPException(422, f"不支持的连接类型：{kind}")
    return value


def _normalize_config(raw_config: dict, kind: str) -> dict:
    config = dict(raw_config or {})
    if kind in {"mysql", "postgres"}:
        config = _build_db_config(config, kind)
    elif kind == "mongo":
        if not str(config.get("uri") or "").strip():
            raise HTTPException(422, "MongoDB 需要 URI")
        if not str(config.get("database") or "").strip():
            raise HTTPException(422, "MongoDB 需要数据库名")
    elif kind == "rest":
        # Accept the old url/headers shape once, then store only the canonical
        # REST contract used by the connector and settings page.
        legacy_url = str(config.pop("url", "") or "").strip()
        if legacy_url and not config.get("base_url"):
            from urllib.parse import urlsplit
            parsed = urlsplit(legacy_url)
            config["base_url"] = f"{parsed.scheme}://{parsed.netloc}"
            config["endpoints"] = [parsed.path or "/"]
        endpoints = config.get("endpoints") or []
        if isinstance(endpoints, str):
            endpoints = [item.strip() for item in endpoints.split(",") if item.strip()]
        if not str(config.get("base_url") or "").strip() or not endpoints:
            raise HTTPException(422, "REST API 需要 base_url 和至少一个 endpoint")
        config["endpoints"] = endpoints
        config["params"] = config.get("params") if isinstance(config.get("params"), dict) else {}
        config["auth"] = config.get("auth") if isinstance(config.get("auth"), dict) else {}
        pagination = config.get("pagination") if isinstance(config.get("pagination"), dict) else {}
        if config.get("data_path"):
            pagination["data_path"] = config.pop("data_path")
        config["pagination"] = pagination
    return config


def _connection_config(conn: Connection) -> dict:
    from app.services import encryption_service
    raw = (conn.config or {}).get("_encrypted", "")
    try:
        return json.loads(encryption_service.decrypt(raw)) if raw else dict(conn.config or {})
    except Exception as exc:
        raise HTTPException(409, "连接配置无法解密；请在设置页重新保存连接") from exc


def _connector_for(conn: Connection):
    return get_connector(conn.kind, _connection_config(conn))


def _build_db_config(raw_config: dict, db_type: str) -> dict:
    """
    ConnectorInspector 发送单个字段（host/port/user/password/database）而非
    connection_string，这里组装成 SQLAlchemy 可用的连接 URL。
    密码中的特殊字符通过 urllib.parse.quote 编码以避免 URL 解析歧义。
    """
    from urllib.parse import quote
    host = raw_config.get("host", "localhost")
    port = raw_config.get("port", "3306" if db_type == "mysql" else "5432")
    user = raw_config.get("user", "")
    password = raw_config.get("password", "")
    database = raw_config.get("database", "")
    # 密码/用户名/库名中的特殊字符（如 @ : / # 空格等）必须 URL 编码，
    # 否则 SQLAlchemy 的 URL 解析器会将 @ 等视为 URL 结构分隔符而非密码的一部分。
    # host 不编码（IPv6 地址用 [] 括起，需原样保留）。
    scheme = "mysql+pymysql" if db_type == "mysql" else "postgresql"
    conn_str = f"{scheme}://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}/{quote(database, safe='')}"
    config = dict(raw_config)
    config["connection_string"] = conn_str
    return config


@router.post("/test-config")
def test_connection_config(body: TestConfigBody):
    """测试连接配置（无需先创建 Connection，供 Builder 使用）"""
    try:
        kind = _normalize_kind(body.type)
        cfg = _normalize_config(body.config, kind)
        connector = get_connector(kind, cfg)
        ok = connector.test_connection()
        return {"success": ok}
    except Exception as e:
        return {"success": False, "detail": _safe_error(e)}


@router.get("/{connection_id}", response_model=ConnectionResponse)
def get_connection(connection_id: str, db: Session = Depends(get_db)):
    return _connection_or_404(connection_id, db)


@router.get("/{connection_id}/resources")
def list_connection_resources(connection_id: str, db: Session = Depends(get_db)):
    """列出资源及字段。SQL 资源会额外返回字段类型，其他连接返回端点/集合。"""
    conn = _connection_or_404(connection_id, db)
    try:
        connector = _connector_for(conn)
        resources = connector.list_resources()
        if conn.kind in {"mysql", "postgres"}:
            return {
                "connection_id": connection_id,
                "resources": [{"name": name, "columns": connector.describe_resource(name)} for name in resources],
            }
        return {"connection_id": connection_id, "resources": [{"name": name} for name in resources]}
    except Exception as exc:
        raise HTTPException(422, {"code": "CONNECTION_RESOURCES_FAILED", "message": _safe_error(exc)}) from exc


@router.post("/{connection_id}/preview")
def preview_connection(connection_id: str, body: PreviewBody, db: Session = Depends(get_db)):
    """按资源、列和结构化筛选预览数据，不接受任意 SQL。"""
    conn = _connection_or_404(connection_id, db)
    try:
        connector = _connector_for(conn)
        if conn.kind in {"mysql", "postgres"}:
            rows = connector.query_rows(body.resource, columns=body.columns or None, filters=body.filters, limit=body.limit)
            total_estimate = connector.estimate_rows(body.resource, filters=body.filters)
        else:
            rows = connector.pull_sample(body.resource, limit=body.limit)
            total_estimate = None
        return {"connection_id": connection_id, "resource": body.resource, "rows": rows, "count": len(rows), "total_estimate": total_estimate}
    except Exception as exc:
        raise HTTPException(422, {"code": "CONNECTION_PREVIEW_FAILED", "message": _safe_error(exc), "next": "检查连接、资源名称和字段筛选"}) from exc


@router.post("/{connection_id}/imports", status_code=202)
def create_data_import(connection_id: str, body: ImportBody, db: Session = Depends(get_db)):
    """创建统一数据导入任务。MongoDB/REST 本轮只支持测试与预览。"""
    conn = _connection_or_404(connection_id, db)
    if conn.kind not in IMPORTABLE_KINDS:
        raise HTTPException(422, "MongoDB/REST 本轮可测试、预览，暂不导入")
    if body.mode == "append" and (not body.watermark_column or not body.dedupe_key):
        raise HTTPException(422, "增量导入需要配置水位列和去重键")
    if conn.kind in {"mysql", "postgres"}:
        # Validate identifiers before a worker is queued. The worker repeats
        # this check because its process may outlive this request.
        from app.services.connection.sql_connector import _validate_identifier
        _validate_identifier(body.resource, "resource")
        for column in body.columns:
            _validate_identifier(column, "column")
        for item in body.filters:
            _validate_identifier(str(item.get("column") or ""), "filter column")
        if body.watermark_column:
            _validate_identifier(body.watermark_column, "watermark_column")
        if body.dedupe_key:
            _validate_identifier(body.dedupe_key, "dedupe_key")
    task = DataImportTask(
        connection_id=connection_id,
        data_class=body.data_class,
        status="queued",
        config_json=body.model_dump(),
        progress={"stage": "queued", "percent": 0},
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    try:
        from app.tasks.v2.connection_sync import run_data_import_task
        run_data_import_task.delay(task.id)
    except Exception as exc:
        task.status = "failed"
        task.error = f"任务派发失败：{_safe_error(exc)}"
        db.commit()
        raise HTTPException(503, {"code": "IMPORT_QUEUE_UNAVAILABLE", "message": task.error, "next": "启动 Celery worker 后重试"}) from exc
    return {"id": task.id, "status": task.status, "progress": task.progress}


@router.post("/{connection_id}/test")
def test_connection(connection_id: str, db: Session = Depends(get_db)):
    """连接测试。尝试真实连接并返回结果。"""
    conn = _connection_or_404(connection_id, db)
    try:
        connector = _connector_for(conn)
        ok = connector.test_connection()
        conn.status = "active" if ok else "error"
        db.commit()
        return {"success": ok, "status": conn.status}
    except Exception as e:
        conn.status = "error"
        db.commit()
        return {"success": False, "status": "error", "detail": _safe_error(e)}


@router.delete("/{connection_id}", status_code=204)
def delete_connection(connection_id: str, db: Session = Depends(get_db)):
    conn = db.query(Connection).filter(Connection.id == connection_id).first()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    db.delete(conn)
    db.commit()


@router.post("/{connection_id}/schedule")
def set_schedule(connection_id: str, cron_expr: str, db: Session = Depends(get_db)):
    """为连接设置 Cron 调度表达式"""
    from app.services.v2.scheduler.cron_service import CronService
    svc = CronService()
    if not svc.validate_cron(cron_expr):
        raise HTTPException(400, f"无效的 cron 表达式: {cron_expr}")

    conn = db.query(Connection).filter(Connection.id == connection_id).first()
    if not conn:
        raise HTTPException(404, "Connection not found")

    result = svc.schedule_connection_sync(connection_id, cron_expr)
    config = conn.config or {}
    config["schedule_cron"] = cron_expr
    conn.config = config
    db.commit()
    return result


@router.post("/{connection_id}/sync")
def trigger_sync(connection_id: str, db: Session = Depends(get_db)):
    """手动触发同步必须有已保存的资源选择，避免空实现伪装成功。"""
    conn = _connection_or_404(connection_id, db)
    latest = db.query(DataImportTask).filter(DataImportTask.connection_id == connection_id).order_by(DataImportTask.created_at.desc()).first()
    if not latest:
        raise HTTPException(422, "尚未配置资源范围；请先在资源预览中选择表/集合并点击导入")
    if latest.status in {"queued", "running"}:
        return {"connection_id": connection_id, "status": latest.status, "task_id": latest.id}
    try:
        from app.tasks.v2.connection_sync import run_data_import_task
        latest.status = "queued"
        latest.error = None
        latest.cancel_requested = False
        db.commit()
        run_data_import_task.delay(latest.id)
        return {"connection_id": connection_id, "status": "sync_triggered", "task_id": latest.id}
    except Exception as exc:
        latest.status = "failed"
        latest.error = f"任务派发失败：{_safe_error(exc)}"
        db.commit()
        raise HTTPException(503, {"code": "IMPORT_QUEUE_UNAVAILABLE", "message": latest.error}) from exc


@data_imports_router.get("/data-imports/{task_id}")
def get_data_import(task_id: str, db: Session = Depends(get_db)):
    task = db.query(DataImportTask).filter(DataImportTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "导入任务不存在")
    return {
        "id": task.id, "connection_id": task.connection_id, "dataset_id": task.dataset_id,
        "data_class": task.data_class, "status": task.status, "progress": task.progress,
        "error": task.error, "cancel_requested": task.cancel_requested,
    }


@data_imports_router.post("/data-imports/{task_id}/retry")
def retry_data_import(task_id: str, db: Session = Depends(get_db)):
    task = db.query(DataImportTask).filter(DataImportTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "导入任务不存在")
    if task.status in {"queued", "running"}:
        return {"id": task.id, "status": task.status}
    task.status = "queued"
    task.error = None
    task.cancel_requested = False
    task.progress = {"stage": "queued", "percent": 0}
    db.commit()
    try:
        from app.tasks.v2.connection_sync import run_data_import_task
        run_data_import_task.delay(task.id)
    except Exception as exc:
        task.status = "failed"
        task.error = f"任务派发失败：{_safe_error(exc)}"
        db.commit()
        raise HTTPException(503, {"code": "IMPORT_QUEUE_UNAVAILABLE", "message": task.error}) from exc
    return {"id": task.id, "status": task.status}


@data_imports_router.post("/data-imports/{task_id}/cancel")
def cancel_data_import(task_id: str, db: Session = Depends(get_db)):
    task = db.query(DataImportTask).filter(DataImportTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "导入任务不存在")
    if task.status in {"completed", "failed", "cancelled"}:
        return {"id": task.id, "status": task.status}
    task.cancel_requested = True
    db.commit()
    return {"id": task.id, "status": task.status, "cancel_requested": True}
