from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie
from uuid import UUID

from sqlalchemy import select

from aimeet_api.db.models import AuthSession, User


def test_protected_endpoints_require_a_session(client):
    for path in ("/api/v1/auth/me", "/api/v1/meetings"):
        assert client.get(path).status_code == 401


def test_login_creates_private_session_and_returns_public_identity(client, alice):
    response = client.post("/api/v1/auth/login", json=alice.credentials)

    assert response.status_code == 200
    assert response.json() == alice.identity
    assert client.get("/api/v1/auth/me").json() == alice.identity

    cookies = SimpleCookie()
    cookies.load(response.headers["set-cookie"])
    assert cookies
    session_cookie = next(iter(cookies.values()))
    assert session_cookie["httponly"]
    assert session_cookie["secure"]
    assert session_cookie["samesite"].lower() in {"lax", "strict"}
    assert "password" not in response.text


def test_invalid_password_and_unknown_account_have_same_public_response(client, alice):
    bad_password = client.post(
        "/api/v1/auth/login",
        json={"email": alice.email, "password": "wrong-password"},
    )
    unknown_account = client.post(
        "/api/v1/auth/login",
        json={"email": "unknown@example.com", "password": "wrong-password"},
    )

    assert bad_password.status_code == unknown_account.status_code == 401
    assert bad_password.json()["error"] == unknown_account.json()["error"]
    assert client.get("/api/v1/auth/me").status_code == 401


def test_logout_invalidates_the_session_even_if_the_cookie_is_replayed(client, alice):
    assert client.post("/api/v1/auth/login", json=alice.credentials).status_code == 200
    original_cookies = dict(client.cookies)

    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401

    client.cookies.update(original_cookies)
    assert client.get("/api/v1/auth/me").status_code == 401


def test_login_requires_csrf_header(client, alice):
    client.headers.pop("X-Requested-With", None)
    response = client.post("/api/v1/auth/login", json=alice.credentials)
    assert response.status_code == 403
    assert client.get("/api/v1/auth/me").status_code == 401


def test_mutations_reject_an_untrusted_origin(client, alice):
    response = client.post(
        "/api/v1/auth/login",
        json=alice.credentials,
        headers={"Origin": "https://attacker.invalid"},
    )
    assert response.status_code == 403
    assert client.get("/api/v1/auth/me").status_code == 401


def test_repeated_failed_logins_are_throttled(client, alice):
    statuses = []
    for _ in range(25):
        response = client.post(
            "/api/v1/auth/login",
            json={"email": alice.email, "password": "wrong-password"},
        )
        statuses.append(response.status_code)
        if response.status_code == 429:
            break

    assert statuses[0] == 401
    assert statuses[-1] == 429
    assert client.get("/api/v1/auth/me").status_code == 401


def test_expired_session_is_rejected(client, alice, app):
    assert client.post("/api/v1/auth/login", json=alice.credentials).status_code == 200
    with app.state.session_factory() as session:
        auth_session = session.scalar(select(AuthSession))
        assert auth_session is not None
        auth_session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()

    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.get("/api/v1/meetings").status_code == 401


def test_disabling_a_user_rejects_existing_sessions_and_new_logins(client, alice, app):
    assert client.post("/api/v1/auth/login", json=alice.credentials).status_code == 200
    with app.state.session_factory() as session:
        user = session.get(User, UUID(alice.id))
        assert user is not None
        user.is_active = False
        session.commit()

    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.post("/api/v1/auth/login", json=alice.credentials).status_code == 401


def test_validation_errors_do_not_echo_credentials(client):
    password = "private-password-that-must-never-appear-in-errors"
    response = client.post(
        "/api/v1/auth/login", json={"email": "not-an-email", "password": password}
    )

    assert response.status_code == 422
    assert password not in response.text
    assert response.json()["request_id"]
