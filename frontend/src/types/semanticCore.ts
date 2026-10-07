/** Generated from backend/app/schemas/v2/semantic_core.py. Do not edit by hand. */

export type SemanticResourceKindContract = "object_type" | "property" | "interface" | "interface_implementation" | "link_type" | "shared_property" | "value_type" | "value_type_version" | "struct" | "struct_field" | "logic_rule" | "source_mapping";

export type PolicyEffectContract = "allow" | "deny";

export type PolicyScopeContract = "ontology" | "object_type" | "property" | "link";

export interface SemanticResourcePayload {
  api_name?: string | null
  display_name?: string | null
  name_cn?: string | null
  name_en?: string | null
  description?: string | null
  base_type?: string | null
  type?: string | null
  cardinality?: string | null
  parent_resource_id?: string | null
  source_resource_id?: string | null
  target_resource_id?: string | null
  source_name?: string | null
  target_name?: string | null
  direction?: "directed" | "undirected" | null
  source_field?: string | null
  source_table?: string | null
  source_dataset_id?: string | null
  source_version_id?: string | null
  mapped_resource_id?: string | null
  mapping_kind?: string | null
  mapping_status?: "candidate" | "confirmed" | "rejected" | null
  evidence?: Record<string, unknown> | null
  unit?: string | null
  is_identifier?: boolean | null
  is_required?: boolean | null
  is_array?: boolean | null
  constraints?: Record<string, unknown> | null
  provenance?: Record<string, unknown> | null
  metadata?: Record<string, unknown> | null
  [key: string]: unknown
}

export interface SemanticResourceContract {
  id: string
  resource_id: string
  revision_id: string
  kind: string
  api_name: string
  display_name?: string | null
  name_cn?: string | null
  name_en?: string | null
  description?: string | null
  parent_resource_id?: string | null
  source_resource_id?: string | null
  target_resource_id?: string | null
  source_name?: string | null
  target_name?: string | null
  direction?: "directed" | "undirected" | null
  interface_resource_id?: string | null
  value_type_resource_id?: string | null
  struct_resource_id?: string | null
  base_type?: string | null
  cardinality?: string | null
  unit?: string | null
  source_field?: string | null
  is_identifier: boolean
  is_required: boolean
  is_array: boolean
  constraints: Record<string, unknown>
  provenance: Record<string, unknown>
  metadata: Record<string, unknown>
  [key: string]: unknown
}

export interface LegacyCompatibilityContract {
  entities: boolean
  relations: boolean
  writes_translated: boolean
  deprecated: boolean
}

export interface OntologySourceMappingContract {
  id: string
  resource_id: string
  source_dataset_id?: string | null
  source_version_id?: string | null
  source_table?: string | null
  source_field?: string | null
  mapping_kind: string
  mapping_status: "candidate" | "confirmed" | "rejected"
  evidence: Record<string, unknown>
}

export interface SemanticSchemaContract {
  ontology_id: string
  revision_id: string
  revision_no?: number | null
  metadata_digest?: string | null
  schema_version: string
  resources: Record<string, Array<SemanticResourceContract>>
  all_resources: Array<SemanticResourceContract>
  source_mappings: Array<OntologySourceMappingContract>
  counts: Record<string, number>
  legacy_compatibility: LegacyCompatibilityContract
}

export interface SemanticChangeRequest {
  resource_kind?: string | null
  target_kind?: string | null
  kind?: string | null
  operation: "add" | "update" | "delete"
  base_revision_id?: string | null
  resource_id?: string | null
  target_id?: string | null
  payload?: SemanticResourcePayload
  resource?: SemanticResourcePayload | null
  [key: string]: unknown
}

export interface PolicyConditionContract {
  field?: string | null
  property?: string | null
  operator?: "equals" | "=" | "==" | "not_equals" | "!=" | "in" | "not_in" | ">" | ">=" | "<" | "<=" | "range" | "belongs_to" | "属于" | "≠" | "≥" | "≤"
  value?: unknown
}

export interface SecurityPolicyRequest {
  name: string
  subject_kind?: "user" | "role"
  subject_id: string
  effect?: "allow" | "deny"
  scope_kind?: "ontology" | "object_type" | "property" | "link"
  scope_id?: string | null
  conditions?: Array<PolicyConditionContract> | Record<string, unknown>
  field_allowlist?: Array<string>
  enabled?: boolean
  priority?: number
  note?: string | null
}

export interface SecurityPolicyPatch {
  name?: string | null
  subject_kind?: "user" | "role" | null
  subject_id?: string | null
  effect?: "allow" | "deny" | null
  scope_kind?: "ontology" | "object_type" | "property" | "link" | null
  scope_id?: string | null
  conditions?: Array<PolicyConditionContract> | Record<string, unknown> | null
  field_allowlist?: Array<string> | null
  enabled?: boolean | null
  priority?: number | null
  note?: string | null
}

export interface SecurityPolicyContract {
  id: string
  ontology_id: string
  revision_id?: string | null
  name: string
  subject_kind: "user" | "role"
  subject_id: string
  effect: "allow" | "deny"
  scope_kind: "ontology" | "object_type" | "property" | "link"
  scope_id?: string | null
  conditions: Array<Record<string, unknown>> | Record<string, unknown>
  field_allowlist: Array<string>
  enabled: boolean
  priority: number
  created_by?: string | null
  created_at?: string | null
  updated_at?: string | null
  note?: string | null
}

export interface PolicyEvaluateRequest {
  principal_id?: string | null
  principal_role?: string | null
  metadata_revision_id?: string | null
  requested_fields?: Array<string>
  object_type_id?: string | null
  records?: Array<Record<string, unknown>>
}

export interface ConsistencyRequestContract {
  mode?: "live" | "pinned" | "snapshot"
  snapshot_id?: string | null
  allow_degraded?: boolean
}

export interface AuthorizationContextContract {
  ontology_id: string
  principal_id: string
  principal_role?: string | null
  metadata_revision_id?: string | null
  requested_fields?: Array<string>
  object_type_id?: string | null
  scope_kind?: "ontology" | "object_type" | "property" | "link"
  scope_id?: string | null
}

export interface AuthorizationProjectionContract {
  allowed: boolean
  visible_object_ids?: Array<string> | null
  allowed_fields?: Array<string> | null
  allowed_fields_by_object?: Record<string, Array<string> | null>
  denied_fields?: Array<string>
  denied_fields_by_object?: Record<string, Array<string>>
  policy_ids?: Array<string>
  permission_digest: string
  reason?: string | null
}

export interface AdapterCapabilitiesContract {
  adapter: string
  adapter_version: string
  supports_live?: boolean
  supports_pinned?: boolean
  supports_snapshot?: boolean
  supports_object_filter?: boolean
  supports_field_filter?: boolean
  supports_temporal_facts?: boolean
  supports_aggregate?: boolean
  semantic_fallback_group?: string | null
}

export interface ExecutionContextContract {
  context_id: string
  ontology_id: string
  metadata_revision_id?: string | null
  metadata_digest?: string | null
  source_manifest_id?: string | null
  source_version?: string | null
  projection_id?: string | null
  view_id?: string | null
  snapshot_id?: string | null
  scenario_id?: string | null
  edit_context_id?: string | null
  permission_digest?: string | null
  executor_version: string
  consistency: ConsistencyRequestContract
}

export interface ResultManifestContract {
  context_id: string
  ontology_id: string
  metadata_revision_id?: string | null
  metadata_digest?: string | null
  source_manifest_id?: string | null
  source_version?: string | null
  projection_id?: string | null
  view_id?: string | null
  snapshot_id?: string | null
  scenario_id?: string | null
  permission_digest?: string | null
  executor_version: string
  consistency: ConsistencyRequestContract
  adapter: string
  adapter_version: string
  status: "ready" | "degraded" | "stale" | "expired" | "unavailable"
  redacted_fields?: Array<string>
  warnings?: Array<string>
  result_count?: number | null
  generated_at?: string | null
  [key: string]: unknown
}

export interface DataPlaneProjectionContract {
  adapter: string
  status: string
  authoritative?: boolean | null
  graph_namespace?: string | null
  counts?: Record<string, number> | null
  semantic_fallback_group?: string | null
  [key: string]: unknown
}

export interface DataPlaneStatusContract {
  ontology_id: string
  metadata_revision_id?: string | null
  metadata_digest?: string | null
  authoritative: string
  source_snapshot: Record<string, unknown>
  projections: Array<DataPlaneProjectionContract>
  fallback_policy: string
  execution_context: Record<string, unknown>
  result_manifest: ResultManifestContract
}

export interface DataPlaneCapabilitiesContract {
  ontology_id: string
  capabilities: Array<AdapterCapabilitiesContract>
  adapters: Record<string, DataPlaneProjectionContract>
  fallback_policy: string
}

export interface MigrationInstructionContract {
  kind: string
  resource_id?: string | null
  target_id?: string | null
  api_name?: string | null
  payload?: Record<string, unknown>
  [key: string]: unknown
}

export interface MigrationRequest {
  plan_id?: string | null
  base_revision_id?: string | null
  instructions?: Array<MigrationInstructionContract>
}

export interface SchemaMigrationInstructionStatusContract {
  id: string
  sequence_no: number
  kind: string
  resource_id?: string | null
  payload: Record<string, unknown>
  status: string
  error?: string | null
  [key: string]: unknown
}

export interface SchemaMigrationRunContract {
  id: string
  plan_id: string
  ontology_id: string
  status: string
  phase: string
  progress: number
  target_revision_id?: string | null
  result: Record<string, unknown>
  error?: string | null
  created_at?: string | null
  started_at?: string | null
  completed_at?: string | null
}

export interface SchemaMigrationPlanContract {
  id: string
  ontology_id: string
  base_revision_id: string
  target_revision_id?: string | null
  status: string
  phase: string
  impact: Record<string, unknown>
  shadow_namespace?: string | null
  result: Record<string, unknown>
  error?: string | null
  created_by?: string | null
  created_at?: string | null
  updated_at?: string | null
  completed_at?: string | null
  instructions: Array<SchemaMigrationInstructionStatusContract>
  run?: SchemaMigrationRunContract | null
}

export type SemanticResource = SemanticResourceContract
export type SemanticSchema = SemanticSchemaContract
export type PolicyCondition = PolicyConditionContract
export type PolicyEffect = PolicyEffectContract
export type PolicyScope = PolicyScopeContract
export type SecurityPolicy = SecurityPolicyContract
export type ConsistencyRequest = ConsistencyRequestContract
export type AuthorizationContext = AuthorizationContextContract
export type AuthorizationProjection = AuthorizationProjectionContract
export type AdapterCapabilities = AdapterCapabilitiesContract
export type ExecutionContext = ExecutionContextContract
export type ResultManifest = ResultManifestContract
export type DataPlaneStatus = DataPlaneStatusContract
export type MigrationInstruction = MigrationInstructionContract
export type SchemaMigrationPlan = SchemaMigrationPlanContract
