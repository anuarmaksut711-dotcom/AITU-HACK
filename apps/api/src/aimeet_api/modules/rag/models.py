import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from aimeet_api.db.models import Base, utcnow


class RagIndex(Base):
    __tablename__ = "rag_indexes"
    __table_args__ = (
        UniqueConstraint(
            "meeting_id", "profile", "source_hash", name="uq_rag_index_meeting_profile"
        ),
        CheckConstraint("status IN ('queued','running','ready','failed')", name="status"),
        Index("ix_rag_indexes_claim", "status", "lease_until"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("meetings.id", ondelete="CASCADE"), index=True
    )
    profile: Mapped[str] = mapped_column(String(64))
    source_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    node_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RagNode(Base):
    __tablename__ = "rag_nodes"
    __table_args__ = (
        UniqueConstraint("index_id", "id", name="uq_rag_node_index_id"),
        UniqueConstraint("index_id", "kind", "ordinal", name="uq_rag_node_ordinal"),
        ForeignKeyConstraint(
            ["index_id", "parent_id"],
            ["rag_nodes.index_id", "rag_nodes.id"],
            name="fk_rag_node_parent_scope",
        ),
        CheckConstraint("kind IN ('parent','child')", name="kind"),
        CheckConstraint("start_char >= 0 AND end_char > start_char", name="offsets"),
        CheckConstraint(
            "(kind = 'parent' AND parent_id IS NULL) OR (kind = 'child' AND parent_id IS NOT NULL)",
            name="parent_kind",
        ),
        Index("ix_rag_nodes_index_kind", "index_id", "kind"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    index_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("rag_indexes.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(8))
    ordinal: Mapped[int] = mapped_column(Integer)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    start_char: Mapped[int] = mapped_column(Integer)
    end_char: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    # Unconstrained dimensions permit independent embedding profiles in one table.
    # SQLite is exclusively a test fallback. Production uses native pgvector cosine.
    embedding: Mapped[list[float] | None] = mapped_column(Vector().with_variant(JSON(), "sqlite"))


class RagEdge(Base):
    __tablename__ = "rag_edges"
    __table_args__ = (
        ForeignKeyConstraint(
            ["index_id", "source_id"],
            ["rag_nodes.index_id", "rag_nodes.id"],
            ondelete="CASCADE",
            name="fk_rag_edge_source_scope",
        ),
        ForeignKeyConstraint(
            ["index_id", "target_id"],
            ["rag_nodes.index_id", "rag_nodes.id"],
            ondelete="CASCADE",
            name="fk_rag_edge_target_scope",
        ),
        CheckConstraint("relation IN ('child','parent','next','previous')", name="relation"),
    )
    index_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    source_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    target_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    relation: Mapped[str] = mapped_column(String(8), primary_key=True)


class AssistantTaskOperation(Base):
    __tablename__ = "assistant_task_operations"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    payload_hash: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AssistantConversation(Base):
    __tablename__ = "assistant_conversations"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(100), default="Новый чат")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AssistantConversationTurn(Base):
    __tablename__ = "assistant_conversation_turns"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("assistant_conversations.id", ondelete="CASCADE"), index=True
    )
    question: Mapped[str] = mapped_column(Text)
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
