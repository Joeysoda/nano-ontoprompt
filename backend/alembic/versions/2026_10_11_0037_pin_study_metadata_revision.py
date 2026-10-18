"""Pin the metadata revision used by Study versions and compiled RunPlans."""
from alembic import op
import sqlalchemy as sa


revision = "0037_pin_study_metadata_revision"
down_revision = "0036_versioned_scenario_studies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("v2_study_versions", sa.Column("ontology_metadata_revision_id", sa.String(), nullable=True))
    op.add_column("v2_scenario_run_plans", sa.Column("ontology_metadata_revision_id", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("v2_scenario_run_plans", "ontology_metadata_revision_id")
    op.drop_column("v2_study_versions", "ontology_metadata_revision_id")
