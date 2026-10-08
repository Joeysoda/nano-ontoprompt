import importlib.util
from pathlib import Path
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect


def test_decision_migration_upgrade_and_downgrade():
    path = Path(__file__).resolve().parents[1] / 'alembic/versions/2026_09_10_0017_decisions.py'
    spec = importlib.util.spec_from_file_location('decision_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine('sqlite://')
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        assert set(inspect(connection).get_table_names()) == {'v2_decisions','v2_decision_links'}
        migration.downgrade()
        assert inspect(connection).get_table_names() == []
    engine.dispose()
