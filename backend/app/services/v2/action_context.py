"""Resolve the execution target shared by live and Scenario Action paths.

This is deliberately a small boundary for Phase 1.  It does not execute an
Action; it prevents a request carrying Scenario context from silently falling
through to the legacy live-write executor.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.schemas.v2.object_query import ExecutionContext
from app.services.v2.scenarios import ScenarioError, resolve_scenario_context


class ActionContextError(ValueError):
    def __init__(self, code: str, message: str, status: int = 422, path: str = "context"):
        self.code = code
        self.message = message
        self.status = status
        self.path = path
        super().__init__(message)


@dataclass(frozen=True)
class ResolvedActionContext:
    context: ExecutionContext
    target: Literal["live", "scenario"]


def resolve_action_context(db, ontology_id: str, context: ExecutionContext | None, user) -> ResolvedActionContext:
    """Validate and resolve an Action's execution target.

    A missing context is explicitly live for backwards compatibility.  A
    Scenario pointer is resolved through the existing Scenario authorization
    and data-view contract; callers must still use a Scenario-aware compiler
    before they can write edits to that target.
    """
    candidate = context or ExecutionContext(ontology_id=ontology_id)
    if candidate.ontology_id != ontology_id:
        raise ActionContextError(
            "ontology_context_mismatch",
            "Action context ontology differs from route ontology",
            422,
            "context.ontology_id",
        )
    if candidate.scenario_revision is not None and not candidate.scenario_id:
        raise ActionContextError(
            "invalid_scenario_context",
            "scenario_revision requires scenario_id",
            422,
            "context.scenario_revision",
        )
    if not candidate.scenario_id:
        return ResolvedActionContext(candidate, "live")
    try:
        resolved = resolve_scenario_context(db, candidate, ontology_id, user)
    except ScenarioError as exc:
        raise ActionContextError(exc.code, exc.message, exc.status, exc.path) from exc
    return ResolvedActionContext(resolved, "scenario")


def action_context_error(exc: ActionContextError) -> dict[str, object]:
    return {"code": exc.code, "path": exc.path, "message": exc.message, "details": {}}


__all__ = ["ActionContextError", "ResolvedActionContext", "resolve_action_context", "action_context_error"]
