import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException
from starlette.middleware.cors import CORSMiddleware

from aimeet_api.core.config import Settings
from aimeet_api.core.dependencies import DatabaseDep
from aimeet_api.core.middleware import RequestBoundaryMiddleware
from aimeet_api.core.middleware import logger as request_logger
from aimeet_api.core.schemas import ErrorResponse
from aimeet_api.db.session import create_engine_and_session
from aimeet_api.modules.auth.router import router as auth_router
from aimeet_api.modules.intelligence.router import router as intelligence_router
from aimeet_api.modules.live.audio import router as live_audio_router
from aimeet_api.modules.live.router import router as live_router
from aimeet_api.modules.meetings.router import router as meetings_router
from aimeet_api.modules.rag.providers import RagError
from aimeet_api.modules.rag.router import router as rag_router
from aimeet_api.modules.transcription.router import router as transcription_router


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings()
    engine, session_factory = create_engine_and_session(config)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Schema changes and initial accounts are explicit one-shot operational commands.
        yield
        engine.dispose()

    application = FastAPI(
        title="AI Meet API",
        version="0.2.0",
        description="Local meeting archive and offline audio transcription.",
        docs_url="/api/docs" if config.app_env == "development" else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if config.app_env == "development" else None,
        lifespan=lifespan,
        responses={
            code: {"model": ErrorResponse}
            for code in (400, 401, 403, 404, 409, 413, 415, 422, 429, 500)
        },
    )
    application.state.settings = config
    application.state.engine = engine
    application.state.session_factory = session_factory
    application.add_middleware(
        CORSMiddleware,
        allow_origins=config.origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "X-Requested-With"],
        expose_headers=["X-Request-ID"],
    )
    application.add_middleware(
        RequestBoundaryMiddleware,
        max_request_bytes=config.max_request_bytes,
        max_audio_bytes=config.max_audio_bytes,
    )

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return JSONResponse(
            {
                "error": {"code": f"HTTP_{exc.status_code}", "message": str(exc.detail)},
                "request_id": request.state.request_id,
            },
            status_code=exc.status_code,
            headers=exc.headers,
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Pydantic includes the original input by default. Never return credentials/transcripts.
        details = [
            {"path": list(error["loc"]), "type": error["type"], "message": error["msg"]}
            for error in exc.errors()
        ]
        return JSONResponse(
            {
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Invalid request",
                    "details": details,
                },
                "request_id": request.state.request_id,
            },
            status_code=422,
        )

    @application.exception_handler(RagError)
    async def rag_error(request: Request, exc: RagError):
        request_logger.warning(
            json.dumps(
                {
                    "event": "rag_error",
                    "request_id": request.state.request_id,
                    "code": exc.code,
                    "status": exc.status_code,
                }
            )
        )
        return JSONResponse(
            {
                "error": {"code": exc.code, "message": exc.code},
                "request_id": request.state.request_id,
            },
            status_code=exc.status_code,
        )

    @application.get("/api/health/live", tags=["Health"], operation_id="getLiveness")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/api/health/ready", tags=["Health"], operation_id="getReadiness")
    def ready(db: DatabaseDep):
        try:
            db.execute(text("SELECT 1"))
            return {"status": "ok"}
        except SQLAlchemyError:
            return JSONResponse({"status": "unavailable"}, status_code=503)

    application.include_router(auth_router, prefix="/api/v1")
    application.include_router(live_router, prefix="/api/v1")
    application.include_router(live_audio_router, prefix="/api/v1")
    application.include_router(transcription_router, prefix="/api/v1")
    application.include_router(meetings_router, prefix="/api/v1")
    application.include_router(rag_router, prefix="/api/v1")
    application.include_router(intelligence_router, prefix="/api/v1")
    return application


app = create_app()
