"""Pin the metadata revision used by Study versions and compiled RunPlans."""
from alembic import op
import sqlalchemy as sa


revision = "0037_pin_study_metadata_revision"
down_revision = "0036_versioned_scenario_studies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table in ("v2_study_versions", "v2_scenario_run_plans"):
        columns = {column["name"] for column in inspector.get_columns(table)}
        if "ontology_metadata_revision_id" not in columns:
            op.add_column(table, sa.Column("ontology_metadata_revision_id", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("v2_scenario_run_plans", "ontology_metadata_revision_id")
    op.drop_column("v2_study_versions", "ontology_metadata_revision_id")
