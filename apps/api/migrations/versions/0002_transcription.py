"""Local audio sources and recoverable transcription jobs."""

import sqlalchemy as sa
from alembic import op

revision = "0002_transcription"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Early local installs predate op.f() in 0001 and applied the convention twice.
    existing = {
        item["name"] for item in sa.inspect(op.get_bind()).get_check_constraints("meetings")
    }
    with op.batch_alter_table("meetings") as batch:
        for name in ("language", "status", "source_type", "transcript_length"):
            candidates = {f"ck_meetings_{name}", f"ck_meetings_ck_meetings_{name}"} & existing
            if not candidates:
                raise RuntimeError(f"Expected meeting constraint is missing: {name}")
            for actual_name in candidates:
                batch.drop_constraint(op.f(actual_name), type_="check")
        batch.create_check_constraint("language", "language IN ('auto', 'ru', 'kk', 'en')")
        batch.create_check_constraint("status", "status IN ('draft', 'transcribed')")
        batch.create_check_constraint("source_type", "source_type IN ('text', 'audio')")
        batch.create_check_constraint("transcript_length", "transcript_length >= 0")
        batch.add_column(sa.Column("audio_filename", sa.String(255)))
        batch.add_column(sa.Column("audio_bytes", sa.Integer()))
        batch.add_column(sa.Column("audio_sha256", sa.String(64)))
        batch.add_column(sa.Column("segments", sa.JSON()))
    op.create_table(
        "transcription_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "meeting_id",
            sa.Uuid(),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_token", sa.Uuid()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(48)),
        sa.Column("detected_language", sa.String(16)),
        sa.Column("duration_seconds", sa.Float()),
        sa.Column("config", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name=op.f("ck_transcription_jobs_status"),
        ),
        sa.CheckConstraint(
            "progress >= 0 AND progress <= 100", name=op.f("ck_transcription_jobs_progress")
        ),
    )
    op.create_index(
        "ix_transcription_jobs_claim", "transcription_jobs", ["status", "lease_until", "created_at"]
    )


def downgrade() -> None:
    # An automatic downgrade would either lose recordings or violate the old text constraints.
    raise RuntimeError("Restore the pre-migration backup to roll back audio support safely.")
