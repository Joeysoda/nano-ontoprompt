"""Freeze supplier Run command identity and artifact pointers."""
from alembic import op
import sqlalchemy as sa

revision = "0029_supplier_run_identity"
down_revision = "0028_supplier_study"
branch_labels = None
depends_on = None


def upgrade():
    for name, kind in (("client_request_id", sa.String(200)), ("case_definition_revision", sa.Integer()),
                       ("retry_of_run_id", sa.String(36)), ("result_view_id", sa.String(64))):
        op.add_column("v2_scenario_runs", sa.Column(name, kind, nullable=True))
    if op.get_bind().dialect.name == "sqlite":
        op.create_index("uq_scenario_run_case_request", "v2_scenario_runs", ["case_id", "client_request_id"], unique=True)
    else:
        op.create_unique_constraint("uq_scenario_run_case_request", "v2_scenario_runs", ["case_id", "client_request_id"])


def downgrade():
    if op.get_bind().dialect.name == "sqlite":
        op.drop_index("uq_scenario_run_case_request", table_name="v2_scenario_runs")
    else:
        op.drop_constraint("uq_scenario_run_case_request", "v2_scenario_runs", type_="unique")
    for name in ("result_view_id", "retry_of_run_id", "case_definition_revision", "client_request_id"):
        op.drop_column("v2_scenario_runs", name)
