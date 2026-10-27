"""Make process models publishable and link generated Action transitions."""
from alembic import op
import sqlalchemy as sa

revision = "0045_process_models"
down_revision = "0044_derived_projections"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("v2_ontology_state_machines") as batch:
        batch.add_column(sa.Column("name", sa.String(200), nullable=False, server_default="Process"))
        batch.add_column(sa.Column("description", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("status", sa.String(20), nullable=False, server_default="draft"))
        batch.add_column(sa.Column("version", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("etag", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("created_by", sa.String(), sa.ForeignKey("users.id"), nullable=True))
    with op.batch_alter_table("v2_ontology_action_types") as batch:
        batch.add_column(sa.Column("state_machine_id", sa.String(), sa.ForeignKey("v2_ontology_state_machines.id", ondelete="SET NULL"), nullable=True))
        batch.add_column(sa.Column("transition_key", sa.String(100), nullable=True))
        batch.create_unique_constraint("uq_action_state_transition", ["state_machine_id", "transition_key"])


def downgrade():
    with op.batch_alter_table("v2_ontology_action_types") as batch:
        batch.drop_constraint("uq_action_state_transition", type_="unique")
        batch.drop_column("transition_key"); batch.drop_column("state_machine_id")
    with op.batch_alter_table("v2_ontology_state_machines") as batch:
        for name in ("created_by", "etag", "version", "status", "description", "name"):
            batch.drop_column(name)
