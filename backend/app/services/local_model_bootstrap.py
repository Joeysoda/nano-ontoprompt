"""Register the local model slot without claiming that Ollama is running."""
from __future__ import annotations

import os
import uuid

from sqlalchemy.orm import Session

from app.models.model_config import ModelConfig
from app.models.user import User

LOCAL_MODEL_NAME = "qwen3.5:0.8b"
LOCAL_CONFIG_NAME = "Ollama 本地审查 · qwen3.5:0.8b"
LOCAL_API_BASE = "http://127.0.0.1:11434/v1"


def bootstrap_local_model_slot(db: Session) -> dict:
    admin = db.query(User).filter(User.role == "admin").first()
    if not admin:
        return {"configured": False, "model": LOCAL_MODEL_NAME}
    config = db.query(ModelConfig).filter(ModelConfig.name == LOCAL_CONFIG_NAME).first()
    values = {
        "name": LOCAL_CONFIG_NAME,
        "config_type": "llm",
        "provider": "ollama",
        "api_base": os.getenv("OLLAMA_API_BASE", LOCAL_API_BASE),
        "models": [LOCAL_MODEL_NAME],
        "options": {
            "local": True,
            "usage_tags": ["本体审查", "审查", "Ontology Mapping", "构建辅助"],
            "capabilities": ["audit", "build", "vision"],
            "probe_endpoint": "/api/tags",
            "verified": False,
        },
        "created_by": admin.id,
    }
    if config:
        for key, value in values.items():
            if key != "created_by":
                setattr(config, key, value)
    else:
        config = ModelConfig(id=str(uuid.uuid4()), api_key_encrypted="", **values)
        db.add(config)
    db.commit()
    return {"configured": True, "model": LOCAL_MODEL_NAME, "model_id": config.id, "api_base": config.api_base}
