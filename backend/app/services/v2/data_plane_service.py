"""Shared execution context, result manifests and adapter capabilities."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.config import settings


class DataPlaneError(ValueError):
    def __init__(self, code: str, message: str, *, next_action: str | None = None, context_id: str | None = None, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.next_action = next_action
        self.context_id = context_id or str(uuid.uuid4())
        self.details = details or {}


@dataclass(frozen=True)
class ConsistencyRequest:
    mode: str = "live"  # live|pinned|snapshot
    snapshot_id: str | None = None
    allow_degraded: bool = False


@dataclass(frozen=True)
class ExecutionContext:
    ontology_id: str
    metadata_revision_id: str | None = None
    metadata_digest: str | None = None
    source_manifest_id: str | None = None
    source_version: str | None = None
    projection_id: str | None = None
    view_id: str | None = None
    snapshot_id: str | None = None
    scenario_id: str | None = None
    edit_context_id: str | None = None
    permission_digest: str | None = None
    executor_version: str = "ontology-workbench-v2"
    consistency: ConsistencyRequest = field(default_factory=ConsistencyRequest)

    @property
    def context_id(self) -> str:
        payload = json.dumps(asdict(self), ensure_ascii=False, sort_keys=True, default=str).encode()
        return hashlib.sha256(payload).hexdigest()[:32]


@dataclass(frozen=True)
class AdapterCapabilities:
    adapter: str
    adapter_version: str
    supports_live: bool = False
    supports_pinned: bool = False
    supports_snapshot: bool = False
    supports_object_filter: bool = False
    supports_field_filter: bool = False
    supports_temporal_facts: bool = False
    supports_aggregate: bool = False
    semantic_fallback_group: str | None = None


@dataclass
class ResultManifest:
    context: ExecutionContext
    adapter: str
    adapter_version: str
    status: str = "ready"  # ready|degraded|stale|expired|unavailable
    permission_digest: str | None = None
    redacted_fields: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    result_count: int | None = None
    generated_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "context_id": self.context.context_id,
            "ontology_id": self.context.ontology_id,
            "metadata_revision_id": self.context.metadata_revision_id,
            "metadata_digest": self.context.metadata_digest,
            "source_manifest_id": self.context.source_manifest_id,
            "source_version": self.context.source_version,
            "projection_id": self.context.projection_id,
            "view_id": self.context.view_id,
            "snapshot_id": self.context.snapshot_id,
            "scenario_id": self.context.scenario_id,
            "permission_digest": self.permission_digest or self.context.permission_digest,
            "executor_version": self.context.executor_version,
            "consistency": asdict(self.context.consistency),
            "adapter": self.adapter,
            "adapter_version": self.adapter_version,
            "status": self.status,
            "redacted_fields": sorted(set(self.redacted_fields)),
            "warnings": self.warnings,
            "result_count": self.result_count,
            "generated_at": self.generated_at,
        }


def build_context(ontology_id: str, *, metadata_revision_id: str | None = None, metadata_digest: str | None = None, permission_digest: str | None = None, **kwargs: Any) -> ExecutionContext:
    consistency = kwargs.pop("consistency", None)
    if isinstance(consistency, dict):
        consistency = ConsistencyRequest(**consistency)
    if consistency is None:
        consistency = ConsistencyRequest()
    if consistency.mode not in {"live", "pinned", "snapshot"}:
        raise DataPlaneError("INVALID_CONSISTENCY", "一致性模式必须是 live、pinned 或 snapshot")
    if consistency.mode == "snapshot" and not consistency.snapshot_id:
        raise DataPlaneError("SNAPSHOT_REQUIRED", "snapshot 模式必须提供 snapshot_id")
    return ExecutionContext(
        ontology_id=ontology_id,
        metadata_revision_id=metadata_revision_id,
        metadata_digest=metadata_digest,
        permission_digest=permission_digest,
        consistency=consistency,
        **kwargs,
    )


def check_capability(capabilities: AdapterCapabilities, context: ExecutionContext, *, operation: str = "read") -> None:
    supported = {
        "live": capabilities.supports_live,
        "pinned": capabilities.supports_pinned,
        "snapshot": capabilities.supports_snapshot,
    }.get(context.consistency.mode, False)
    if not supported:
        raise DataPlaneError(
            "CAPABILITY_UNSUPPORTED",
            f"adapter {capabilities.adapter} 不支持 {context.consistency.mode} 一致性读取",
            next_action="切换兼容的数据源或显式允许降级",
            context_id=context.context_id,
            details={"operation": operation, "adapter": capabilities.adapter},
        )


def make_manifest(context: ExecutionContext, capabilities: AdapterCapabilities, *, status: str = "ready", permission_digest: str | None = None, redacted_fields: list[str] | None = None, warnings: list[str] | None = None, result_count: int | None = None) -> ResultManifest:
    return ResultManifest(
        context=context,
        adapter=capabilities.adapter,
        adapter_version=capabilities.adapter_version,
        status=status,
        permission_digest=permission_digest,
        redacted_fields=redacted_fields or [],
        warnings=warnings or [],
        result_count=result_count,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


def encode_cursor(position: dict[str, Any], context: ExecutionContext) -> str:
    payload = {
        "position": position,
        "context_id": context.context_id,
        "metadata_digest": context.metadata_digest,
        "permission_digest": context.permission_digest,
        "view_id": context.view_id,
        "snapshot_id": context.snapshot_id,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.new(settings.secret_key.encode(), raw, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw + b"." + signature).decode().rstrip("=")


def decode_cursor(cursor: str, context: ExecutionContext) -> dict[str, Any]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        # HMAC-SHA256 is always 32 bytes. Split at the fixed separator
        # position; signature bytes are arbitrary binary and may themselves
        # contain a dot, so rsplit(b".", 1) is not safe here.
        separator = len(raw) - hashlib.sha256().digest_size - 1
        if separator < 0 or raw[separator:separator + 1] != b".":
            raise ValueError("cursor format")
        payload_raw = raw[:separator]
        signature = raw[separator + 1:]
        expected = hmac.new(settings.secret_key.encode(), payload_raw, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("signature")
        payload = json.loads(payload_raw.decode())
    except Exception as exc:
        raise DataPlaneError("INVALID_CURSOR", "分页游标无效或已损坏", next_action="重新开始分页") from exc
    if payload.get("context_id") != context.context_id or payload.get("metadata_digest") != context.metadata_digest or payload.get("permission_digest") != context.permission_digest or payload.get("view_id") != context.view_id or payload.get("snapshot_id") != context.snapshot_id:
        raise DataPlaneError("CONTEXT_MISMATCH", "分页游标与当前元数据、权限或数据视图不一致", next_action="重新开始分页", context_id=context.context_id)
    return dict(payload.get("position") or {})


def unavailable_manifest(context: ExecutionContext, reason: str, *, adapter: str = "none", adapter_version: str = "unknown") -> ResultManifest:
    return ResultManifest(context=context, adapter=adapter, adapter_version=adapter_version, status="unavailable", warnings=[reason])


__all__ = [
    "AdapterCapabilities",
    "ConsistencyRequest",
    "DataPlaneError",
    "ExecutionContext",
    "ResultManifest",
    "build_context",
    "check_capability",
    "make_manifest",
    "encode_cursor",
    "decode_cursor",
    "unavailable_manifest",
]
