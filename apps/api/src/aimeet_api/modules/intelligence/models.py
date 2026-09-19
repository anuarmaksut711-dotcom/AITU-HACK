import uuid
from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from aimeet_api.db.models import Base, utcnow


class MeetingBoard(Base):
    __tablename__ = "meeting_boards"
    __table_args__ = (
        CheckConstraint("status IN ('idle','queued','running','ready','failed')", name="status"),
    )
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("meetings.id", ondelete="CASCADE"),
        primary_key=True,
    )
    version: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="idle")
    source_hash: Mapped[str | None] = mapped_column(String(64))
    cards: Mapped[list] = mapped_column(JSON, default=list)
    summary: Mapped[list] = mapped_column(JSON, default=list)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
