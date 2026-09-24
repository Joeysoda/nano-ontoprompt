"""Typed API contract for the What-if workbench."""
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field


class ScenarioBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Target(ScenarioBase):
    ontology_id: str
    concrete_type: str
    object_id: str


class LinkTarget(ScenarioBase):
    ontology_id: str
    relation_type: str
    source: Target
    target: Target


class Edit(ScenarioBase):
    op: Literal["set_property", "unset_property", "create_object", "delete_object", "add_link", "remove_link", "invoke_action"]
    target: Target | None = None
    link: LinkTarget | None = None
    property: str | None = Field(default=None, min_length=1)
    value: Any = None
    expected_old_value: Any = None
    object_type: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)
    action_key: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    source_action: str | None = None
    sequence: int = Field(ge=0)


class CreateScenarioRequest(ScenarioBase):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    base_view_id: str
    ttl_seconds: int | None = Field(default=None, ge=60, le=31_536_000)
    protected_demo: bool = False


class ChangeSetRequest(ScenarioBase):
    base_revision: int = Field(ge=0)
    client_request_id: str = Field(min_length=1, max_length=200)
    edits: list[Edit] = Field(min_length=1, max_length=500)


class RevisionRequest(ChangeSetRequest):
    expected_etag: int = Field(ge=1)


class RunRequest(ScenarioBase):
    revision: int = Field(ge=0)
    case_key: str = Field(min_length=1, max_length=120)
    logic_asset_key: str = Field(min_length=1, max_length=160)
    logic_version: str = "1.0.0"
    parameters: dict[str, Any] = Field(default_factory=dict)


class GrantRequest(ScenarioBase):
    principal_id: str
    role: Literal["viewer", "editor"]
