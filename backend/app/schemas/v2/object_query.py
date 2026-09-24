"""Public, storage-independent contracts for Object Set queries.

The four boundaries in this module are intentionally separate:
``ObjectSetExpr`` defines membership, ``ReadOptions`` defines loading,
``ExecutionContext`` selects a data view, and ``TerminalKind`` selects the
result operation.  Database-specific syntax must not cross this boundary.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TypeRef(StrictModel):
    kind: Literal["object", "interface"] = "object"
    api_name: str = Field(min_length=1, max_length=200)


class PropertyRef(StrictModel):
    api_name: str = Field(min_length=1, max_length=200)


class LinkRef(StrictModel):
    api_name: str = Field(min_length=1, max_length=200)
    direction: Literal["out", "in"] = "out"


class ParameterRef(StrictModel):
    kind: Literal["parameter"]
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,99}$")
    data_type: Literal["string", "boolean", "integer", "decimal", "date", "timestamp", "object", "array"]


class ParameterSpec(StrictModel):
    data_type: Literal["string", "boolean", "integer", "decimal", "date", "timestamp", "object", "array"]
    default: Any = None
    required: bool = True
    nullable: bool = False
    minimum: Any = None
    maximum: Any = None
    enum: list[Any] | None = Field(default=None, max_length=1000)
    max_length: int = Field(default=1000, ge=1, le=10000)
    element_type: Literal["string", "boolean", "integer", "decimal", "date", "timestamp", "object"] | None = None
    allowed_fields: list[str] = Field(min_length=1, max_length=200)


class AndFilter(StrictModel):
    kind: Literal["and"]
    items: list["FilterExpr"] = Field(min_length=1, max_length=50)


class OrFilter(StrictModel):
    kind: Literal["or"]
    items: list["FilterExpr"] = Field(min_length=1, max_length=50)


class NotFilter(StrictModel):
    kind: Literal["not"]
    item: "FilterExpr"


class ComparisonFilter(StrictModel):
    kind: Literal["comparison"]
    property: PropertyRef
    op: Literal["eq", "ne", "gt", "gte", "lt", "lte", "in"]
    value: Any

    @model_validator(mode="after")
    def validate_value_shape(self) -> "ComparisonFilter":
        if isinstance(self.value, dict) and self.value.get("kind") == "parameter":
            ParameterRef.model_validate(self.value)
            return self
        if isinstance(self.value, dict) and set(self.value) in ({"$decimal"}, {"$date"}, {"$timestamp"}):
            return self
        if self.op == "in" and not isinstance(self.value, list):
            raise ValueError("comparison 'in' requires an array value")
        if self.op != "in" and isinstance(self.value, (dict, list)):
            raise ValueError(f"comparison '{self.op}' requires a scalar value")
        return self


class NullFilter(StrictModel):
    kind: Literal["null_test"]
    property: PropertyRef
    is_null: bool = True


class TextFilter(StrictModel):
    kind: Literal["text"]
    property: PropertyRef
    mode: Literal["contains", "starts_with", "match_all_tokens", "match_any_token"]
    query: str = Field(min_length=1, max_length=1000)
    fuzzy: bool = False


class ArrayFilter(StrictModel):
    kind: Literal["array_match"]
    property: PropertyRef
    mode: Literal["contains_any", "contains_all"]
    values: list[Any] = Field(min_length=1, max_length=1000)


class IntervalFilter(StrictModel):
    kind: Literal["interval"]
    property: PropertyRef
    lower: Any | None = None
    upper: Any | None = None
    lower_inclusive: bool = True
    upper_inclusive: bool = True

    @model_validator(mode="after")
    def require_bound(self) -> "IntervalFilter":
        if self.lower is None and self.upper is None:
            raise ValueError("interval requires at least one bound")
        return self


FilterExpr: TypeAlias = Annotated[
    AndFilter | OrFilter | NotFilter | ComparisonFilter | NullFilter | TextFilter | ArrayFilter | IntervalFilter,
    Field(discriminator="kind"),
]


class DerivedMethodInput(StrictModel):
    kind: Literal["method_input"]


class DerivedFilter(StrictModel):
    kind: Literal["filter"]
    input: "DerivedObjectSetExpr"
    where: FilterExpr


class DerivedTraverse(StrictModel):
    kind: Literal["traverse"]
    input: "DerivedObjectSetExpr"
    link: LinkRef


DerivedObjectSetExpr: TypeAlias = Annotated[
    DerivedMethodInput | DerivedFilter | DerivedTraverse,
    Field(discriminator="kind"),
]


class DerivedSelection(StrictModel):
    kind: Literal["selection"]
    input: DerivedObjectSetExpr
    property: PropertyRef


class DerivedAggregation(StrictModel):
    kind: Literal["aggregation"]
    input: DerivedObjectSetExpr
    op: Literal[
        "count", "sum", "avg", "min", "max", "exact_distinct",
        "approximate_distinct", "collect_list", "collect_set",
    ]
    property: PropertyRef | None = None
    limit: int | None = Field(default=None, ge=1, le=1000)

    @model_validator(mode="after")
    def validate_aggregation(self) -> "DerivedAggregation":
        if self.op == "count" and self.property is not None:
            raise ValueError("count does not accept a property")
        if self.op != "count" and self.property is None:
            raise ValueError(f"{self.op} requires a property")
        if self.op in {"collect_list", "collect_set"} and self.limit is None:
            raise ValueError(f"{self.op} requires an explicit limit")
        if self.op not in {"collect_list", "collect_set"} and self.limit is not None:
            raise ValueError(f"{self.op} does not accept a limit")
        return self


DerivedPropertyExpr: TypeAlias = Annotated[
    DerivedSelection | DerivedAggregation,
    Field(discriminator="kind"),
]


class BaseObjectSet(StrictModel):
    kind: Literal["base"]
    type_ref: TypeRef


class InterfaceBaseObjectSet(StrictModel):
    kind: Literal["interface_base"]
    type_ref: TypeRef
    include_all_base_object_properties: bool = False

    @model_validator(mode="after")
    def require_interface(self) -> "InterfaceBaseObjectSet":
        if self.type_ref.kind != "interface":
            raise ValueError("interface_base requires an interface type_ref")
        return self


class StaticObjectSet(StrictModel):
    kind: Literal["static"]
    type_ref: TypeRef
    object_ids: list[str] = Field(max_length=10_000)


class EmptyObjectSet(StrictModel):
    kind: Literal["empty"]
    type_ref: TypeRef


class ReferenceObjectSet(StrictModel):
    kind: Literal["reference"]
    object_set_id: str = Field(min_length=1, max_length=300)
    definition_version: int | None = Field(default=None, ge=1)


class FilterObjectSet(StrictModel):
    kind: Literal["filter"]
    input: "ObjectSetExpr"
    where: FilterExpr


class TraverseObjectSet(StrictModel):
    kind: Literal["traverse"]
    input: "ObjectSetExpr"
    link: LinkRef


class LinkedObjectSet(StrictModel):
    kind: Literal["linked"]
    input: "ObjectSetExpr"
    link: LinkRef
    quantifier: Literal["any", "none", "all", "count"]
    where: FilterExpr
    count_op: Literal["eq", "ne", "gt", "gte", "lt", "lte"] = "gte"
    count: int = Field(default=1, ge=0)


class ReachableObjectSet(StrictModel):
    kind: Literal["reachable"]
    input: "ObjectSetExpr"
    link: LinkRef
    max_depth: int = Field(default=8, ge=1, le=32)
    max_nodes: int = Field(default=1000, ge=1, le=10000)
    include_seed: bool = False


class InterfaceTraverseObjectSet(StrictModel):
    kind: Literal["interface_traverse"]
    input: "ObjectSetExpr"
    link: LinkRef


class UnionObjectSet(StrictModel):
    kind: Literal["union"]
    inputs: list["ObjectSetExpr"] = Field(min_length=2, max_length=20)


class IntersectObjectSet(StrictModel):
    kind: Literal["intersect"]
    inputs: list["ObjectSetExpr"] = Field(min_length=2, max_length=20)


class SubtractObjectSet(StrictModel):
    kind: Literal["subtract"]
    base: "ObjectSetExpr"
    subtract: list["ObjectSetExpr"] = Field(min_length=1, max_length=19)


class TextNearestQuery(StrictModel):
    kind: Literal["text"]
    value: str = Field(min_length=1, max_length=4000)


class VectorNearestQuery(StrictModel):
    kind: Literal["vector"]
    value: list[float] = Field(min_length=1, max_length=2048)


NearestQuery: TypeAlias = Annotated[TextNearestQuery | VectorNearestQuery, Field(discriminator="kind")]


class NearestNeighborsObjectSet(StrictModel):
    kind: Literal["nearest_neighbors"]
    input: "ObjectSetExpr"
    property: PropertyRef
    query: NearestQuery
    k: int = Field(ge=1, le=100)
    similarity_threshold: float | None = Field(default=None, ge=-1.0, le=1.0)


class WithPropertiesObjectSet(StrictModel):
    kind: Literal["with_properties"]
    input: "ObjectSetExpr"
    definitions: dict[str, DerivedPropertyExpr] = Field(min_length=1, max_length=50)


class AsTypeObjectSet(StrictModel):
    kind: Literal["as_type"]
    input: "ObjectSetExpr"
    type_ref: TypeRef


class AsBaseObjectTypesObjectSet(StrictModel):
    kind: Literal["as_base_object_types"]
    input: "ObjectSetExpr"


ObjectSetExpr: TypeAlias = Annotated[
    BaseObjectSet
    | EmptyObjectSet
    | LinkedObjectSet
    | ReachableObjectSet
    | InterfaceBaseObjectSet
    | StaticObjectSet
    | ReferenceObjectSet
    | FilterObjectSet
    | TraverseObjectSet
    | InterfaceTraverseObjectSet
    | UnionObjectSet
    | IntersectObjectSet
    | SubtractObjectSet
    | NearestNeighborsObjectSet
    | WithPropertiesObjectSet
    | AsTypeObjectSet
    | AsBaseObjectTypesObjectSet,
    Field(discriminator="kind"),
]


class OrderBy(StrictModel):
    property: PropertyRef
    direction: Literal["asc", "desc"] = "asc"
    nulls: Literal["first", "last"] = "last"


class ReadOptions(StrictModel):
    select: list[PropertyRef] = Field(default_factory=list, max_length=200)
    order_by: list[OrderBy] = Field(default_factory=list, max_length=10)
    page_size: int = Field(default=50, ge=1, le=200)
    page_token: str | None = Field(default=None, max_length=8192)
    include_total: bool = False
    explain: bool = False
    materialization_limit: int = Field(default=1000, ge=1, le=10_000)


class ExecutionContext(StrictModel):
    ontology_id: str = Field(min_length=1, max_length=200)
    revision_policy: Literal["latest", "pinned"] = "latest"
    revision_id: str | None = Field(default=None, max_length=200)
    consistency: Literal["live", "projection_guarded", "snapshot"] = "live"
    metadata_digest: str | None = None
    data_view_id: str | None = Field(default=None, max_length=200)
    projection_token: str | None = Field(default=None, max_length=500)
    scenario_id: str | None = Field(default=None, max_length=200)
    scenario_revision: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_revision(self) -> "ExecutionContext":
        if self.revision_policy == "pinned" and not self.revision_id:
            raise ValueError("pinned revision_policy requires revision_id")
        if self.scenario_id and self.scenario_revision is None:
            raise ValueError("scenario context requires scenario_revision")
        return self


TerminalKind = Literal["load", "aggregate", "load_links", "derive", "create_temporary", "subscribe"]


class LoadObjectSetRequest(StrictModel):
    contract_version: Literal["contract_v2"] = "contract_v2"
    expression: ObjectSetExpr
    parameter_schema: dict[str, ParameterSpec] = Field(default_factory=dict, max_length=100)
    parameters: dict[str, Any] = Field(default_factory=dict, max_length=100)
    read: ReadOptions = Field(default_factory=ReadOptions)
    context: ExecutionContext
    terminal: Literal["load"] = "load"


class AggregationSpec(StrictModel):
    op: Literal["count", "sum", "avg", "min", "max", "exact_distinct", "approximate_distinct"]
    property: PropertyRef | None = None
    alias: str = Field(default="value", min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_property(self) -> "AggregationSpec":
        if self.op == "count" and self.property is not None:
            raise ValueError("count does not accept a property")
        if self.op != "count" and self.property is None:
            raise ValueError(f"{self.op} requires a property")
        return self


class TimeBucketSpec(StrictModel):
    property: PropertyRef
    unit: Literal["hour", "day", "week", "month"]


class AggregateObjectSetRequest(StrictModel):
    contract_version: Literal["contract_v2"] = "contract_v2"
    expression: ObjectSetExpr
    parameter_schema: dict[str, ParameterSpec] = Field(default_factory=dict, max_length=100)
    parameters: dict[str, Any] = Field(default_factory=dict, max_length=100)
    group_by: list[PropertyRef] = Field(default_factory=list, max_length=5)
    time_bucket: TimeBucketSpec | None = None
    aggregations: list[AggregationSpec] = Field(min_length=1, max_length=20)
    context: ExecutionContext
    terminal: Literal["aggregate"] = "aggregate"


class AggregateResult(StrictModel):
    groups: list[dict[str, Any]]
    exact: bool
    completeness: Literal["complete", "incomplete"]
    definition_hash: str
    execution_hash: str
    execution_context: dict[str, Any] = Field(default_factory=dict)


class CompareObjectSetRequest(StrictModel):
    contract_version: Literal["contract_v2"] = "contract_v2"
    expression: ObjectSetExpr
    parameter_schema: dict[str, ParameterSpec] = Field(default_factory=dict, max_length=100)
    parameters: dict[str, Any] = Field(default_factory=dict, max_length=100)
    baseline_context: ExecutionContext
    candidate_context: ExecutionContext
    mode: Literal["fixed_cohort", "reevaluate"]
    select: list[PropertyRef] = Field(default_factory=list, max_length=200)


class CompareResult(StrictModel):
    mode: Literal["fixed_cohort", "reevaluate"]
    added: list[dict[str, Any]]
    removed: list[dict[str, Any]]
    retained: list[dict[str, Any]]
    baseline_execution_hash: str
    candidate_execution_hash: str


class QueryDataViewCreate(StrictModel):
    source_ontology_id: str = Field(min_length=1, max_length=200)
    source_manifest_digest: str = Field(min_length=1, max_length=128)
    retention_seconds: int = Field(default=86400, ge=60, le=31536000)


class QueryDataViewResponse(StrictModel):
    view_id: str
    ontology_id: str
    status: Literal["building", "validating", "ready", "failed", "expired"]
    metadata_digest: str
    source_manifest_digest: str
    base_view_id: str | None = None
    changeset_digest: str | None = None
    changeset_version: str | None = None
    builder_version: str = "query-view-builder-v1"
    index_version: str = "instance-index-v1"
    object_count: int = Field(ge=0)
    edge_count: int = Field(ge=0)
    content_digest: str | None = None
    created_at: str
    retention_until: str | None = None


class QueryJobResponse(StrictModel):
    job_id: str
    ontology_id: str
    kind: Literal["materialize"]
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    checkpoint: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    cancel_requested: bool = False


class ObjectRecord(StrictModel):
    object_id: str
    object_type: str
    properties: dict[str, Any]


class LoadPageInfo(StrictModel):
    page_size: int
    returned: int
    has_more: bool
    next_page_token: str | None = None
    stability: Literal["single_page_live", "snapshot_keyset"] = "single_page_live"


class QueryExplanation(StrictModel):
    expression_hash: str
    node_count: int
    traversal_depth: int
    result_type: TypeRef


class LoadObjectSetResponse(StrictModel):
    objects: list[ObjectRecord]
    page: LoadPageInfo
    explanation: QueryExplanation | None = None
    definition_hash: str = ""
    execution_hash: str = ""
    execution_context: dict[str, Any] = Field(default_factory=dict)
    completeness: Literal["complete", "partial", "incomplete"] = "partial"
    status: Literal["ok", "empty", "partial", "incomplete"] = "partial"


class ValidateObjectQueryResponse(StrictModel):
    valid: bool = True
    contract_version: Literal["contract_v2"] = "contract_v2"
    result_type: TypeRef
    definition_hash: str
    metadata_digest: str
    dependencies: list[dict[str, Any]] = Field(default_factory=list)
    node_count: int = Field(ge=1)
    traversal_depth: int = Field(ge=0)
    capabilities: dict[str, Any] = Field(default_factory=dict)


for _model in (
    AndFilter, OrFilter, NotFilter, DerivedFilter, DerivedTraverse,
    DerivedSelection, DerivedAggregation, FilterObjectSet, TraverseObjectSet,
    InterfaceTraverseObjectSet, UnionObjectSet, IntersectObjectSet,
    SubtractObjectSet, NearestNeighborsObjectSet, WithPropertiesObjectSet,
    AsTypeObjectSet, AsBaseObjectTypesObjectSet, LoadObjectSetRequest,
    AggregateObjectSetRequest, LinkedObjectSet, ReachableObjectSet,
):
    _model.model_rebuild(
        _types_namespace={
            "FilterExpr": FilterExpr,
            "DerivedObjectSetExpr": DerivedObjectSetExpr,
            "DerivedPropertyExpr": DerivedPropertyExpr,
            "ObjectSetExpr": ObjectSetExpr,
        }
    )


def object_query_json_schema() -> dict[str, Any]:
    """Return the generated public load-request schema."""
    return LoadObjectSetRequest.model_json_schema()
