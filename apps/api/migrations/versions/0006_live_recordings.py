"""Retained participant streams and durable whole-meeting recording assembly."""

import sqlalchemy as sa
from alembic import op

revision = "0006_live_recordings"
down_revision = "0005_assistant_tasks"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "live_rooms",
        sa.Column("recording_status", sa.String(16), nullable=False, server_default="none"),
    )
    op.add_column("live_rooms", sa.Column("recording_error", sa.String(48)))
    op.add_column("live_rooms", sa.Column("recording_lease", sa.Uuid()))
    op.add_column("live_rooms", sa.Column("recording_lease_until", sa.DateTime(timezone=True)))
    op.add_column(
        "live_rooms",
        sa.Column("recording_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "live_recording_streams",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "room_id", sa.Uuid(), sa.ForeignKey("live_rooms.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "participant_id",
            sa.Uuid(),
            sa.ForeignKey("live_participants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("start", sa.Float(), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_live_recording_streams_room_id", "live_recording_streams", ["room_id"])


def downgrade():
    op.drop_table("live_recording_streams")
    for column in (
        "recording_attempts",
        "recording_lease_until",
        "recording_lease",
        "recording_error",
        "recording_status",
    ):
        op.drop_column("live_rooms", column)
