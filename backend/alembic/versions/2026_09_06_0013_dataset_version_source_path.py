"""Keep the original source path on every immutable dataset version."""
from alembic import op
import sqlalchemy as sa


revision = "0013_dataset_version_source_path"
down_revision = "0012_workbench_repair_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {item["name"] for item in sa.inspect(bind).get_columns("v2_dataset_versions")}
    if "source_path" not in columns:
        op.add_column("v2_dataset_versions", sa.Column("source_path", sa.Text(), nullable=True))


def downgrade() -> None:
    # Source provenance is retained during downgrade; no destructive drop.
    pass
