import uuid
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Kind = Literal["task", "decision", "topic", "question", "risk"]
Status = Literal["todo", "doing", "blocked", "done", "dismissed"]
Priority = Literal["unspecified", "low", "medium", "high"]
Agreement = Literal["confirmed", "proposed", "unclear"]


class SourceQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quote: str = Field(min_length=1, max_length=2000)
    quote_start: int | None = Field(default=None, ge=0)


class Evidence(BaseModel):
    quote: str
    start_char: int
    end_char: int
    start_seconds: float | None = None
    end_seconds: float | None = None
    speaker: str | None = None


class GeneratedRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: Literal["due_text", "assignee"]
    before_value: str = Field(min_length=1, max_length=200)
    after_value: str = Field(min_length=1, max_length=200)
    before: SourceQuote
    after: SourceQuote


class Revision(BaseModel):
    field: Literal["due_text", "assignee", "decision"]
    before_value: str
    after_value: str
    before: Evidence
    after: Evidence


class CardInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Kind = "task"
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=4000)
    assignee: str | None = Field(default=None, max_length=200)
    due_date: date | None = None
    due_text: str | None = Field(default=None, max_length=200)
    priority: Priority = "unspecified"
    status: Status = "todo"
    reviewed: bool = False
    quote: str | None = Field(default=None, min_length=1, max_length=2000)
    quote_start: int | None = Field(default=None, ge=0)
    agreement: Agreement = "unclear"

    @field_validator("title")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Title must not be blank")
        return value.strip()


class Card(CardInput):
    id: uuid.UUID
    origin: Literal["ai", "manual"]
    start_char: int | None = None
    end_char: int | None = None
    evidence: Evidence | None = None
    revisions: list[Revision] = Field(default_factory=list)
    clarifications: list[str] = Field(default_factory=list)


class CardCreate(CardInput):
    version: int = Field(ge=0)


class CardUpdate(CardInput):
    version: int = Field(ge=0)


class SummarySentence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=1500)
    quote: str = Field(min_length=1, max_length=2000)


class SummaryOutput(SummarySentence):
    evidence: Evidence | None = None


class GeneratedCard(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Kind
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(max_length=4000)
    assignee: str | None = Field(max_length=200)
    due_text: str | None = Field(max_length=200)
    priority: Priority
    priority_evidence: str | None = Field(max_length=200)
    quote: str = Field(min_length=1, max_length=2000)
    quote_start: int | None = Field(default=None, ge=0)
    agreement: Agreement = "unclear"
    revisions: list[GeneratedRevision] = Field(default_factory=list, max_length=20)


class GeneratedProtocol(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: list[SummarySentence] = Field(max_length=5)
    cards: list[GeneratedCard] = Field(max_length=80)


class CorrectionLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    earlier_id: uuid.UUID
    later_id: uuid.UUID
    # Exact later evidence that explicitly changes (or disputes) the earlier agreement.
    quote: str = Field(min_length=1, max_length=2000)
    resolved: bool


class Reconciliation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    links: list[CorrectionLink] = Field(max_length=80)


class BoardOutput(BaseModel):
    version: int
    status: Literal["idle", "queued", "running", "ready", "failed"]
    error_code: str | None
    summary: list[SummaryOutput]
    cards: list[Card]
    progress: int
