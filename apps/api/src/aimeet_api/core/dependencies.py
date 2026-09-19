from collections.abc import Generator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from fastapi.security import APIKeyCookie
from sqlalchemy import select
from sqlalchemy.orm import Session

from aimeet_api.core.config import Settings
from aimeet_api.core.security import hash_token
from aimeet_api.db.models import AuthSession, User, utcnow


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Generator[Session, None, None]:
    with request.app.state.session_factory() as session:
        yield session


SettingsDep = Annotated[Settings, Depends(get_settings)]
DatabaseDep = Annotated[Session, Depends(get_db)]
session_cookie = APIKeyCookie(name="aimeet_session", auto_error=False)


def current_user(
    request: Request,
    db: DatabaseDep,
    settings: SettingsDep,
    _cookie: Annotated[str | None, Depends(session_cookie)],
) -> User:
    token = request.cookies.get(settings.cookie_name)
    if not token or len(token) > 256:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = db.scalar(
        select(User)
        .join(AuthSession, AuthSession.user_id == User.id)
        .where(
            AuthSession.token_hash == hash_token(token),
            AuthSession.expires_at > utcnow(),
            User.is_active.is_(True),
        )
    )
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def require_csrf(
    request: Request,
    settings: SettingsDep,
    x_requested_with: Annotated[str | None, Header()] = None,
) -> None:
    if x_requested_with != "aimeet":
        raise HTTPException(status_code=403, detail="Request verification failed")
    origin = request.headers.get("origin")
    if origin is not None and origin not in settings.origins:
        raise HTTPException(status_code=403, detail="Request origin is not allowed")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(status_code=403, detail="Cross-site requests are not allowed")
