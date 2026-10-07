"""Pydantic contracts for Plan A.

The service layer still accepts dictionaries for backwards compatibility with
old callers, but all new HTTP routes validate through these models first.  The
models deliberately allow extension keys in payload bags while keeping
operation, policy and migration envelopes typed and stable.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SemanticResourceKindContract = Literal[
    "object_type", "property", "interface", "interface_implementation", "link_type",
    "shared_property", "value_type", "value_type_version", "struct", "struct_field",
    "logic_rule", "source_mapping",
]
PolicyEffectContract = Literal["allow", "deny"]
PolicyScopeContract = Literal["ontology", "object_type", "property", "link"]


class SemanticResourcePayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    api_name: str | None = None
    display_name: str | None = None
    name_cn: str | None = None
    name_en: str | None = None
    description: str | None = None
    base_type: str | None = None
    type: str | None = None
    cardinality: str | None = None
    parent_resource_id: str | None = None
    source_resource_id: str | None = None
    target_resource_id: str | None = None
    source_name: str | None = None
    target_name: str | None = None
    direction: Literal["directed", "undirected"] | None = None
    source_field: str | None = None
    source_table: str | None = None
    source_dataset_id: str | None = None
    source_version_id: str | None = None
    mapped_resource_id: str | None = None
    mapping_kind: str | None = None
    mapping_status: Literal["candidate", "confirmed", "rejected"] | None = None
    evidence: dict[str, Any] | None = None
    unit: str | None = None
    is_identifier: bool | None = None
    is_required: bool | None = None
    is_array: bool | None = None
    constraints: dict[str, Any] | None = None
    provenance: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


class SemanticResourceContract(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    resource_id: str
    revision_id: str
    kind: str
    api_name: str
    display_name: str | None = None
    name_cn: str | None = None
    name_en: str | None = None
    description: str | None = None
    parent_resource_id: str | None = None
    source_resource_id: str | None = None
    target_resource_id: str | None = None
    source_name: str | None = None
    target_name: str | None = None
    direction: Literal["directed", "undirected"] | None = None
    interface_resource_id: str | None = None
    value_type_resource_id: str | None = None
    struct_resource_id: str | None = None
    base_type: str | None = None
    cardinality: str | None = None
    unit: str | None = None
    source_field: str | None = None
    is_identifier: bool
    is_required: bool
    is_array: bool
    constraints: dict[str, Any]
    provenance: dict[str, Any]
    metadata: dict[str, Any]


class LegacyCompatibilityContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entities: bool
    relations: bool
    writes_translated: bool
    deprecated: bool


class OntologySourceMappingContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    resource_id: str
    source_dataset_id: str | None = None
    source_version_id: str | None = None
    source_table: str | None = None
    source_field: str | None = None
    mapping_kind: str
    mapping_status: Literal["candidate", "confirmed", "rejected"]
    evidence: dict[str, Any]


class SemanticSchemaContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ontology_id: str
    revision_id: str
    revision_no: int | None = None
    metadata_digest: str | None = None
    schema_version: str
    resources: dict[str, list[SemanticResourceContract]]
    all_resources: list[SemanticResourceContract]
    source_mappings: list[OntologySourceMappingContract]
    counts: dict[str, int]
    legacy_compatibility: LegacyCompatibilityContract


class SemanticChangeRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    resource_kind: str | None = None
    target_kind: str | None = None
    kind: str | None = None
    operation: Literal["add", "update", "delete"]
    base_revision_id: str | None = None
    resource_id: str | None = None
    target_id: str | None = None
    payload: SemanticResourcePayload = Field(default_factory=SemanticResourcePayload)
    resource: SemanticResourcePayload | None = None

    @field_validator("resource_kind", "target_kind", "kind")
    @classmethod
    def strip_kind(cls, value: str | None) -> str | None:
        return value.strip().lower() if isinstance(value, str) else value


class PolicyConditionContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str | None = None
    property: str | None = None
    operator: Literal["equals", "=", "==", "not_equals", "!=", "in", "not_in", ">", ">=", "<", "<=", "range", "belongs_to", "属于", "≠", "≥", "≤"] = "equals"
    value: Any = None

    @field_validator("field", "property")
    @classmethod
    def non_empty_reference(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("field/property 不能为空")
        return value


class SecurityPolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    subject_kind: Literal["user", "role"] = "user"
    subject_id: str = Field(min_length=1, max_length=200)
    effect: Literal["allow", "deny"] = "allow"
    scope_kind: Literal["ontology", "object_type", "property", "link"] = "ontology"
    scope_id: str | None = None
    conditions: list[PolicyConditionContract] | dict[str, Any] = Field(default_factory=list)
    field_allowlist: list[str] = Field(default_factory=list, max_length=500)
    enabled: bool = True
    priority: int = Field(default=100, ge=0, le=100000)
    note: str | None = None


class SecurityPolicyPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    subject_kind: Literal["user", "role"] | None = None
    subject_id: str | None = Field(default=None, min_length=1, max_length=200)
    effect: Literal["allow", "deny"] | None = None
    scope_kind: Literal["ontology", "object_type", "property", "link"] | None = None
    scope_id: str | None = None
    conditions: list[PolicyConditionContract] | dict[str, Any] | None = None
    field_allowlist: list[str] | None = Field(default=None, max_length=500)
    enabled: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=100000)
    note: str | None = None


class SecurityPolicyContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    ontology_id: str
    revision_id: str | None = None
    name: str
    subject_kind: Literal["user", "role"]
    subject_id: str
    effect: Literal["allow", "deny"]
    scope_kind: Literal["ontology", "object_type", "property", "link"]
    scope_id: str | None = None
    conditions: list[dict[str, Any]] | dict[str, Any]
    field_allowlist: list[str]
    enabled: bool
    priority: int
    created_by: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    note: str | None = None


class PolicyEvaluateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    principal_id: str | None = None
    principal_role: str | None = None
    metadata_revision_id: str | None = None
    requested_fields: list[str] = Field(default_factory=list, max_length=500)
    object_type_id: str | None = None
    records: list[dict[str, Any]] = Field(default_factory=list, max_length=5000)


class ConsistencyRequestContract(BaseModel):
    mode: Literal["live", "pinned", "snapshot"] = "live"
    snapshot_id: str | None = None
    allow_degraded: bool = False


class AuthorizationContextContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ontology_id: str
    principal_id: str
    principal_role: str | None = None
    metadata_revision_id: str | None = None
    requested_fields: list[str] = Field(default_factory=list, max_length=500)
    object_type_id: str | None = None
    scope_kind: Literal["ontology", "object_type", "property", "link"] = "ontology"
    scope_id: str | None = None


class AuthorizationProjectionContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed: bool
    visible_object_ids: list[str] | None = None
    allowed_fields: list[str] | None = None
    allowed_fields_by_object: dict[str, list[str] | None] = Field(default_factory=dict)
    denied_fields: list[str] = Field(default_factory=list)
    denied_fields_by_object: dict[str, list[str]] = Field(default_factory=dict)
    policy_ids: list[str] = Field(default_factory=list)
    permission_digest: str
    reason: str | None = None


class AdapterCapabilitiesContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

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


class ExecutionContextContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    context_id: str
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
    executor_version: str
    consistency: ConsistencyRequestContract


class ResultManifestContract(BaseModel):
    model_config = ConfigDict(extra="allow")

    context_id: str
    ontology_id: str
    metadata_revision_id: str | None = None
    metadata_digest: str | None = None
    source_manifest_id: str | None = None
    source_version: str | None = None
    projection_id: str | None = None
    view_id: str | None = None
    snapshot_id: str | None = None
    scenario_id: str | None = None
    permission_digest: str | None = None
    executor_version: str
    consistency: ConsistencyRequestContract
    adapter: str
    adapter_version: str
    status: Literal["ready", "degraded", "stale", "expired", "unavailable"]
    redacted_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    result_count: int | None = None
    generated_at: str | None = None


class DataPlaneProjectionContract(BaseModel):
    model_config = ConfigDict(extra="allow")

    adapter: str
    status: str
    authoritative: bool | None = None
    graph_namespace: str | None = None
    counts: dict[str, int] | None = None
    semantic_fallback_group: str | None = None


class DataPlaneStatusContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ontology_id: str
    metadata_revision_id: str | None = None
    metadata_digest: str | None = None
    authoritative: str
    source_snapshot: dict[str, Any]
    projections: list[DataPlaneProjectionContract]
    fallback_policy: str
    execution_context: dict[str, Any]
    result_manifest: ResultManifestContract


class DataPlaneCapabilitiesContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ontology_id: str
    capabilities: list[AdapterCapabilitiesContract]
    adapters: dict[str, DataPlaneProjectionContract]
    fallback_policy: str


class SchemaMigrationInstructionStatusContract(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    sequence_no: int
    kind: str
    resource_id: str | None = None
    payload: dict[str, Any]
    status: str
    error: str | None = None


class SchemaMigrationRunContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    plan_id: str
    ontology_id: str
    status: str
    phase: str
    progress: int
    target_revision_id: str | None = None
    result: dict[str, Any]
    error: str | None = None
    created_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


class SchemaMigrationPlanContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    ontology_id: str
    base_revision_id: str
    target_revision_id: str | None = None
    status: str
    phase: str
    impact: dict[str, Any]
    shadow_namespace: str | None = None
    result: dict[str, Any]
    error: str | None = None
    created_by: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    completed_at: str | None = None
    instructions: list[SchemaMigrationInstructionStatusContract]
    run: SchemaMigrationRunContract | None = None


class MigrationInstructionContract(BaseModel):
    model_config = ConfigDict(extra="allow")

    kind: str
    resource_id: str | None = None
    target_id: str | None = None
    api_name: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class MigrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str | None = None
    base_revision_id: str | None = None
    instructions: list[MigrationInstructionContract] = Field(default_factory=list, max_length=500)
