from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from aimeet_api.core.config import Settings


def create_engine_and_session(settings: Settings) -> tuple[Engine, sessionmaker[Session]]:
    kwargs: dict = {"pool_pre_ping": True, "hide_parameters": True}
    if settings.database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs.update(pool_size=5, max_overflow=10, pool_timeout=10)
    engine = create_engine(settings.database_url, **kwargs)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def sqlite_foreign_keys(connection, _record):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine, sessionmaker(engine, expire_on_commit=False, autoflush=False)
