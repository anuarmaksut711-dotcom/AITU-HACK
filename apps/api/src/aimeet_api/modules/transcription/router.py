import uuid
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import update

from aimeet_api.core.dependencies import CurrentUser, DatabaseDep, SettingsDep, require_csrf
from aimeet_api.db.models import Meeting, TranscriptionJob
from aimeet_api.modules.meetings.repository import MeetingRepository
from aimeet_api.modules.meetings.schemas import MeetingDetail
from aimeet_api.modules.transcription.storage import audio_path, store_audio

router = APIRouter(prefix="/meetings", tags=["Transcription"])


@router.post(
    "/audio",
    status_code=202,
    response_model=MeetingDetail,
    dependencies=[Depends(require_csrf)],
    operation_id="uploadMeetingAudio",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
)
async def upload_audio(
    request: Request,
    user: CurrentUser,
    db: DatabaseDep,
    settings: SettingsDep,
    title: Annotated[str, Query(min_length=1, max_length=200)],
    filename: Annotated[str, Query(min_length=1, max_length=255)],
    language: Literal["auto", "ru", "kk", "en"] = "auto",
    num_speakers: Annotated[int | None, Query(ge=1, le=32)] = None,
):
    if not title.strip():
        raise HTTPException(422, "Title must not be blank")
    filename = Path(filename.replace("\\", "/")).name
    if Path(filename).suffix.lower() not in {".mp3", ".wav", ".m4a"}:
        raise HTTPException(415, "Supported formats: MP3, WAV, M4A")
    meeting_id = uuid.uuid4()
    size, digest = await store_audio(request, settings, meeting_id)
    meeting = Meeting(
        id=meeting_id,
        workspace_id=user.workspace_id,
        created_by=user.id,
        title=title.strip(),
        language=language,
        source_type="audio",
        transcript="",
        transcript_length=0,
        audio_filename=filename,
        audio_bytes=size,
        audio_sha256=digest,
        transcription=TranscriptionJob(config={"num_speakers": num_speakers}),
    )
    try:
        db.add(meeting)
        db.commit()
    except BaseException:
        db.rollback()
        audio_path(settings, meeting_id).unlink(missing_ok=True)
        raise
    return MeetingRepository(db, user.workspace_id).get(meeting_id)


def owned_audio(db, user, meeting_id):
    meeting = MeetingRepository(db, user.workspace_id).get(meeting_id)
    if meeting is None or meeting.source_type != "audio":
        raise HTTPException(404, "Audio meeting not found")
    return meeting


@router.get("/{meeting_id}/audio", operation_id="getMeetingAudio")
def read_audio(meeting_id: uuid.UUID, user: CurrentUser, db: DatabaseDep, settings: SettingsDep):
    meeting = owned_audio(db, user, meeting_id)
    source = audio_path(settings, meeting_id)
    if not source.is_file():
        raise HTTPException(404, "Audio source not found")
    media = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4"}
    return FileResponse(
        source,
        media_type=media.get(
            Path(meeting.audio_filename or "").suffix.lower(), "application/octet-stream"
        ),
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.post(
    "/{meeting_id}/transcription/cancel",
    response_model=MeetingDetail,
    dependencies=[Depends(require_csrf)],
    operation_id="cancelTranscription",
)
def cancel(meeting_id: uuid.UUID, user: CurrentUser, db: DatabaseDep):
    owned_audio(db, user, meeting_id)
    db.execute(
        update(TranscriptionJob)
        .execution_options(synchronize_session=False)
        .where(
            TranscriptionJob.meeting_id == meeting_id,
            TranscriptionJob.status.in_(["queued", "running"]),
        )
        .values(status="cancelled", lease_token=None, lease_until=None, error_code=None)
    )
    db.commit()
    db.expire_all()
    return MeetingRepository(db, user.workspace_id).get(meeting_id)


@router.post(
    "/{meeting_id}/transcription/retry",
    response_model=MeetingDetail,
    dependencies=[Depends(require_csrf)],
    operation_id="retryTranscription",
)
def retry(meeting_id: uuid.UUID, user: CurrentUser, db: DatabaseDep, settings: SettingsDep):
    meeting = owned_audio(db, user, meeting_id)
    requested_speakers = (meeting.transcription.config or {}).get("num_speakers")
    if not audio_path(settings, meeting_id).is_file():
        raise HTTPException(409, "Audio source is missing; upload it again")
    result = db.execute(
        update(TranscriptionJob)
        .execution_options(synchronize_session=False)
        .where(
            TranscriptionJob.meeting_id == meeting_id,
            TranscriptionJob.status.in_(["failed", "cancelled"]),
        )
        .values(
            status="queued",
            progress=0,
            attempts=0,
            error_code=None,
            lease_token=None,
            lease_until=None,
            config={"num_speakers": requested_speakers},
            detected_language=None,
            duration_seconds=None,
        )
    )
    if result.rowcount != 1:
        raise HTTPException(409, "Transcription cannot be retried in its current state")
    db.execute(
        update(Meeting)
        .where(Meeting.id == meeting_id)
        .values(transcript="", transcript_length=0, segments=None, status="draft")
    )
    db.commit()
    db.expire_all()
    return MeetingRepository(db, user.workspace_id).get(meeting_id)
