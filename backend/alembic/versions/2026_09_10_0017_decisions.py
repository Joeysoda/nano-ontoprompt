"""Decision authority records and relationships."""
from alembic import op
import sqlalchemy as sa

revision = '0017_decisions'
down_revision = '0016_reasoning'
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table('v2_decisions'):
        op.create_table('v2_decisions', sa.Column('id', sa.String(), primary_key=True),
                        sa.Column('ontology_id', sa.String(), sa.ForeignKey('ontology_projects.id', ondelete='CASCADE'), nullable=False),
                        sa.Column('payload', sa.JSON(), nullable=False))
        op.create_index('ix_v2_decisions_ontology_id', 'v2_decisions', ['ontology_id'])
    if not inspector.has_table('v2_decision_links'):
        op.create_table('v2_decision_links', sa.Column('id', sa.String(), primary_key=True),
                        sa.Column('ontology_id', sa.String(), sa.ForeignKey('ontology_projects.id', ondelete='CASCADE'), nullable=False),
                        sa.Column('source', sa.String(), sa.ForeignKey('v2_decisions.id'), nullable=False),
                        sa.Column('target', sa.String(), sa.ForeignKey('v2_decisions.id'), nullable=False),
                        sa.Column('payload', sa.JSON(), nullable=False))
        op.create_index('ix_v2_decision_links_ontology_id', 'v2_decision_links', ['ontology_id'])


def downgrade():
    op.drop_table('v2_decision_links')
    op.drop_table('v2_decisions')
