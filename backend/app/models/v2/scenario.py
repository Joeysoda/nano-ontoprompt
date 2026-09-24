"""Durable What-if scenarios, immutable revisions and typed edits."""
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class ScenarioResource(Base):
    __tablename__ = "v2_scenarios"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    owner_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    base_view_id: Mapped[str] = mapped_column(String(64), ForeignKey("v2_query_data_views.id"), nullable=False)
    head_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    etag: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    ttl_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    protected_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    __table_args__ = (CheckConstraint("status IN ('active','archived')", name="ck_scenario_status"),)


class ScenarioRevision(Base):
    __tablename__ = "v2_scenario_revisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    scenario_id: Mapped[str] = mapped_column(String(36), ForeignKey("v2_scenarios.id", ondelete="CASCADE"), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    changeset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    result_view_id: Mapped[str] = mapped_column(String(64), ForeignKey("v2_query_data_views.id"), nullable=False)
    logic_manifest_digest: Mapped[str | None] = mapped_column(String(128), nullable=True)
    content_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ready")
    error_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    __table_args__ = (UniqueConstraint("scenario_id", "revision", name="uq_scenario_revision"),
                      CheckConstraint("status IN ('building','ready','failed','cancelled')", name="ck_scenario_revision_status"))


class ScenarioChangeSet(Base):
    __tablename__ = "v2_scenario_changesets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    scenario_id: Mapped[str] = mapped_column(String(36), ForeignKey("v2_scenarios.id", ondelete="CASCADE"), nullable=False, index=True)
    base_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    ordered_edits: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    payload_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    client_request_id: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="validated")
    validation_report: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    actor_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    __table_args__ = (UniqueConstraint("scenario_id", "client_request_id", name="uq_scenario_request"),)


class ScenarioRun(Base):
    __tablename__ = "v2_scenario_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    scenario_id: Mapped[str] = mapped_column(String(36), ForeignKey("v2_scenarios.id", ondelete="CASCADE"), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    case_key: Mapped[str] = mapped_column(String(120), nullable=False)
    logic_asset_key: Mapped[str] = mapped_column(String(160), nullable=False)
    logic_version: Mapped[str] = mapped_column(String(40), nullable=False)
    parameters: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_stage: Mapped[str] = mapped_column(String(40), nullable=False, default="queued")
    input_digest: Mapped[str | None] = mapped_column(String(128), nullable=True)
    output_digest: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result_manifest: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    study_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    case_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    client_request_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    case_definition_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retry_of_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    result_view_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    compatibility_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    depends_on_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    __table_args__ = (UniqueConstraint("case_id", "client_request_id", name="uq_scenario_run_case_request"),)


class ScenarioRunStage(Base):
    __tablename__ = "v2_scenario_run_stages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("v2_scenario_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    stage_key: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    __table_args__ = (UniqueConstraint("run_id", "stage_key", name="uq_scenario_run_stage"),
                      CheckConstraint("status IN ('pending','running','completed','failed','cancelled','skipped')", name="ck_scenario_run_stage_status"))


class ScenarioStudy(Base):
    __tablename__ = "v2_scenario_studies"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    owner_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    base_view_id: Mapped[str] = mapped_column(String(64), ForeignKey("v2_query_data_views.id"), nullable=False)
    source_manifest_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    scenario_time: Mapped[str] = mapped_column(String(40), nullable=False)
    scope_definition: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    scope_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    smoothing_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    run_baseline_sim: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    model_key: Mapped[str] = mapped_column(String(160), nullable=False)
    model_version: Mapped[str] = mapped_column(String(40), nullable=False)
    model_config_alias: Mapped[str] = mapped_column(String(100), nullable=False)
    parameter_projection: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    etag: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class ScenarioStudyCase(Base):
    __tablename__ = "v2_scenario_study_cases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    study_id: Mapped[str] = mapped_column(String(36), ForeignKey("v2_scenario_studies.id", ondelete="CASCADE"), nullable=False, index=True)
    scenario_id: Mapped[str] = mapped_column(String(36), ForeignKey("v2_scenarios.id", ondelete="CASCADE"), nullable=False, unique=True)
    case_key: Mapped[str] = mapped_column(String(40), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    case_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    action_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    submitted_parameters: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    parameters_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    definition_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    etag: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    synthetic_manifest_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    __table_args__ = (UniqueConstraint("study_id", "case_key", name="uq_study_case_key"),)


class ScenarioMetricSnapshot(Base):
    __tablename__ = "v2_scenario_metrics"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("v2_scenario_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    metric_key: Mapped[str] = mapped_column(String(120), nullable=False)
    baseline: Mapped[float | None] = mapped_column(nullable=True)
    candidate: Mapped[float | None] = mapped_column(nullable=True)
    unit: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    delta: Mapped[float | None] = mapped_column(nullable=True)
    completeness: Mapped[str] = mapped_column(String(20), nullable=False, default="exact")
    provenance: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class ScenarioRunWarning(Base):
    __tablename__ = "v2_scenario_run_warnings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("v2_scenario_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    object_ref: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    provenance: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    __table_args__ = (UniqueConstraint("run_id", "code", name="uq_scenario_run_warning_code"),)


class ScenarioRunArtifact(Base):
    __tablename__ = "v2_scenario_run_artifacts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("v2_scenario_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    artifact_type: Mapped[str] = mapped_column(String(80), nullable=False)
    data_view_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("v2_query_data_views.id"), nullable=True)
    object_set_definition: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    manifest_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    content_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    __table_args__ = (UniqueConstraint("run_id", "artifact_type", name="uq_scenario_run_artifact_type"),)


class ScenarioGrant(Base):
    __tablename__ = "v2_scenario_grants"
    scenario_id: Mapped[str] = mapped_column(String(36), ForeignKey("v2_scenarios.id", ondelete="CASCADE"), primary_key=True)
    principal_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(10), nullable=False)
    __table_args__ = (CheckConstraint("role IN ('viewer','editor')", name="ck_scenario_grant_role"),)


class ScenarioAudit(Base):
    __tablename__ = "v2_scenario_audits"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    scenario_id: Mapped[str] = mapped_column(String(36), ForeignKey("v2_scenarios.id", ondelete="CASCADE"), nullable=False, index=True)
    revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    actor_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    operation: Mapped[str] = mapped_column(String(60), nullable=False)
    summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    request_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


@event.listens_for(ScenarioRevision, "before_update")
def immutable_revision(mapper, connection, target):
    raise ValueError("Published Scenario revisions are immutable")
