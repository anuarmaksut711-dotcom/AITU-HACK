"""Workspace identity, revocable sessions, login throttle, immutable text meetings."""

import sqlalchemy as sa
from alembic import op

revision = "0001_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_workspace_id", "users", ["workspace_id"])
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("token_hash", name="uq_auth_sessions_token_hash"),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])
    op.create_table(
        "login_throttles",
        sa.Column("key", sa.String(80), primary_key=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
    )
    op.create_index("ix_login_throttles_window_start", "login_throttles", ["window_start"])
    op.create_table(
        "meetings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("language", sa.String(4), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("source_type", sa.String(24), nullable=False),
        sa.Column("transcript", sa.Text(), nullable=False),
        sa.Column("transcript_length", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "language IN ('auto', 'ru', 'kk', 'en')", name=op.f("ck_meetings_language")
        ),
        sa.CheckConstraint("status = 'draft'", name=op.f("ck_meetings_status")),
        sa.CheckConstraint("source_type = 'text'", name=op.f("ck_meetings_source_type")),
        sa.CheckConstraint("transcript_length >= 1", name=op.f("ck_meetings_transcript_length")),
    )
    op.create_index(
        "ix_meetings_workspace_created_id", "meetings", ["workspace_id", "created_at", "id"]
    )


def downgrade() -> None:
    op.drop_table("meetings")
    op.drop_table("login_throttles")
    op.drop_table("auth_sessions")
    op.drop_table("users")
    op.drop_table("workspaces")
