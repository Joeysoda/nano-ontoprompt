import atexit
import os
import tempfile

# The application default is Docker's host.docker.internal. Test processes run
# on the host, where the real local FalkorDB service is exposed on loopback.
os.environ.setdefault("FALKORDB_HOST", "127.0.0.1")
os.environ.setdefault("FALKORDB_PORT", "6381")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.main import app
from app.database import Base
from app.deps import get_db
from app.limiter import limiter
from app.services.auth_service import hash_password
from app.models.user import User
from app.config import settings
import uuid

# The Docker desktop overlay may set local_single_user for manual use. Tests
# exercise the JWT contract explicitly and must remain deterministic.
settings.auth_mode = "jwt"

# 测试环境关闭限流，避免连续登录/注册被 429
limiter.enabled = False

# 每次 pytest 运行使用独立的临时 SQLite, 避免并发运行互相锁库
_db_fd, _db_path = tempfile.mkstemp(prefix="ontoprompt_test_", suffix=".db")
os.close(_db_fd)

TEST_DB = f"sqlite:///{_db_path}"
engine = create_engine(TEST_DB, connect_args={"check_same_thread": False})
TestSession = sessionmaker(bind=engine)


@atexit.register
def _cleanup_test_db():
    engine.dispose()
    try:
        os.unlink(_db_path)
    except OSError:
        pass

@pytest.fixture(autouse=True)
def setup_db():
    # Import all models
    from app.models import user, ontology, file, prompt, model_config, entity
    from app.models import logic, action, relation, extraction_task, rules_config
    from app.models.v2 import query_view  # noqa: F401
    from app.models.v2 import query_job  # noqa: F401
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)

@pytest.fixture
def db():
    session = TestSession()
    try:
        yield session
    finally:
        session.close()

@pytest.fixture
def client(db):
    def override_get_db():
        yield db
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()

@pytest.fixture
def admin_user(db):
    user = User(id=str(uuid.uuid4()), username="admin", email="admin@test.com",
                password_hash=hash_password("admin123"), role="admin")
    db.add(user); db.commit(); db.refresh(user)
    return user

@pytest.fixture
def editor_user(db):
    user = User(id=str(uuid.uuid4()), username="editor", email="editor@test.com",
                password_hash=hash_password("editor123"), role="editor")
    db.add(user); db.commit(); db.refresh(user)
    return user

@pytest.fixture
def admin_token(client, admin_user):
    r = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
    return r.json()["data"]["access_token"]

@pytest.fixture
def auth_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}

@pytest.fixture
def ontology(client, auth_headers, db):
    r = client.post("/api/v1/ontologies", json={"name": "测试本体", "domain": "供应链"}, headers=auth_headers)
    return r.json()["data"]
