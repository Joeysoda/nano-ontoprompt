"""Merge Plan A 2.0 schema alignment with the Plan B runtime chain.

The branches touch disjoint tables (Plan A: audit, instances, datasets, logic
assets; Plan B: studies, rules, graph views), so the merge carries no DDL.
"""
revision = "0040_merge_plan_a2_plan_b"
down_revision = ("0037_schema_migration_execution_journal", "0039_graph_views")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
