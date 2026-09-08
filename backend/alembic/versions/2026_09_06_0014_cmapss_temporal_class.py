"""Expose the installed C-MAPSS source package as temporal data."""
from alembic import op
import sqlalchemy as sa


revision = "0014_cmapss_temporal_class"
down_revision = "0013_dataset_version_source_path"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "v2_datasets" not in sa.inspect(bind).get_table_names():
        return
    # The curated copies remain technical/history records.  The two raw
    # tables form one user-facing C-MAPSS source package and must use the
    # temporal privacy/routing rules.
    op.execute(sa.text("""
        UPDATE v2_datasets
        SET data_class = 'temporal'
        WHERE lower(name) IN ('sensor_readings', 'equipment')
    """))


def downgrade() -> None:
    # Do not revert classifications on a workstation with built ontologies.
    pass
