from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from app.schemas.v2.object_query import StrictModel
from app.services.v2.row_mapping import RowMapping


class LiteralBinding(StrictModel):
    kind: Literal["literal"]
    value: Any


class RowsBinding(StrictModel):
    kind: Literal["mapped_rows"]


class ColumnBinding(StrictModel):
    kind: Literal["column"]
    field: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$", max_length=120)


InputBinding = Annotated[LiteralBinding | RowsBinding | ColumnBinding, Field(discriminator="kind")]


class ProjectionSpec(StrictModel):
    query_api_name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,62}$")
    query_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    query_params: dict[str, Any] = Field(default_factory=dict, max_length=20)
    time_range: Literal["all", "1d", "7d", "30d"] = "all"
    limit: int = Field(default=10_000, ge=1, le=10_000)
    mapping: RowMapping
    asset_key: str = Field(min_length=1, max_length=120)
    asset_version: str = Field(min_length=1, max_length=40)
    inputs: dict[str, InputBinding] = Field(min_length=1, max_length=50)
    max_rows: int = Field(default=10_000, ge=1, le=10_000)

    @model_validator(mode="after")
    def references_mapped_fields(self):
        names = {field.target for field in self.mapping.fields}
        for target, binding in self.inputs.items():
            if binding.kind == "column" and binding.field not in names:
                raise ValueError(f"Input {target} references unknown mapped field {binding.field}")
        return self


class CreateProjection(StrictModel):
    api_name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,62}$")
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    spec: ProjectionSpec
    interval_minutes: int | None = Field(default=None, ge=5, le=1440)

class UpdateProjection(StrictModel):
    expected_etag: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    spec: ProjectionSpec | None = None
    status: Literal["active", "paused", "retired"] | None = None
    interval_minutes: int | None = Field(default=None, ge=5, le=1440)

