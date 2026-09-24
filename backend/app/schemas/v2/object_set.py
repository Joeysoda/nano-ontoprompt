"""Object Set resource API contracts (read options are never persisted)."""
from typing import Literal
from pydantic import Field
from .object_query import StrictModel, ObjectSetExpr, ParameterSpec


class ObjectSetDefinition(StrictModel):
    contract_version: Literal['contract_v2'] = 'contract_v2'
    expression: ObjectSetExpr
    parameter_schema: dict[str, ParameterSpec] = Field(default_factory=dict, max_length=100)


class CreateObjectSet(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default='', max_length=10000)
    lifecycle: Literal['permanent', 'temporary'] = 'permanent'
    ttl_seconds: int = Field(default=86400, ge=60, le=604800)
    definition: ObjectSetDefinition


class NewObjectSetVersion(StrictModel):
    expected_version: int = Field(ge=1)
    definition: ObjectSetDefinition


class PatchObjectSet(StrictModel):
    expected_etag: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10000)
    status: Literal['active', 'archived'] | None = None


class GrantObjectSet(StrictModel):
    expected_etag: int = Field(ge=1)
    principal_id: str
    role: Literal['view', 'edit'] | None = None


class SetHandleRequest(StrictModel):
    data_view_id: str = Field(min_length=1, max_length=200)
    parameters: dict[str, object] = Field(default_factory=dict, max_length=100)
    lease_seconds: int = Field(default=300, ge=60, le=300)
