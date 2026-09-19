import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=2000)

    @field_validator("question")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Question must not be blank")
        return value.strip()


class ConversationMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=6000)


class AssistantQuestion(Question):
    history: list[ConversationMessage] = Field(default_factory=list, max_length=20)

    @field_validator("history")
    @classmethod
    def bounded_history(cls, value):
        if sum(len(message.content) for message in value) > 40_000:
            raise ValueError("Conversation history is too long")
        return value


class AssistantDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["reply", "search_meetings", "create_tasks", "open_panel"]
    answer: str = Field(max_length=12000)
    search_query: str = Field(max_length=2000)


class TaskCreationRequest(AssistantQuestion):
    request_id: uuid.UUID
    conversation_id: uuid.UUID | None = None


class AssistantTaskDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    meeting_id: uuid.UUID
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(max_length=4000)
    assignee: str | None = Field(max_length=200)
    due_date: date | None
    due_text: str | None = Field(max_length=200)


class MeetingPanelAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    meeting_id: uuid.UUID
    view: Literal["kanban", "insights", "conversation"]


class TaskCreationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["create", "clarify"]
    answer: str = Field(max_length=3000)
    tasks: list[AssistantTaskDraft] = Field(max_length=10)
    panel: MeetingPanelAction | None = None


class CreatedTask(AssistantTaskDraft):
    card_id: uuid.UUID
    meeting_title: str
    board_version: int


class TaskCreationResult(BaseModel):
    status: Literal["created", "clarification"]
    answer: str
    tasks: list[CreatedTask]
    panel: MeetingPanelAction | None = None


class IndexStatus(BaseModel):
    index_id: uuid.UUID | None
    status: Literal["not_indexed", "queued", "running", "ready", "failed"]
    node_count: int = 0
    error_code: str | None = None


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str
    quote: str = Field(min_length=1, max_length=2000)


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=3000)
    evidence: list[Evidence] = Field(min_length=1, max_length=5)


class GeneratedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["answered", "insufficient_evidence"]
    claims: list[Claim] = Field(max_length=12)
    panel: MeetingPanelAction | None = None


class Source(BaseModel):
    source_id: str
    node_id: uuid.UUID
    parent_id: uuid.UUID | None
    start_char: int
    end_char: int
    text: str
    reason: Literal["hit", "parent", "neighbor"]


class Citation(BaseModel):
    source_id: str
    node_id: uuid.UUID
    start_char: int
    end_char: int
    quote: str


class AnswerClaim(BaseModel):
    text: str
    citations: list[Citation]


class Answer(BaseModel):
    status: Literal["answered", "insufficient_evidence"]
    answer: str
    claims: list[AnswerClaim]
    sources: list[Source]
    index_id: uuid.UUID
    model: str
    provider: str
    prompt_version: str


class SearchResult(BaseModel):
    index_id: uuid.UUID
    sources: list[Source]


class RagConfiguration(BaseModel):
    offline: bool
    llm_provider: str
    llm_model: str
    reasoning_effort: str
    embedding_provider: str
    embedding_model: str
    embedding_dimensions: int
    cloud_configured: bool


class WorkspaceCoverage(BaseModel):
    total: int = 0
    ready: int = 0
    pending: int = 0
    failed: int = 0
    not_indexed: int = 0
    unavailable: int = 0


class WorkspaceSource(Source):
    meeting_id: uuid.UUID
    meeting_title: str
    meeting_created_at: datetime


class CatalogSource(BaseModel):
    source_id: str
    text: str
    meeting_id: uuid.UUID | None
    meeting_title: str | None


class CatalogCitation(BaseModel):
    kind: Literal["catalog"] = "catalog"
    source_id: str
    quote: str


class BoardSource(BaseModel):
    source_id: str
    kind: Literal["summary", "kanban"]
    meeting_id: uuid.UUID
    meeting_title: str
    card_id: uuid.UUID | None
    title: str
    text: str
    board_version: int
    provisional: bool
    transcript_current: bool
    transcript_quote: str | None


class BoardCitation(BaseModel):
    kind: Literal["board"] = "board"
    source_id: str
    quote: str


class BoardCoverage(BaseModel):
    available_sources: int = 0
    selected_sources: int = 0


class WorkspaceClaim(BaseModel):
    text: str
    citations: list[Citation | CatalogCitation | BoardCitation]


class WorkspaceAnswer(BaseModel):
    panel: MeetingPanelAction | None = None
    status: Literal["answered", "insufficient_evidence"]
    answer: str
    claims: list[WorkspaceClaim]
    sources: list[WorkspaceSource]
    catalog_sources: list[CatalogSource] = Field(default_factory=list)
    board_sources: list[BoardSource] = Field(default_factory=list)
    board_coverage: BoardCoverage = Field(default_factory=BoardCoverage)
    coverage: WorkspaceCoverage


class GraphNode(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    kind: str
    ordinal: int
    parent_id: uuid.UUID | None
    start_char: int
    end_char: int


class GraphEdge(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    source_id: uuid.UUID
    target_id: uuid.UUID
    relation: str


class Graph(BaseModel):
    index_id: uuid.UUID
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class GeneralChatResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["assistant"]
    answer: str = Field(min_length=1, max_length=12000)


class MeetingChatResult(WorkspaceAnswer):
    mode: Literal["meetings"]


class TaskChatResult(TaskCreationResult):
    mode: Literal["tasks"]


class PanelPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    meeting_id: uuid.UUID | None
    view: Literal["kanban", "insights", "conversation"]
    answer: str = Field(max_length=3000)
    choices: list[uuid.UUID] = Field(max_length=8)


class PanelMeeting(BaseModel):
    id: uuid.UUID
    title: str


class NavigationResult(BaseModel):
    mode: Literal["navigation"] = "navigation"
    answer: str
    view: Literal["kanban", "insights", "conversation"]
    panel: MeetingPanelAction | None
    meetings: list[PanelMeeting]


class ConversationCreate(BaseModel):
    id: uuid.UUID


class ConversationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


class ConversationTurnInput(Question):
    id: uuid.UUID
    result: GeneralChatResult | MeetingChatResult | TaskChatResult | NavigationResult = Field(
        discriminator="mode"
    )

    @field_validator("result")
    @classmethod
    def bounded_result(cls, value):
        if len(value.model_dump_json()) > 300_000:
            raise ValueError("Conversation result too large")
        return value


class PendingChatResult(BaseModel):
    mode: Literal["pending", "failed"]
    answer: Literal[""] = ""
    activity: Literal["thinking", "creating"] = "thinking"
    error_code: str | None = None
    error_status: int | None = None


class ConversationTurnStart(Question):
    id: uuid.UUID
    state: Literal["pending", "failed"] = "pending"
    activity: Literal["thinking", "creating"] = "thinking"
    error_code: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    error_status: int | None = Field(default=None, ge=400, le=599)


class ConversationTurnOutput(Question):
    id: uuid.UUID
    result: (
        GeneralChatResult
        | MeetingChatResult
        | TaskChatResult
        | NavigationResult
        | PendingChatResult
    ) = Field(discriminator="mode")
    created_at: datetime


class ConversationDetail(ConversationSummary):
    turns: list[ConversationTurnOutput]
