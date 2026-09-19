import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from aimeet_api.db.models import Base, utcnow


class LiveRoom(Base):
    __tablename__ = "live_rooms"
    __table_args__ = (CheckConstraint("status IN ('active', 'ending', 'ended')", name="status"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(200))
    language: Mapped[str] = mapped_column(String(4), default="auto")
    status: Mapped[str] = mapped_column(String(20), default="active")
    invite_nonce: Mapped[uuid.UUID] = mapped_column(Uuid, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    meeting_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("meetings.id", ondelete="SET NULL")
    )
    analysis_status: Mapped[str] = mapped_column(String(24), default="waiting")
    analysis_error: Mapped[str | None] = mapped_column(String(48))
    analysis_through: Mapped[int] = mapped_column(Integer, default=0)
    analysis_lease: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    analysis_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    analysis_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    transcript_revision: Mapped[int] = mapped_column(Integer, default=0)
    audio_error: Mapped[str | None] = mapped_column(String(48))
    insights: Mapped[list] = mapped_column(JSON, default=list)
    recording_status: Mapped[str] = mapped_column(String(16), default="none")
    recording_error: Mapped[str | None] = mapped_column(String(48))
    recording_lease: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    recording_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recording_attempts: Mapped[int] = mapped_column(Integer, default=0)


class LiveParticipant(Base):
    __tablename__ = "live_participants"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    room_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("live_rooms.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(String(80))
    is_host: Mapped[bool] = mapped_column(default=False)
    revoked: Mapped[bool] = mapped_column(default=False)
    stream_token: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LiveChunk(Base):
    __tablename__ = "live_chunks"
    __table_args__ = (
        CheckConstraint("status IN ('queued', 'running', 'done', 'failed')", name="status"),
        Index("ix_live_chunks_claim", "status", "lease_until", "created_at"),
        Index("ix_live_chunks_room_start", "room_id", "start"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    room_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("live_rooms.id", ondelete="CASCADE")
    )
    participant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("live_participants.id", ondelete="CASCADE")
    )
    start: Mapped[float] = mapped_column(Float)
    end: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    text: Mapped[str] = mapped_column(Text, default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(48))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LiveRecordingStream(Base):
    __tablename__ = "live_recording_streams"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    room_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("live_rooms.id", ondelete="CASCADE"),
        index=True,
    )
    participant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("live_participants.id", ondelete="CASCADE"),
    )
    start: Mapped[float] = mapped_column(Float)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
