"""Typed public contracts for versioned scenario studies."""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ParameterDefinition(StrictModel):
    key: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    label: str | None = Field(default=None, max_length=200)
    type: Literal["string", "integer", "decimal", "boolean", "datetime", "object_ref", "object_set", "json"]
    unit: str | None = Field(default=None, max_length=80)
    required: bool = False
    default: Any = None
    minimum: float | int | None = None
    maximum: float | int | None = None
    enum: list[Any] | None = Field(default=None, max_length=200)


class NodeImplementation(StrictModel):
    key: str = Field(min_length=1, max_length=160)
    version: str = Field(min_length=1, max_length=80)


class StudyNode(StrictModel):
    key: str = Field(min_length=1, max_length=60, pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    kind: Literal[
        "object_query", "function", "action_preview", "action_apply_scenario",
        "aggregate_metric", "compare", "time_series", "solver",
    ]
    implementation: NodeImplementation | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, str] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)


class StudyEdge(StrictModel):
    source: str = Field(min_length=1, max_length=241)
    target: str = Field(min_length=1, max_length=241)


class MetricDefinition(StrictModel):
    key: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    label: str | None = Field(default=None, max_length=200)
    type: Literal["integer", "decimal", "boolean", "duration"]
    direction: Literal["minimize", "maximize", "neutral"] = "neutral"
    unit: str | None = Field(default=None, max_length=80)
    source: str = Field(min_length=1, max_length=241)


class ConstraintDefinition(StrictModel):
    key: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    expression_ref: str = Field(min_length=1, max_length=160)
    severity: Literal["hard", "soft"] = "hard"
    config: dict[str, Any] = Field(default_factory=dict)


class StudyBudgets(StrictModel):
    max_nodes: int = Field(default=50, ge=1, le=200)
    max_objects: int = Field(default=100_000, ge=1, le=10_000_000)
    max_wall_seconds: int = Field(default=900, ge=1, le=86_400)
    max_artifact_bytes: int = Field(default=52_428_800, ge=1024, le=1_073_741_824)


class ContextPolicy(StrictModel):
    scenario_mode: Literal["pinned_on_run"] = "pinned_on_run"
    time_required: bool = False


class StudySpec(StrictModel):
    spec_version: Literal["scenario-study/v1"] = "scenario-study/v1"
    context_policy: ContextPolicy = Field(default_factory=ContextPolicy)
    parameters: list[ParameterDefinition] = Field(default_factory=list, max_length=200)
    nodes: list[StudyNode] = Field(default_factory=list, max_length=200)
    edges: list[StudyEdge] = Field(default_factory=list, max_length=1000)
    metrics: list[MetricDefinition] = Field(default_factory=list, max_length=200)
    constraints: list[ConstraintDefinition] = Field(default_factory=list, max_length=200)
    budgets: StudyBudgets = Field(default_factory=StudyBudgets)

    @field_validator("parameters", "nodes", "metrics", "constraints")
    @classmethod
    def unique_keys(cls, value: list[Any]) -> list[Any]:
        keys = [item.key for item in value]
        if len(keys) != len(set(keys)):
            raise ValueError("keys must be unique within the collection")
        return value


class CreateStudyRequest(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10_000)
    draft_spec: StudySpec = Field(default_factory=StudySpec)


class UpdateStudyRequest(StrictModel):
    expected_etag: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10_000)
    draft_spec: StudySpec | None = None


class PublishStudyRequest(StrictModel):
    expected_etag: int = Field(ge=1)
    change_note: str = Field(default="", max_length=10_000)
    compatibility: Literal["compatible", "breaking"] = "compatible"


class OrderedAction(StrictModel):
    action_key: str = Field(min_length=1, max_length=160)
    version: str = Field(min_length=1, max_length=80)
    parameters: dict[str, Any] = Field(default_factory=dict)


class CreateCaseRequest(StrictModel):
    study_version: int = Field(ge=1)
    scenario_id: str = Field(min_length=1, max_length=36)
    name: str = Field(min_length=1, max_length=200)
    case_kind: Literal["baseline", "candidate"] = "candidate"
    source_case_id: str | None = Field(default=None, max_length=36)
    parameter_overrides: dict[str, Any] = Field(default_factory=dict)
    ordered_actions: list[OrderedAction] = Field(default_factory=list, max_length=500)


class UpdateCaseRequest(StrictModel):
    expected_etag: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    parameter_overrides: dict[str, Any] | None = None
    ordered_actions: list[OrderedAction] | None = Field(default=None, max_length=500)


class StartStudyRunRequest(StrictModel):
    expected_etag: int = Field(ge=1)
    client_request_id: str = Field(min_length=1, max_length=200)


class RetryStudyRunRequest(StrictModel):
    client_request_id: str = Field(min_length=1, max_length=200)
