import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session, undefer

from aimeet_api.db.models import Meeting


class MeetingRepository:
    """Every read/write is explicitly scoped to the authenticated workspace."""

    def __init__(self, db: Session, workspace_id: uuid.UUID):
        self.db = db
        self.workspace_id = workspace_id

    def list(self, query: str | None, limit: int, offset: int) -> tuple[list[Meeting], int]:
        filters = [Meeting.workspace_id == self.workspace_id]
        if query:
            escaped = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            filters.append(Meeting.title.ilike(f"%{escaped}%", escape="\\"))
        total = self.db.scalar(select(func.count()).select_from(Meeting).where(*filters)) or 0
        items = self.db.scalars(
            select(Meeting)
            .where(*filters)
            .order_by(Meeting.created_at.desc(), Meeting.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return list(items), total

    def get(self, meeting_id: uuid.UUID) -> Meeting | None:
        return self.db.scalar(
            select(Meeting)
            .options(undefer(Meeting.transcript), undefer(Meeting.segments))
            .where(Meeting.workspace_id == self.workspace_id, Meeting.id == meeting_id)
        )
