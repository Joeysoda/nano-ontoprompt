"""Single model gateway facade used by workbench call sites."""
from __future__ import annotations

from app.services.model_config_selector import llm_call_kwargs


class ModelGateway:
    """Route a visible request through the configured LiteLLM endpoint."""

    def __init__(self, model_config):
        self.model_config = model_config

    @property
    def kwargs(self) -> dict | None:
        return llm_call_kwargs(self.model_config)

    def call(self, messages: list[dict], *, json_mode: bool = True) -> str:
        kwargs = self.kwargs
        if not kwargs:
            raise RuntimeError("模型未配置或凭据无法解密")
        from app.services.llm_service import _call_llm
        return _call_llm(**kwargs, messages=messages, json_mode=json_mode)
