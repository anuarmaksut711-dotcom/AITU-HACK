import pytest
from pydantic import ValidationError

from aimeet_api.core.config import Settings


def test_declared_large_request_is_rejected_before_parsing(client, app):
    response = client.post(
        "/api/v1/auth/login",
        content=b"private-password-content",
        headers={"Content-Length": str(app.state.settings.max_request_bytes + 1)},
    )
    assert response.status_code == 413
    assert "private-password-content" not in response.text
    assert response.headers["x-request-id"] == response.json()["request_id"]


def test_chunked_large_request_is_rejected_without_trusting_content_length(client, app):
    chunks = (b"a" * 64_000 for _ in range(app.state.settings.max_request_bytes // 64_000 + 1))
    response = client.post("/api/v1/auth/login", content=chunks)
    assert response.status_code == 413


def test_cross_site_fetch_metadata_rejects_login(client, alice):
    response = client.post(
        "/api/v1/auth/login",
        json=alice.credentials,
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    "overrides",
    [
        {"cookie_secure": False},
        {"allowed_origins": "http://example.com"},
        {"database_url": "sqlite://"},
    ],
)
def test_production_rejects_insecure_configuration(overrides):
    settings = {
        "app_env": "production",
        "cookie_secure": True,
        "allowed_origins": "https://example.com",
        "database_url": "postgresql+psycopg://unused@localhost/unused",
    }
    with pytest.raises(ValidationError):
        Settings(**(settings | overrides))
