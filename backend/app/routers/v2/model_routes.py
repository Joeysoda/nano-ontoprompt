"""Model route health without conflating configuration and availability."""
from __future__ import annotations

import json
import os
import urllib.request

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.deps import get_current_user, require_admin
from app.models.model_config import ModelConfig
from app.models.v2.workbench_task import ModelInvocation
from app.services import encryption_service

router = APIRouter(dependencies=[Depends(get_current_user)])
invocations_router = APIRouter(dependencies=[Depends(get_current_user)])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _get_json(url: str, *, headers: dict[str, str] | None = None, timeout: float = 3) -> tuple[bool, object, str | None]:
    try:
        request = urllib.request.Request(url, headers=headers or {"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return True, json.loads(response.read().decode("utf-8")), None
    except Exception as exc:
        return False, None, str(exc)[:240]


def _post_json(url: str, payload: dict, *, headers: dict[str, str], timeout: float = 20) -> tuple[bool, object, str | None]:
    try:
        request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={**headers, "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return True, json.loads(response.read().decode("utf-8")), None
    except Exception as exc:
        return False, None, str(exc)[:240]


@router.get("/status")
def model_routes_status(
    probe: bool = False,
    probe_local: bool = False,
    probe_cloud: bool = False,
    db: Session = Depends(get_db),
):
    configs = db.query(ModelConfig).filter(ModelConfig.config_type == "llm").order_by(ModelConfig.updated_at.desc()).all()
    by_model: dict[str, ModelConfig] = {}
    for config in configs:
        for name in config.models or []:
            alias = str(name)
            current = by_model.get(alias)
            # Older migrations left several compatible Ollama slots behind.
            # Prefer an explicitly local slot for the qwen route so the
            # status probe reflects the actual Ollama upstream rather than a
            # hidden legacy placeholder.
            if current is None or (alias == "qwen3.5:0.8b" and str(config.provider or "").lower() in {"ollama", "local"} and str(current.provider or "").lower() not in {"ollama", "local"}):
                by_model[alias] = config

    gateway_base = os.getenv("LITELLM_API_BASE", "").strip().rstrip("/")
    gateway_key = os.getenv("LITELLM_API_KEY", "").strip() or "local-gateway"
    gateway_reachable = False
    gateway_models: list[str] = []
    gateway_error = None
    if gateway_base:
        ok, payload, error = _get_json(f"{gateway_base}/models", headers={"Authorization": f"Bearer {gateway_key}", "Accept": "application/json"})
        gateway_reachable = ok
        gateway_error = error
        if isinstance(payload, dict):
            gateway_models = [str(item.get("id")) for item in payload.get("data", []) if isinstance(item, dict) and item.get("id")]

    routes = []
    for alias in ("MiniMax-M3", "qwen3.5:0.8b"):
        config = by_model.get(alias)
        configured = bool(config)
        gateway_ok = bool(gateway_reachable and alias in gateway_models) if gateway_base else False
        upstream_ok = False
        upstream_error = None
        is_local_route = bool(config and str(config.provider or "").lower() in {"ollama", "local"})
        route_was_probed = bool(
            ((probe or probe_local) and is_local_route)
            or (probe_cloud and alias == "MiniMax-M3")
        )
        # The settings page may safely refresh a local Ollama probe.  Do not
        # make a cloud provider request merely because the page was opened:
        # probing M3 is an explicit, separately scoped action.
        if (probe or probe_local) and config and str(config.provider or "").lower() in {"ollama", "local"}:
            base = (config.api_base or "http://127.0.0.1:11434/v1").rstrip("/")
            root = base[:-3] if base.endswith("/v1") else base
            ok, payload, error = _get_json(f"{root}/api/tags")
            names = [str(item.get("name") or item.get("model")) for item in (payload.get("models", []) if isinstance(payload, dict) else []) if isinstance(item, dict)]
            upstream_ok = ok and any(name == alias or name.startswith(f"{alias}:") for name in names)
            upstream_error = error if not upstream_ok else None
        elif probe_cloud and config and alias == "MiniMax-M3" and gateway_base and gateway_ok:
            # A model-list response only proves that LiteLLM is up.  For the
            # cloud route, use a tiny explicit probe so the UI does not call a
            # configured-but-unauthorized model "可用".
            ok, _, error = _post_json(
                f"{gateway_base}/chat/completions",
                {"model": alias, "messages": [{"role": "user", "content": "Reply OK."}], "max_completion_tokens": 32, "temperature": 0},
                headers={"Authorization": f"Bearer {gateway_key}"},
                timeout=30,
            )
            upstream_ok = ok
            upstream_error = error if not upstream_ok else None
        routes.append({
            "alias": alias,
            "purpose": "build" if alias == "MiniMax-M3" else "audit/build/vision",
            "configured": configured,
            "gateway_reachable": gateway_ok,
            "upstream_authorized": upstream_ok if route_was_probed else None,
            # Configuration and a model-list response are not proof that an
            # upstream route can answer requests.  Keep the public status
            # conservative: only an explicit probe may mark a route usable.
            "available": bool(route_was_probed and configured and gateway_ok and upstream_ok),
            "provider": config.provider if config else None,
            "error": upstream_error or (gateway_error if configured and gateway_base and not gateway_ok else None),
        })
    return {"gateway": {"configured": bool(gateway_base), "reachable": gateway_reachable, "models": gateway_models, "error": gateway_error}, "routes": routes}


def _serialize_invocation(item: ModelInvocation) -> dict:
    def decrypt(value: str | None) -> str | None:
        if not value:
            return None
        try:
            return encryption_service.decrypt(value)
        except Exception:
            return None

    return {
        "id": item.id,
        "draft_id": item.draft_id,
        "construction_run_id": item.construction_run_id,
        "audit_task_id": item.audit_task_id,
        "mapping_task_id": item.mapping_task_id,
        "route_alias": item.route_alias,
        "provider": item.provider,
        "model_name": item.model_name,
        "status": item.status,
        "request": decrypt(item.request_ciphertext),
        "response": decrypt(item.response_ciphertext),
        "request_hash": item.request_hash,
        "response_hash": item.response_hash,
        "metadata": item.metadata_json or {},
        "duration_ms": item.duration_ms,
        "error": item.error,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "completed_at": item.completed_at.isoformat() if item.completed_at else None,
    }


@invocations_router.get("/model-invocations")
def list_model_invocations(
    draft_id: str | None = None,
    model: str | None = None,
    status: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    query = db.query(ModelInvocation)
    if draft_id:
        query = query.filter(ModelInvocation.draft_id == draft_id)
    if model:
        query = query.filter(ModelInvocation.model_name == model)
    if status:
        query = query.filter(ModelInvocation.status == status)
    items = query.order_by(ModelInvocation.created_at.desc()).limit(limit).all()
    return {"items": [_serialize_invocation(item) for item in items], "count": len(items)}


@invocations_router.delete("/model-invocations/{invocation_id}")
def delete_model_invocation(
    invocation_id: str,
    confirm: bool = Query(False),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
):
    if not confirm:
        raise HTTPException(400, "删除模型调用日志需要 confirm=true")
    item = db.query(ModelInvocation).filter(ModelInvocation.id == invocation_id).first()
    if not item:
        raise HTTPException(404, "模型调用日志不存在")
    db.delete(item)
    db.commit()
    return {"deleted": invocation_id}
