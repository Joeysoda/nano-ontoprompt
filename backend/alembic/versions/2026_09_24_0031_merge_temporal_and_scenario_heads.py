"""Merge Joey's temporal data-plane history with the scenario workbench history."""

revision = "0031_merge_temporal_scenario"
down_revision = ("0018_temporal_stream_facts", "0030_supplier_run_evidence")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """The parent migrations contain all schema changes."""


def downgrade() -> None:
    """Downgrading the merge revision restores the two parent heads."""
