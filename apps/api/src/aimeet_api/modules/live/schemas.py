import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RoomCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    language: Literal["auto", "ru", "kk", "en"] = "auto"

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value):
        if not value.strip():
            raise ValueError("Title is required")
        return value.strip()


class GuestJoin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    invite: str = Field(min_length=32, max_length=128)
    name: str = Field(min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value):
        if not value.strip():
            raise ValueError("Name is required")
        return value.strip()


class InviteCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    invite: str = Field(min_length=32, max_length=128)


class JoinGrant(BaseModel):
    room_id: uuid.UUID
    participant_id: uuid.UUID
    name: str
    is_host: bool
    member_token: str
    media_token: str
    media_url: str


class ParticipantOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    is_host: bool


class UtteranceOutput(BaseModel):
    id: uuid.UUID
    participant_id: uuid.UUID
    speaker: str
    start: float
    end: float
    text: str


class InsightOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["goal", "idea", "decision", "task", "question"]
    text: str
    source_ids: list[str]
    quotes: list[str]


class GeneratedInsight(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["goal", "idea", "decision", "task", "question"]
    text: str
    source_ids: list[str]


class GeneratedInsights(BaseModel):
    model_config = ConfigDict(extra="forbid")
    insights: list[GeneratedInsight]


class RoomSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    title: str
    language: str
    status: Literal["active", "ending", "ended"]
    created_at: datetime
    ended_at: datetime | None
    meeting_id: uuid.UUID | None


class RoomState(RoomSummary):
    participants: list[ParticipantOutput]
    utterances: list[UtteranceOutput]
    insights: list[InsightOutput]
    transcript_revision: int
    analysis_status: str
    analysis_error: str | None
    audio_error: str | None
    analysis_provider: str
    can_end: bool
