"""Create the ReAct audit task table used by the audit router/worker."""
from alembic import op
import sqlalchemy as sa

revision = "0004_audit_tasks"
down_revision = "0003_entity_instances"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "audit_tasks" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "audit_tasks",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("ontology_id", sa.String(), nullable=False),
        sa.Column("model_id", sa.String(), nullable=True),
        sa.Column("model_name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("progress", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("findings", sa.JSON(), nullable=True),
        sa.Column("react_trace", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["model_id"], ["model_configs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["ontology_id"], ["ontology_projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("audit_tasks")
