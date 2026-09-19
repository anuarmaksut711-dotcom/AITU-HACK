import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator


class MeetingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    language: Literal["auto", "ru", "kk", "en"] = "auto"
    transcript: str = Field(min_length=1, max_length=200_000)

    @field_validator("title", "transcript")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Must contain non-whitespace characters")
        return value

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        return value.strip()


class MeetingSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    title: str
    language: Literal["auto", "ru", "kk", "en"]
    status: Literal["draft", "transcribed"]
    source_type: Literal["text", "audio"]
    created_at: datetime
    updated_at: datetime
    transcript_length: int
    transcription: "TranscriptionOutput | None"

    @field_serializer("created_at", "updated_at")
    def serialize_datetime(self, value: datetime) -> str:
        # SQLite test storage drops tzinfo; production stores TIMESTAMPTZ.
        return value.replace(tzinfo=UTC).isoformat() if value.tzinfo is None else value.isoformat()


class MeetingDetail(MeetingSummary):
    transcript: str
    audio_filename: str | None
    audio_bytes: int | None
    segments: list["TranscriptSegment"] | None


class TranscriptSegment(BaseModel):
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(ge=0, allow_inf_nan=False)
    text: str
    speaker: str | None = None


class TranscriptionOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    progress: int
    error_code: str | None
    detected_language: str | None
    duration_seconds: float | None


class MeetingList(BaseModel):
    items: list[MeetingSummary]
    total: int
    limit: int
    offset: int
