from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url


@pytest.mark.parametrize("legacy_names", [False, True])
def test_transcription_migration_handles_legacy_constraint_names(
    isolated_database_url,
    monkeypatch,
    legacy_names,
):
    url = make_url(isolated_database_url)
    if url.get_backend_name() != "postgresql":
        pytest.skip("Physical legacy PostgreSQL schema regression")
    # Hide any public alembic_version from this independent schema's migration context.
    options = url.query["options"].replace(",public", "")
    url = url.update_query_dict({"options": options})
    monkeypatch.setenv("DATABASE_URL", url.render_as_string(hide_password=False))
    monkeypatch.setenv("APP_ENV", "development")
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, "0001_foundation")
    engine = create_engine(url)
    try:
        if legacy_names:
            with engine.begin() as connection:
                for name in ("language", "status", "source_type", "transcript_length"):
                    connection.execute(
                        text(
                            f"ALTER TABLE meetings RENAME CONSTRAINT ck_meetings_{name} "
                            f"TO ck_meetings_ck_meetings_{name}"
                        )
                    )
        command.upgrade(config, "0002_transcription")
        constraints = {item["name"] for item in inspect(engine).get_check_constraints("meetings")}
        assert constraints == {
            "ck_meetings_language",
            "ck_meetings_status",
            "ck_meetings_source_type",
            "ck_meetings_transcript_length",
        }
        assert "audio_sha256" in {
            column["name"] for column in inspect(engine).get_columns("meetings")
        }
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0002_transcription"
            )
    finally:
        engine.dispose()
