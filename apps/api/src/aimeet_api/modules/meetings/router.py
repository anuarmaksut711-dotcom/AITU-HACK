import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import delete, select

from aimeet_api.core.dependencies import CurrentUser, DatabaseDep, SettingsDep, require_csrf
from aimeet_api.db.models import Meeting
from aimeet_api.modules.live.audio import chunk_path
from aimeet_api.modules.live.models import LiveChunk, LiveRecordingStream, LiveRoom
from aimeet_api.modules.live.recordings import recording_path
from aimeet_api.modules.meetings.playback import router as playback_router
from aimeet_api.modules.meetings.repository import MeetingRepository
from aimeet_api.modules.meetings.schemas import (
    MeetingCreate,
    MeetingDetail,
    MeetingList,
    MeetingSummary,
)
from aimeet_api.modules.transcription.storage import audio_path

router = APIRouter(prefix="/meetings", tags=["Meetings"])
router.include_router(playback_router)


@router.get("", response_model=MeetingList, operation_id="listMeetings")
def list_meetings(
    user: CurrentUser,
    db: DatabaseDep,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> MeetingList:
    items, total = MeetingRepository(db, user.workspace_id).list(q, limit, offset)
    return MeetingList(
        items=[MeetingSummary.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "",
    response_model=MeetingDetail,
    status_code=201,
    dependencies=[Depends(require_csrf)],
    operation_id="createMeeting",
)
def create_meeting(payload: MeetingCreate, user: CurrentUser, db: DatabaseDep):
    meeting = Meeting(
        workspace_id=user.workspace_id,
        created_by=user.id,
        title=payload.title,
        language=payload.language,
        transcript=payload.transcript,
        transcript_length=len(payload.transcript),
    )
    db.add(meeting)
    db.commit()
    db.refresh(meeting)
    return meeting


@router.get("/{meeting_id}", response_model=MeetingDetail, operation_id="getMeeting")
def get_meeting(meeting_id: uuid.UUID, user: CurrentUser, db: DatabaseDep):
    meeting = MeetingRepository(db, user.workspace_id).get(meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return meeting


@router.delete(
    "/{meeting_id}",
    status_code=204,
    dependencies=[Depends(require_csrf)],
    operation_id="deleteMeeting",
)
def delete_meeting(
    meeting_id: uuid.UUID, user: CurrentUser, db: DatabaseDep, settings: SettingsDep
) -> Response:
    meeting = MeetingRepository(db, user.workspace_id).get(meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    clip_ids = list(
        db.scalars(
            select(LiveChunk.id)
            .join(LiveRoom)
            .where(
                LiveRoom.meeting_id == meeting_id,
            )
        )
    )
    stream_ids = list(
        db.scalars(
            select(LiveRecordingStream.id)
            .join(LiveRoom)
            .where(
                LiveRoom.meeting_id == meeting_id,
            )
        )
    )
    db.execute(delete(LiveRoom).where(LiveRoom.meeting_id == meeting_id))
    db.delete(meeting)
    db.commit()
    audio_path(settings, meeting_id).unlink(missing_ok=True)
    for clip_id in clip_ids:
        chunk_path(settings, clip_id).unlink(missing_ok=True)
    for stream_id in stream_ids:
        recording_path(settings, stream_id).unlink(missing_ok=True)
    return Response(status_code=204)
