from alembic import op
import sqlalchemy as sa
revision = "0023_query_jobs"
down_revision = "0022_query_views"
branch_labels = None
depends_on = None
def upgrade():
    op.create_table("v2_query_jobs",
        sa.Column("id", sa.String(length=64), nullable=False), sa.Column("ontology_id", sa.String(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=False), sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False), sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=True), sa.Column("checkpoint_json", sa.JSON(), nullable=True),
        sa.Column("error_json", sa.JSON(), nullable=True), sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("lease_token", sa.String(length=128), nullable=True), sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('queued','running','completed','failed','cancelled')", name="ck_query_job_status"),
        sa.ForeignKeyConstraint(["ontology_id"], ["ontology_projects.id"]), sa.ForeignKeyConstraint(["created_by"], ["users.id"]), sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_v2_query_jobs_ontology_id", "v2_query_jobs", ["ontology_id"])
def downgrade():
    raise RuntimeError("Query job history is retained; destructive downgrade is not supported")
