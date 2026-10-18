"""Typed rule runtime contracts.

Rule logic follows the public Foundry Rules shape: an input, an ordered chain
of logic blocks, and output rows carrying the rule's output properties.  The
input is an existing Object Set expression so membership, traversal and data
authorization stay owned by the Object Query compiler.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, TypeAlias

from pydantic import Field, model_validator

from app.schemas.v2.object_query import ExecutionContext, FilterExpr, ObjectSetExpr, StrictModel

Severity: TypeAlias = Literal["low", "medium", "high", "critical"]


class FilterBlock(StrictModel):
    kind: Literal["filter"]
    where: FilterExpr


class SeriesWindowBlock(StrictModel):
    """Trailing window over a Time Series Property of each input object.

    Windows count points, not wall-clock time; the evaluator reports gaps
    instead of silently treating irregular sampling as regular.
    """
    kind: Literal["series_window"]
    series_property: str = Field(min_length=1, max_length=200)
    window_points: int = Field(ge=1, le=10_000)
    aggregate: Literal["avg", "min", "max", "sum"]
    op: Literal["gt", "gte", "lt", "lte"]
    threshold: float
    min_consecutive_windows: int = Field(default=1, ge=1, le=100_000)


LogicBlock: TypeAlias = Annotated[FilterBlock | SeriesWindowBlock, Field(discriminator="kind")]


class RuleOutput(StrictModel):
    severity: Severity
    # Workflow-owner custom properties copied onto every output row, e.g.
    # assignee, group or priority (Foundry Rules "custom properties").
    properties: dict[str, str | int | float | bool] = Field(default_factory=dict, max_length=20)
    evidence_properties: list[str] = Field(default_factory=list, max_length=20)


class ActionBinding(StrictModel):
    """Suggested follow-up Action. Rules never apply it; users preview/submit."""
    action_id: str = Field(min_length=1, max_length=200)
    # Object-typed Action parameter that receives the alert subject reference.
    object_parameter: str | None = Field(default=None, max_length=100)
    parameters: dict[str, Any] = Field(default_factory=dict, max_length=50)


class RuleLogic(StrictModel):
    input: ObjectSetExpr
    blocks: list[LogicBlock] = Field(default_factory=list, max_length=20)
    output: RuleOutput
    action: ActionBinding | None = None

    @model_validator(mode="after")
    def series_window_is_terminal(self) -> "RuleLogic":
        positions = [i for i, block in enumerate(self.blocks) if block.kind == "series_window"]
        if len(positions) > 1 or (positions and positions[0] != len(self.blocks) - 1):
            raise ValueError("series_window must be the single final logic block")
        return self


class CreateRuleRequest(StrictModel):
    api_name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,99}$")
    display_name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    logic: RuleLogic


class ProposeRuleChangeRequest(StrictModel):
    base_version: int = Field(ge=0)
    logic: RuleLogic
    description: str = Field(default="", max_length=2000)


class ReviewProposalRequest(StrictModel):
    decision: Literal["approve", "reject"]
    comment: str = Field(default="", max_length=2000)


class RuleStatusRequest(StrictModel):
    status: Literal["active", "muted", "paused"]


class EvaluateRuleRequest(StrictModel):
    context: ExecutionContext
    mode: Literal["preview", "commit"] = "preview"
    version: int | None = Field(default=None, ge=1)


class AlertTransitionRequest(StrictModel):
    note: str = Field(default="", max_length=2000)
    # Resolve only: the completed Action run that handled the alert (trace link).
    action_run_id: str | None = Field(default=None, max_length=200)
