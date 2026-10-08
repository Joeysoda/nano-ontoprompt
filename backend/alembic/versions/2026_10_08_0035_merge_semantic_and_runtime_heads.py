"""Join the semantic-governance and operational-runtime migration histories.

Both branches extended the published FactoryNet temporal revision independently.
This no-op merge makes a database deployed from either branch upgrade through
the other branch's migrations before the combined application starts.
"""

revision = "0035_merge_semantic_runtime"
down_revision = ("0020_source_mapping_status", "0034_object_view_configs")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
