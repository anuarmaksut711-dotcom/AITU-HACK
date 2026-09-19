"""Meeting protocols, editable kanban and durable local analysis queue."""

import sqlalchemy as sa
from alembic import op

revision = "0004_meeting_boards"
down_revision = "0003_meeting_rag"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "meeting_boards",
        sa.Column(
            "meeting_id",
            sa.Uuid(),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("source_hash", sa.String(64)),
        sa.Column("cards", sa.JSON(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_token", sa.Uuid()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('idle','queued','running','ready','failed')", name="status"),
    )


def downgrade():
    op.drop_table("meeting_boards")
