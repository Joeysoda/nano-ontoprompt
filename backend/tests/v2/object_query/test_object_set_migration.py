"""Incremental migration against real SQLite and isolated PostgreSQL schema."""
import os
from uuid import uuid4
import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory


def test_sqlite_full_upgrade_preserves_data(tmp_path,monkeypatch):
    url='sqlite:///'+str(tmp_path/'migration.db')
    monkeypatch.setenv('DATABASE_URL',url)
    config=Config('alembic.ini')
    command.upgrade(config,'0020_logic_contracts')
    engine=sa.create_engine(url)
    with engine.begin() as conn:
        conn.execute(sa.text('CREATE TABLE user_sentinel (value TEXT)'))
        conn.execute(sa.text("INSERT INTO user_sentinel VALUES ('preserve')"))
    command.upgrade(config,'head')
    command.upgrade(config,'head')
    with engine.connect() as conn:
        assert conn.execute(sa.text('SELECT value FROM user_sentinel')).scalar_one()=='preserve'
        assert conn.execute(sa.text('SELECT version_num FROM alembic_version')).scalar_one()==ScriptDirectory.from_config(config).get_current_head()
        assert len([v for v in sa.inspect(conn).get_table_names() if v.startswith('v2_object_set_')])==7
        assert 'v2_query_data_views' in sa.inspect(conn).get_table_names()
        assert 'v2_query_jobs' in sa.inspect(conn).get_table_names()
    engine.dispose()


def test_postgres_incremental_migration_preserves_data():
    url=os.environ.get('OBJECT_SET_TEST_POSTGRES_URL')
    if not url: pytest.skip('Set OBJECT_SET_TEST_POSTGRES_URL to run isolated PostgreSQL migration')
    schema='object_set_test_'+uuid4().hex
    engine=sa.create_engine(url)
    config=Config('alembic.ini')
    try:
        with engine.begin() as conn:
            conn.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
            conn.execute(sa.text(f'SET LOCAL search_path TO "{schema}"'))
            config.attributes['connection'] = conn
            command.upgrade(config, '0020_logic_contracts')
            conn.execute(sa.text('CREATE TABLE user_sentinel (value TEXT)'))
            conn.execute(sa.text("INSERT INTO user_sentinel VALUES ('preserve')"))
            command.upgrade(config, 'head')
            command.upgrade(config, 'head')
            assert conn.execute(sa.text('SELECT value FROM user_sentinel')).scalar_one() == 'preserve'
            assert conn.execute(sa.text('SELECT version_num FROM alembic_version')).scalar_one() == ScriptDirectory.from_config(config).get_current_head()
            assert 'v2_query_data_views' in sa.inspect(conn).get_table_names(schema=schema)
            assert 'v2_query_jobs' in sa.inspect(conn).get_table_names(schema=schema)
            assert len([v for v in sa.inspect(conn).get_table_names(schema=schema) if v.startswith('v2_object_set_')])==7
    finally:
        with engine.begin() as conn: conn.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()
