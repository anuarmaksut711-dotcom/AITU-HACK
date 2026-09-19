from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import case, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from aimeet_api.core.config import Settings
from aimeet_api.core.security import (
    hash_password,
    hash_token,
    new_session_token,
    password_needs_rehash,
    verify_password,
)
from aimeet_api.db.models import AuthSession, LoginThrottle, User, utcnow


def reserve_login_attempt(db: Session, email: str, peer: str, settings: Settings) -> None:
    """Atomic per-account and per-peer counters, shared by every API process.

    Forwarded IP headers are intentionally ignored; only a configured trusted proxy may
    translate them into the ASGI client address. Credentials never enter this table.
    """
    now = utcnow()
    epoch = int(now.timestamp())
    window = datetime.fromtimestamp(epoch - epoch % settings.login_window_seconds, tz=UTC)
    insert = pg_insert if db.bind.dialect.name == "postgresql" else sqlite_insert
    exceeded = False
    for namespace, value, limit in (
        ("account", email, settings.login_attempt_limit),
        ("peer", peer, settings.login_ip_attempt_limit),
    ):
        stmt = insert(LoginThrottle).values(
            key=f"{namespace}:{hash_token(value)}", window_start=window, attempts=1
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[LoginThrottle.key],
            set_={
                "window_start": window,
                "attempts": case(
                    (LoginThrottle.window_start < window, 1),
                    else_=LoginThrottle.attempts + 1,
                ),
            },
        ).returning(LoginThrottle.attempts)
        attempts = db.scalar(stmt)
        exceeded = exceeded or attempts > limit
    db.execute(delete(LoginThrottle).where(LoginThrottle.window_start < now - timedelta(days=1)))
    # A rejected password must still persist its reserved attempt.
    db.commit()
    if exceeded:
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts. Try again later",
            headers={
                "Retry-After": str(
                    settings.login_window_seconds - epoch % settings.login_window_seconds
                )
            },
        )


def login(
    db: Session,
    email: str,
    password: str,
    peer: str,
    previous_token: str | None,
    settings: Settings,
) -> tuple[User, str, datetime]:
    normalized_email = email.strip().casefold()
    reserve_login_attempt(db, normalized_email, peer, settings)
    user = db.scalar(select(User).where(User.email == normalized_email))
    # Release the connection before potentially queued, memory-bounded Argon2 work.
    db.commit()
    password_valid = verify_password(password, user.password_hash if user else None)
    if not password_valid or user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    if previous_token:
        db.execute(delete(AuthSession).where(AuthSession.token_hash == hash_token(previous_token)))
    db.execute(delete(AuthSession).where(AuthSession.expires_at <= utcnow()))
    token = new_session_token()
    expires_at = utcnow() + timedelta(seconds=settings.session_ttl_seconds)
    db.add(AuthSession(user_id=user.id, token_hash=hash_token(token), expires_at=expires_at))
    db.commit()
    return user, token, expires_at
