"""Tracking Scenario lifecycle without changing immutable revision identities."""
from alembic import op
import sqlalchemy as sa

revision = '0033_tracking_scenarios'
down_revision = '0032_action_run_execution_context'
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if 'v2_time_series_syncs' not in tables:
        op.create_table('v2_time_series_syncs',
            sa.Column('id', sa.String(64), primary_key=True),
            sa.Column('ontology_id', sa.String(), sa.ForeignKey('ontology_projects.id'), nullable=False),
            sa.Column('data_view_id', sa.String(64), sa.ForeignKey('v2_query_data_views.id'), nullable=False),
            sa.Column('series_id', sa.String(200), nullable=False),
            sa.Column('root_type', sa.String(200), nullable=False), sa.Column('root_id', sa.String(300), nullable=False),
            sa.Column('sensor_type', sa.String(200), nullable=False), sa.Column('sensor_id', sa.String(300), nullable=False),
            sa.Column('property_api_name', sa.String(200), nullable=False), sa.Column('unit', sa.String(100), nullable=False),
            sa.Column('interpolation', sa.String(40), nullable=False), sa.Column('points', sa.JSON(), nullable=False),
            sa.Column('point_count', sa.Integer(), nullable=False), sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint('data_view_id', 'series_id', name='uq_v2_series_view_id'))
    view_columns = {column['name'] for column in inspector.get_columns('v2_query_data_views')}
    if 'source_snapshot_id' not in view_columns:
        op.add_column('v2_query_data_views', sa.Column('source_snapshot_id', sa.String(), nullable=True))
    scenario_columns = {column['name'] for column in inspector.get_columns('v2_scenarios')}
    if 'mode' not in scenario_columns:
        op.add_column('v2_scenarios', sa.Column('mode', sa.String(20), nullable=False, server_default='pinned'))
    if 'last_rebased_at' not in scenario_columns:
        op.add_column('v2_scenarios', sa.Column('last_rebased_at', sa.DateTime(timezone=True), nullable=True))
    if 'rebase_error' not in scenario_columns:
        op.add_column('v2_scenarios', sa.Column('rebase_error', sa.JSON(), nullable=True))


def downgrade():
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if 'v2_time_series_syncs' in tables:
        op.drop_table('v2_time_series_syncs')
    view_columns = {column['name'] for column in inspector.get_columns('v2_query_data_views')}
    if 'source_snapshot_id' in view_columns:
        op.drop_column('v2_query_data_views', 'source_snapshot_id')
    scenario_columns = {column['name'] for column in inspector.get_columns('v2_scenarios')}
    if 'rebase_error' in scenario_columns:
        op.drop_column('v2_scenarios', 'rebase_error')
    if 'last_rebased_at' in scenario_columns:
        op.drop_column('v2_scenarios', 'last_rebased_at')
    if 'mode' in scenario_columns:
        op.drop_column('v2_scenarios', 'mode')
