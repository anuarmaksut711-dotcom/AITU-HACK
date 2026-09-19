"""Idempotent assistant task creation receipts."""

import sqlalchemy as sa
from alembic import op

revision = "0005_assistant_tasks"
down_revision = "0004_live_rooms"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "assistant_task_operations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_assistant_task_operations_workspace_id", "assistant_task_operations", ["workspace_id"]
    )

    op.create_table(
        "assistant_conversations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_assistant_conversations_workspace_id", "assistant_conversations", ["workspace_id"]
    )
    op.create_index("ix_assistant_conversations_user_id", "assistant_conversations", ["user_id"])
    op.create_table(
        "assistant_conversation_turns",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("assistant_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_assistant_conversation_turns_conversation_id",
        "assistant_conversation_turns",
        ["conversation_id"],
    )


def downgrade():
    op.drop_table("assistant_conversation_turns")
    op.drop_table("assistant_conversations")
    op.drop_table("assistant_task_operations")
