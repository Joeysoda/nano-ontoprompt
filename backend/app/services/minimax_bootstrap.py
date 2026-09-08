"""Opt-in MiniMax M3 model bootstrap.

The API key is read only from the process environment and immediately stored
encrypted in ``model_configs``.  Neither the key nor the model-list response
is returned to callers or written to logs.
"""
from __future__ import annotations

import json
import os
import urllib.request
import uuid

from sqlalchemy.orm import Session

from app.models.model_config import ModelConfig
from app.models.user import User
from app.services.encryption_service import encrypt


MINIMAX_BASE = "https://api.minimaxi.com/v1"
MODEL_NAME = "MiniMax-M3"


def bootstrap_minimax_model(db: Session) -> dict:
    key = os.getenv("MINIMAX_API_KEY", "").strip()
    if not key:
        return {"configured": False, "available": False, "model": MODEL_NAME, "reason": "MINIMAX_API_KEY 未配置"}
    try:
        request = urllib.request.Request(
            f"{MINIMAX_BASE}/models",
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        model_ids = {str(item.get("id")) for item in (payload.get("data") or []) if isinstance(item, dict)}
        if MODEL_NAME not in model_ids:
            return {"configured": True, "available": False, "model": MODEL_NAME, "reason": "当前 Key 未返回 MiniMax-M3 权限"}
    except Exception:
        return {"configured": True, "available": False, "model": MODEL_NAME, "reason": "MiniMax 模型列表不可连接"}

    config = db.query(ModelConfig).filter(ModelConfig.name == "MiniMax M3 多模态").first()
    admin = db.query(User).filter_by(role="admin").first()
    if not admin:
        return {"configured": True, "available": False, "model": MODEL_NAME, "reason": "未找到管理员账号"}
    values = {
        "name": "MiniMax M3 多模态",
        "config_type": "llm",
        "provider": "compatible",
        "api_base": MINIMAX_BASE,
        "api_key_encrypted": encrypt(key),
        "models": [MODEL_NAME],
        "options": {
            "usage_tags": ["VLM提取", "多模态"],
            "modalities": ["text", "image", "document", "video"],
            "bootstrap": "model-list-verified",
        },
        "created_by": admin.id,
    }
    if config:
        for field, value in values.items():
            if field != "created_by":
                setattr(config, field, value)
    else:
        config = ModelConfig(id=str(uuid.uuid4()), **values)
        db.add(config)
    db.commit()
    return {"configured": True, "available": True, "model": MODEL_NAME, "model_id": config.id}
