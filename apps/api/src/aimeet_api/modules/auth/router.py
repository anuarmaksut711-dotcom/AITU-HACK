from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import delete

from aimeet_api.core.dependencies import (
    CurrentUser,
    DatabaseDep,
    SettingsDep,
    require_csrf,
)
from aimeet_api.core.security import hash_token
from aimeet_api.db.models import AuthSession
from aimeet_api.modules.auth import service
from aimeet_api.modules.auth.schemas import LoginInput, UserOutput

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.get("/me", response_model=UserOutput, operation_id="getCurrentUser")
def me(user: CurrentUser):
    return user


@router.post(
    "/login",
    response_model=UserOutput,
    dependencies=[Depends(require_csrf)],
    operation_id="login",
)
def login(
    payload: LoginInput,
    request: Request,
    response: Response,
    db: DatabaseDep,
    settings: SettingsDep,
):
    user, token, expires_at = service.login(
        db,
        str(payload.email),
        payload.password,
        request.client.host if request.client else "unknown",
        request.cookies.get(settings.cookie_name),
        settings,
    )
    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        max_age=settings.session_ttl_seconds,
        expires=expires_at,
        path="/api",
    )
    return user


@router.post(
    "/logout", status_code=204, dependencies=[Depends(require_csrf)], operation_id="logout"
)
def logout(request: Request, db: DatabaseDep, settings: SettingsDep) -> Response:
    token = request.cookies.get(settings.cookie_name)
    if token:
        db.execute(delete(AuthSession).where(AuthSession.token_hash == hash_token(token)))
        db.commit()
    response = Response(status_code=204)
    response.delete_cookie(
        key=settings.cookie_name,
        path="/api",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
    )
    return response
