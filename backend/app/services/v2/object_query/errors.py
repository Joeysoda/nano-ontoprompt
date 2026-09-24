"""Stable domain errors for Object Query Core."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class QueryIssue:
    code: str
    path: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


class ObjectQueryError(ValueError):
    def __init__(self, code: str, path: str, message: str, **details: Any):
        self.issue = QueryIssue(code=code, path=path, message=message, details=details)
        super().__init__(message)

    @property
    def code(self) -> str:
        return self.issue.code

    @property
    def path(self) -> str:
        return self.issue.path

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.issue.code,
            "path": self.issue.path,
            "message": self.issue.message,
            "details": self.issue.details,
        }
