"""Versioned pgvector meeting graph and durable indexing queue."""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "0003_meeting_rag"
down_revision = "0002_transcription"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
    op.create_table(
        "rag_indexes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "meeting_id",
            sa.Uuid(),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("profile", sa.String(64), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_token", sa.Uuid()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(64)),
        sa.Column("node_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "meeting_id", "profile", "source_hash", name="uq_rag_index_meeting_profile"
        ),
        sa.CheckConstraint("status IN ('queued','running','ready','failed')", name="status"),
    )
    op.create_index("ix_rag_indexes_meeting_id", "rag_indexes", ["meeting_id"])
    op.create_index("ix_rag_indexes_claim", "rag_indexes", ["status", "lease_until"])
    op.create_table(
        "rag_nodes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "index_id",
            sa.Uuid(),
            sa.ForeignKey("rag_indexes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.Uuid()),
        sa.Column("start_char", sa.Integer(), nullable=False),
        sa.Column("end_char", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector().with_variant(sa.JSON(), "sqlite")),
        sa.UniqueConstraint("index_id", "id", name="uq_rag_node_index_id"),
        sa.UniqueConstraint("index_id", "kind", "ordinal", name="uq_rag_node_ordinal"),
        sa.ForeignKeyConstraint(
            ["index_id", "parent_id"],
            ["rag_nodes.index_id", "rag_nodes.id"],
            name="fk_rag_node_parent_scope",
        ),
        sa.CheckConstraint("kind IN ('parent','child')", name="kind"),
        sa.CheckConstraint("start_char >= 0 AND end_char > start_char", name="offsets"),
        sa.CheckConstraint(
            "(kind = 'parent' AND parent_id IS NULL) OR (kind = 'child' AND parent_id IS NOT NULL)",
            name="parent_kind",
        ),
    )
    op.create_index("ix_rag_nodes_index_kind", "rag_nodes", ["index_id", "kind"])
    op.create_table(
        "rag_edges",
        sa.Column("index_id", sa.Uuid(), primary_key=True),
        sa.Column("source_id", sa.Uuid(), primary_key=True),
        sa.Column("target_id", sa.Uuid(), primary_key=True),
        sa.Column("relation", sa.String(8), primary_key=True),
        sa.ForeignKeyConstraint(
            ["index_id", "source_id"],
            ["rag_nodes.index_id", "rag_nodes.id"],
            ondelete="CASCADE",
            name="fk_rag_edge_source_scope",
        ),
        sa.ForeignKeyConstraint(
            ["index_id", "target_id"],
            ["rag_nodes.index_id", "rag_nodes.id"],
            ondelete="CASCADE",
            name="fk_rag_edge_target_scope",
        ),
        sa.CheckConstraint("relation IN ('child','parent','next','previous')", name="relation"),
    )


def downgrade():
    op.drop_table("rag_edges")
    op.drop_table("rag_nodes")
    op.drop_table("rag_indexes")
    # The extension may be shared by other modules; preserve it.
