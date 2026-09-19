from alembic import context
from sqlalchemy import create_engine, pool

from aimeet_api.core.config import Settings
from aimeet_api.db.models import Base
from aimeet_api.modules.intelligence import models as intelligence_models  # noqa: F401
from aimeet_api.modules.live import models as live_models  # noqa: F401
from aimeet_api.modules.rag import models as rag_models  # noqa: F401

config = context.config
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=Settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(Settings().database_url, poolclass=pool.NullPool, hide_parameters=True)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
