"""Authorized playback of original Live utterances retained after transcription."""

import io
import uuid
import wave
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select, update

from aimeet_api.core.dependencies import CurrentUser, DatabaseDep, SettingsDep, require_csrf
from aimeet_api.modules.live.audio import chunk_path
from aimeet_api.modules.live.models import LiveChunk, LiveParticipant, LiveRoom
from aimeet_api.modules.meetings.repository import MeetingRepository
from aimeet_api.modules.transcription.storage import audio_path

router = APIRouter()
MAX_PCM_BYTES = 300_000


class AudioClip(BaseModel):
    id: uuid.UUID
    speaker: str
    start: float
    end: float
    start_char: int
    end_char: int
    available: bool


class MeetingAudioClips(BaseModel):
    source: Literal["live", "none"]
    clips: list[AudioClip]
    full_audio_available: bool = False
    recording_status: Literal["none", "recording", "queued", "processing", "ready", "failed"] = (
        "none"
    )


def owned_meeting(db, user, meeting_id):
    meeting = MeetingRepository(db, user.workspace_id).get(meeting_id)
    if meeting is None:
        raise HTTPException(404, "Meeting not found")
    return meeting


@router.get(
    "/{meeting_id}/audio-clips",
    response_model=MeetingAudioClips,
    operation_id="getMeetingAudioClips",
)
def list_clips(meeting_id: uuid.UUID, user: CurrentUser, db: DatabaseDep, settings: SettingsDep):
    meeting = owned_meeting(db, user, meeting_id)
    full_audio = meeting.source_type == "audio" and audio_path(settings, meeting_id).is_file()
    room_ids = list(
        db.scalars(
            select(LiveRoom.id).where(
                LiveRoom.meeting_id == meeting.id,
                LiveRoom.workspace_id == user.workspace_id,
            )
        )
    )
    if not room_ids:
        return MeetingAudioClips(source="none", clips=[], full_audio_available=full_audio)
    rows = db.execute(
        select(LiveChunk, LiveParticipant.name)
        .join(LiveParticipant)
        .where(
            LiveChunk.room_id.in_(room_ids),
            LiveChunk.status == "done",
            LiveChunk.text != "",
        )
        .order_by(LiveChunk.start, LiveChunk.id)
    ).all()
    clips, cursor = [], 0
    for chunk, speaker in rows:
        prefix = f"[{int(chunk.start) // 60:02}:{int(chunk.start) % 60:02}] {speaker}: "
        line = prefix + chunk.text
        position = meeting.transcript.find(line, cursor)
        if position < 0:
            continue
        cursor = position + len(line)
        path = chunk_path(settings, chunk.id)
        try:
            size = path.stat().st_size
            available = path.is_file() and 0 < size <= MAX_PCM_BYTES and size % 2 == 0
        except OSError:
            available = False
        clips.append(
            AudioClip(
                id=chunk.id,
                speaker=speaker,
                start=chunk.start,
                end=chunk.end,
                start_char=position + len(prefix),
                end_char=cursor,
                available=available,
            )
        )
    status = db.scalar(select(LiveRoom.recording_status).where(LiveRoom.id == room_ids[0]))
    return MeetingAudioClips(
        source="live", clips=clips, full_audio_available=full_audio, recording_status=status
    )


@router.post(
    "/{meeting_id}/audio-recording/retry",
    dependencies=[Depends(require_csrf)],
    operation_id="retryMeetingRecording",
)
def retry_recording(meeting_id: uuid.UUID, user: CurrentUser, db: DatabaseDep):
    owned_meeting(db, user, meeting_id)
    result = db.execute(
        update(LiveRoom)
        .where(
            LiveRoom.meeting_id == meeting_id,
            LiveRoom.workspace_id == user.workspace_id,
            LiveRoom.recording_status == "failed",
        )
        .values(recording_status="queued", recording_error=None, recording_attempts=0)
    )
    if result.rowcount != 1:
        raise HTTPException(409, "Recording cannot be retried")
    db.commit()
    return {"status": "queued"}


@router.get("/{meeting_id}/audio-clips/{clip_id}", operation_id="playMeetingAudioClip")
def play_clip(
    meeting_id: uuid.UUID,
    clip_id: uuid.UUID,
    user: CurrentUser,
    db: DatabaseDep,
    settings: SettingsDep,
):
    owned_meeting(db, user, meeting_id)
    chunk = db.scalar(
        select(LiveChunk)
        .join(LiveRoom)
        .where(
            LiveChunk.id == clip_id,
            LiveRoom.meeting_id == meeting_id,
            LiveRoom.workspace_id == user.workspace_id,
            LiveChunk.status == "done",
            LiveChunk.text != "",
        )
    )
    if chunk is None:
        raise HTTPException(404, "Audio clip not found")
    try:
        with chunk_path(settings, chunk.id).open("rb") as file:
            pcm = file.read(MAX_PCM_BYTES + 1)
    except OSError:
        raise HTTPException(404, "Audio clip was not retained") from None
    if not pcm or len(pcm) > MAX_PCM_BYTES or len(pcm) % 2:
        raise HTTPException(404, "Audio clip is unavailable")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(pcm)
    return Response(
        buffer.getvalue(),
        media_type="audio/wav",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
