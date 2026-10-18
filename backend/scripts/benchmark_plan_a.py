"""Measure the Plan A metadata and authorization boundaries.

The command is intentionally read-only for normal measurements.  The cache
invalidation check adds one temporary policy inside the current transaction,
observes the changed permission digest, then rolls the transaction back.

Run from ``backend/`` against the real PostgreSQL service, for example::

    python -m scripts.benchmark_plan_a --runs 25 --warmup 3 \
      --output ../evidence/plan_a_runtime_benchmark.json

The output is JSON so it can be attached to an acceptance run without
depending on a particular dashboard or logging stack.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import platform
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from sqlalchemy import event

# Import the model package once so relationships and semantic-core tables are
# registered before the benchmark opens a SessionLocal connection.
from app import models  # noqa: F401
from app.database import SessionLocal, engine
from app.models.entity import Entity
from app.models.ontology import OntologyProject
from app.models.user import User
from app.models.v2.security import OntologySecurityPolicy
from app.services.v2.authorization_service import AuthorizationContext, resolve_projection
from app.services.v2.object_query.metadata import load_sql_metadata


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def summarize(values: list[float], sql_counts: list[int], policy_counts: list[int]) -> dict[str, Any]:
    return {
        "samples": len(values),
        "p50_ms": round(percentile(values, 0.50), 3),
        "p95_ms": round(percentile(values, 0.95), 3),
        "p99_ms": round(percentile(values, 0.99), 3),
        "max_ms": round(max(values), 3),
        "sql_statements_per_call": {
            "min": min(sql_counts),
            "max": max(sql_counts),
            "mean": round(sum(sql_counts) / len(sql_counts), 3),
        },
        "policy_queries_per_call": {
            "min": min(policy_counts),
            "max": max(policy_counts),
            "mean": round(sum(policy_counts) / len(policy_counts), 3),
        },
    }


class QueryCounter:
    """Count SQL statements for one SQLAlchemy engine during a sample."""

    def __init__(self) -> None:
        self.sql = 0
        self.policy = 0

    def before_execute(self, _conn, _cursor, statement, _parameters, _context, _executemany) -> None:
        self.sql += 1
        if "v2_ontology_security_policies" in statement:
            self.policy += 1


def measure(
    db,
    operation: Callable[[], Any],
    runs: int,
    warmup: int,
) -> dict[str, Any]:
    for _ in range(warmup):
        operation()

    values: list[float] = []
    sql_counts: list[int] = []
    policy_counts: list[int] = []
    counter = QueryCounter()
    event.listen(engine, "before_cursor_execute", counter.before_execute)
    try:
        for _ in range(runs):
            counter.sql = 0
            counter.policy = 0
            started = time.perf_counter()
            operation()
            values.append((time.perf_counter() - started) * 1000)
            sql_counts.append(counter.sql)
            policy_counts.append(counter.policy)
    finally:
        event.remove(engine, "before_cursor_execute", counter.before_execute)
    return summarize(values, sql_counts, policy_counts)


def choose_fixture(db, ontology_id: str | None, principal_id: str | None) -> tuple[OntologyProject, User]:
    project_query = db.query(OntologyProject)
    if ontology_id:
        project = project_query.filter(OntologyProject.id == ontology_id).first()
    else:
        project = project_query.join(Entity, Entity.ontology_id == OntologyProject.id).order_by(OntologyProject.created_at.asc()).first()
    if project is None:
        raise RuntimeError("No ontology with an Entity definition is available")

    user_query = db.query(User).filter(User.is_active.is_(True))
    if principal_id:
        principal = user_query.filter(User.id == principal_id).first()
    else:
        # Prefer a non-admin so the measured path exercises policy projection
        # rather than only the owner/admin fast path.
        principal = user_query.filter(User.role != "admin").order_by(User.created_at.asc()).first()
        principal = principal or user_query.order_by(User.created_at.asc()).first()
    if principal is None:
        raise RuntimeError("No active principal is available")
    return project, principal


def create_temporary_fixture(db) -> tuple[OntologyProject, User]:
    """Create an isolated in-transaction fixture when the DB is empty.

    The real deployment database used for this acceptance run can be freshly
    migrated without containing a FactoryNet ontology yet.  Keeping this
    fallback in the benchmark makes the contract measurable without seeding
    or permanently changing user data; the caller's Session is never
    committed.
    """
    suffix = uuid4().hex
    owner = User(
        id=f"plan-a-owner-{suffix}",
        username=f"plan-a-owner-{suffix}",
        email=f"plan-a-owner-{suffix}@benchmark.invalid",
        password_hash="plan-a-benchmark",
        role="admin",
        is_active=True,
    )
    principal = User(
        id=f"plan-a-principal-{suffix}",
        username=f"plan-a-principal-{suffix}",
        email=f"plan-a-principal-{suffix}@benchmark.invalid",
        password_hash="plan-a-benchmark",
        role="editor",
        is_active=True,
    )
    project = OntologyProject(
        id=f"plan-a-ontology-{suffix}",
        name="Plan A temporary benchmark ontology",
        domain="benchmark",
        status="published",
        created_by=owner.id,
    )
    entity = Entity(
        id=f"plan-a-observation-{suffix}",
        ontology_id=project.id,
        canonical_id="Observation",
        name_cn="观测",
        name_en="Observation",
        properties={
            "property_definitions": [
                {"id": "ordinal", "type": "integer"},
                {"id": "secret", "type": "string"},
            ],
        },
    )
    policy = OntologySecurityPolicy(
        ontology_id=project.id,
        name="temporary observation access",
        subject_kind="user",
        subject_id=principal.id,
        effect="allow",
        scope_kind="ontology",
        conditions_json=[],
        field_allowlist_json=["ordinal"],
        created_by=owner.id,
    )
    db.add_all([owner, principal])
    db.flush()
    db.add(project)
    db.flush()
    db.add_all([entity, policy])
    db.flush()
    return project, principal


def check_permission_cache_invalidation(db, project: OntologyProject, principal: User, context: AuthorizationContext) -> dict[str, Any]:
    before = resolve_projection(db, context).permission_digest
    nested = db.begin_nested()
    temporary = OntologySecurityPolicy(
        ontology_id=project.id,
        revision_id=context.metadata_revision_id,
        name=f"plan-a-benchmark-{uuid4().hex}",
        subject_kind="user",
        subject_id=principal.id,
        effect="allow",
        scope_kind="ontology",
        conditions_json=[],
        field_allowlist_json=[],
        created_by=principal.id,
    )
    try:
        db.add(temporary)
        db.flush()
        changed = resolve_projection(db, context).permission_digest
        digest_changed = before != changed
    finally:
        # Preserve the outer fixture transaction; only the temporary policy
        # belongs to this savepoint and must disappear before the final read.
        nested.rollback()
    after_rollback = resolve_projection(db, context).permission_digest
    return {
        "digest_changed_after_policy_write": digest_changed,
        "digest_restored_after_rollback": after_rollback == before,
        "temporary_policy_rolled_back": temporary.id is not None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ontology-id", default=None)
    parser.add_argument("--principal-id", default=None)
    parser.add_argument("--runs", type=int, default=25)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    if args.runs < 3 or args.warmup < 0:
        parser.error("--runs must be at least 3 and --warmup must be non-negative")

    with SessionLocal() as db:
        temporary_fixture = False
        try:
            project, principal = choose_fixture(db, args.ontology_id, args.principal_id)
        except RuntimeError:
            if args.ontology_id or args.principal_id:
                raise
            project, principal = create_temporary_fixture(db)
            temporary_fixture = True
        context = AuthorizationContext(
            ontology_id=project.id,
            principal_id=str(principal.id),
            principal_role=principal.role,
            metadata_revision_id=project.current_revision_id,
        )
        metadata = measure(db, lambda: load_sql_metadata(db, project.id), args.runs, args.warmup)
        permissions = measure(db, lambda: resolve_projection(db, context), args.runs, args.warmup)
        invalidation = check_permission_cache_invalidation(db, project, principal, context)
        result = {
            "benchmark": "plan-a-metadata-authorization",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "environment": os.getenv("ENVIRONMENT", "development"),
            "database_dialect": engine.dialect.name,
            "python": platform.python_version(),
            "ontology_id": project.id,
            "metadata_revision_id": project.current_revision_id,
            "principal_id": str(principal.id),
            "principal_role": principal.role,
            "fixture": "temporary_uncommitted" if temporary_fixture else "existing_database",
            "runs": args.runs,
            "warmup": args.warmup,
            "measurements": {
                "metadata_parse": metadata,
                "permission_projection": permissions,
            },
            "cache_invalidation": invalidation,
            "query_count_contract": {
                "policy_queries_are_constant_per_projection": permissions["policy_queries_per_call"]["min"] == permissions["policy_queries_per_call"]["max"],
                "policy_queries_max": permissions["policy_queries_per_call"]["max"],
            },
        }

    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
