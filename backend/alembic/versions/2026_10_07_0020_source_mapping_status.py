"""Add the source-mapping review state for databases already stamped at 0019."""
from alembic import op
import sqlalchemy as sa


revision = "0020_source_mapping_status"
down_revision = "0019_semantic_security_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "v2_ontology_source_mappings" not in set(inspector.get_table_names()):
        return
    columns = {column["name"] for column in inspector.get_columns("v2_ontology_source_mappings")}
    if "mapping_status" not in columns:
        op.add_column(
            "v2_ontology_source_mappings",
            sa.Column(
                "mapping_status",
                sa.String(length=20),
                nullable=False,
                server_default="confirmed",
            ),
        )


def downgrade() -> None:
    # Keep provenance state when downgrading; dropping it would discard review history.
    pass
