"""API integration fixtures with a separate database namespace for every test.

SQLite is the fast local default. Set TEST_DATABASE_URL to a dedicated PostgreSQL
test database to exercise the production dialect; each test creates and removes
only its own randomly named schema. The application's DATABASE_URL is never used.
"""

import os
from dataclasses import dataclass
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema

from aimeet_api.core.config import Settings
from aimeet_api.core.security import hash_password
from aimeet_api.db.models import Base, User, Workspace
from aimeet_api.main import create_app


@dataclass(frozen=True)
class Account:
    id: str
    email: str
    display_name: str
    password: str

    @property
    def credentials(self):
        return {"email": self.email, "password": self.password}

    @property
    def identity(self):
        return {"id": self.id, "email": self.email, "display_name": self.display_name}


@pytest.fixture
def isolated_database_url(tmp_path):
    configured = os.environ.get("TEST_DATABASE_URL")
    if not configured:
        yield f"sqlite:///{tmp_path / 'test.db'}"
        return

    url = make_url(configured)
    if url.get_backend_name() != "postgresql":
        raise ValueError("TEST_DATABASE_URL must point to a dedicated PostgreSQL test database")

    schema_name = f"aimeet_test_{uuid4().hex}"
    admin_engine = create_engine(url)
    with admin_engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public"))
        connection.execute(CreateSchema(schema_name))
    options = f"{url.query.get('options', '')} -csearch_path={schema_name},public".strip()
    scoped_url = url.update_query_dict({"options": options})
    try:
        yield scoped_url.render_as_string(hide_password=False)
    finally:
        with admin_engine.begin() as connection:
            connection.execute(DropSchema(schema_name, cascade=True))
        admin_engine.dispose()


@pytest.fixture
def app(isolated_database_url, monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DATABASE_URL", isolated_database_url)
    settings = Settings(
        _env_file=None,
        database_url=isolated_database_url,
        app_env="development",
        allowed_origins="https://testserver",
        cookie_secure=True,
        login_attempt_limit=5,
        login_ip_attempt_limit=30,
    )
    application = create_app(settings)
    # Do not let checkfirst see migrated public tables through the vector extension search_path.
    Base.metadata.create_all(application.state.engine, checkfirst=False)
    yield application
    application.state.engine.dispose()


@pytest.fixture
def client(app):
    with TestClient(
        app,
        base_url="https://testserver",
        headers={"X-Requested-With": "aimeet"},
    ) as test_client:
        yield test_client


def seed_account(app, *, email, display_name, workspace_name):
    password = "Test-only-password-732!"
    with app.state.session_factory() as session:
        workspace = Workspace(name=workspace_name)
        session.add(workspace)
        session.flush()
        user = User(
            email=email,
            display_name=display_name,
            password_hash=hash_password(password),
            workspace_id=workspace.id,
        )
        session.add(user)
        session.flush()
        account = Account(str(user.id), email, display_name, password)
        session.commit()
    return account


@pytest.fixture
def alice(app):
    return seed_account(
        app, email="alice@example.com", display_name="Алия", workspace_name="Workspace A"
    )


@pytest.fixture
def bob(app):
    return seed_account(
        app, email="bob@example.com", display_name="Марат", workspace_name="Workspace B"
    )


@pytest.fixture
def authenticated_client(client, alice):
    response = client.post("/api/v1/auth/login", json=alice.credentials)
    assert response.status_code == 200, response.text
    return client
