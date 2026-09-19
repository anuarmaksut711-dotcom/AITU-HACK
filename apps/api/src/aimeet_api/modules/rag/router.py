import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from aimeet_api.core.dependencies import CurrentUser, DatabaseDep, SettingsDep, require_csrf
from aimeet_api.modules.meetings.repository import MeetingRepository
from aimeet_api.modules.rag.assistant import decide_reply, stream_reply
from aimeet_api.modules.rag.assistant_tasks import create_tasks
from aimeet_api.modules.rag.chunking import embedding_profile, source_hash
from aimeet_api.modules.rag.conversations import (
    create_conversation,
    list_conversations,
    read_conversation,
    save_turn,
    start_turn,
)
from aimeet_api.modules.rag.indexing import enqueue
from aimeet_api.modules.rag.models import RagEdge, RagIndex, RagNode
from aimeet_api.modules.rag.navigation import resolve_panel
from aimeet_api.modules.rag.providers import Providers, RagError
from aimeet_api.modules.rag.retrieval import retrieve
from aimeet_api.modules.rag.schemas import (
    Answer,
    AssistantDecision,
    AssistantQuestion,
    ConversationCreate,
    ConversationDetail,
    ConversationSummary,
    ConversationTurnInput,
    ConversationTurnOutput,
    ConversationTurnStart,
    Graph,
    GraphEdge,
    GraphNode,
    IndexStatus,
    NavigationResult,
    Question,
    RagConfiguration,
    SearchResult,
    TaskCreationRequest,
    TaskCreationResult,
    WorkspaceAnswer,
    WorkspaceCoverage,
)
from aimeet_api.modules.rag.service import answer_question
from aimeet_api.modules.rag.streaming import event_stream
from aimeet_api.modules.rag.workspace import answer_workspace, workspace_events, workspace_scope

router = APIRouter(tags=["RAG"])


def get_providers(settings: SettingsDep):
    return Providers(settings)


ProviderDep = Annotated[Providers, Depends(get_providers)]


@router.post(
    "/assistant/chat",
    response_model=AssistantDecision,
    dependencies=[Depends(require_csrf)],
    operation_id="askAssistant",
)
def assistant_chat(
    payload: AssistantQuestion, user: CurrentUser, db: DatabaseDep, providers: ProviderDep
):
    # The general conversation path never loads meetings or calls embeddings.
    db.rollback()
    return decide_reply(payload, providers)


@router.post(
    "/assistant/tasks",
    response_model=TaskCreationResult,
    dependencies=[Depends(require_csrf)],
    operation_id="createAssistantTasks",
)
def assistant_tasks(
    payload: TaskCreationRequest, user: CurrentUser, db: DatabaseDep, providers: ProviderDep
):
    return create_tasks(db, user, payload, providers)


@router.get(
    "/assistant/conversations",
    response_model=list[ConversationSummary],
    operation_id="listAssistantConversations",
)
def conversation_list(user: CurrentUser, db: DatabaseDep):
    return list_conversations(db, user)


@router.post(
    "/assistant/conversations",
    response_model=ConversationSummary,
    dependencies=[Depends(require_csrf)],
    operation_id="createAssistantConversation",
)
def conversation_create(payload: ConversationCreate, user: CurrentUser, db: DatabaseDep):
    return create_conversation(db, user, payload.id)


@router.get(
    "/assistant/conversations/{conversation_id}",
    response_model=ConversationDetail,
    operation_id="getAssistantConversation",
)
def conversation_detail(conversation_id: uuid.UUID, user: CurrentUser, db: DatabaseDep):
    return read_conversation(db, user, conversation_id)


@router.post(
    "/assistant/conversations/{conversation_id}/turns",
    response_model=ConversationTurnOutput,
    dependencies=[Depends(require_csrf)],
    operation_id="saveAssistantTurn",
)
def conversation_save_turn(
    conversation_id: uuid.UUID, payload: ConversationTurnInput, user: CurrentUser, db: DatabaseDep
):
    return save_turn(db, user, conversation_id, payload)


@router.post(
    "/assistant/conversations/{conversation_id}/turns/pending",
    response_model=ConversationTurnOutput,
    dependencies=[Depends(require_csrf)],
    operation_id="startAssistantTurn",
)
def conversation_start_turn(
    conversation_id: uuid.UUID, payload: ConversationTurnStart, user: CurrentUser, db: DatabaseDep
):
    return start_turn(db, user, conversation_id, payload)


@router.post(
    "/assistant/chat/stream",
    dependencies=[Depends(require_csrf)],
    operation_id="streamAssistantChat",
)
def assistant_chat_stream(
    payload: AssistantQuestion,
    request: Request,
    user: CurrentUser,
    db: DatabaseDep,
    providers: ProviderDep,
):
    db.rollback()
    return StreamingResponse(
        event_stream(stream_reply(payload, providers), request.state.request_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/rag/chat/stream", dependencies=[Depends(require_csrf)], operation_id="streamWorkspaceChat"
)
def workspace_chat_stream(
    payload: Question,
    request: Request,
    user: CurrentUser,
    db: DatabaseDep,
    settings: SettingsDep,
    providers: ProviderDep,
):
    workspace_id = user.workspace_id
    db.rollback()
    return StreamingResponse(
        event_stream(
            workspace_events(db, workspace_id, payload.question, settings, providers),
            request.state.request_id,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


def get_meeting(db, user, meeting_id):
    meeting = MeetingRepository(db, user.workspace_id).get(meeting_id)
    if meeting is None:
        raise HTTPException(404, "Meeting not found")
    if not meeting.transcript.strip() or (
        meeting.source_type == "audio" and meeting.status != "transcribed"
    ):
        raise RagError("TRANSCRIPT_NOT_READY", 409)
    if len(meeting.transcript) > 200_000:
        raise RagError("TRANSCRIPT_TOO_LARGE", 413)
    return meeting


def get_index(db, meeting, settings, *, ready=False):
    index = db.scalar(
        select(RagIndex).where(
            RagIndex.meeting_id == meeting.id,
            RagIndex.profile == embedding_profile(settings),
            RagIndex.source_hash == source_hash(meeting.transcript),
        )
    )
    if ready and (index is None or index.status != "ready"):
        raise RagError("INDEX_NOT_READY", 409)
    if index and index.source_hash != source_hash(meeting.transcript):
        raise RagError("SOURCE_CHANGED", 409)
    return index


def status_of(index):
    return IndexStatus(
        index_id=index.id if index else None,
        status=index.status if index else "not_indexed",
        node_count=index.node_count if index else 0,
        error_code=index.error_code if index else None,
    )


@router.get("/rag/config", response_model=RagConfiguration, operation_id="getRagConfig")
def configuration(user: CurrentUser, settings: SettingsDep):
    return RagConfiguration(
        offline=settings.rag_offline,
        llm_provider=settings.rag_llm_provider,
        llm_model=settings.rag_llm_model,
        reasoning_effort=settings.rag_reasoning_effort,
        embedding_provider=settings.rag_embedding_provider,
        embedding_model=settings.rag_embedding_model,
        embedding_dimensions=settings.rag_embedding_dimensions,
        cloud_configured=bool(settings.openai_api_key.get_secret_value()),
    )


@router.get("/rag/index", response_model=WorkspaceCoverage, operation_id="getWorkspaceRagIndex")
def workspace_status(user: CurrentUser, db: DatabaseDep, settings: SettingsDep):
    return workspace_scope(db, user.workspace_id, settings)[1]


@router.post(
    "/rag/index",
    response_model=WorkspaceCoverage,
    status_code=202,
    dependencies=[Depends(require_csrf)],
    operation_id="indexWorkspaceMeetings",
)
def index_workspace(
    user: CurrentUser,
    db: DatabaseDep,
    settings: SettingsDep,
    providers: ProviderDep,
):
    providers.ensure_configured()
    workspace_id = user.workspace_id
    scope, _ = workspace_scope(db, workspace_id, settings)
    # Capture IDs before enqueue commits expire ORM instances.
    missing = [meeting.id for meeting, index in scope if index is None or index.status == "failed"]
    for meeting_id in missing:
        meeting = MeetingRepository(db, workspace_id).get(meeting_id)
        if meeting and meeting.transcript.strip() and len(meeting.transcript) <= 200_000:
            enqueue(db, meeting, settings)
    return workspace_scope(db, workspace_id, settings)[1]


@router.post(
    "/rag/chat",
    response_model=WorkspaceAnswer,
    dependencies=[Depends(require_csrf)],
    operation_id="askWorkspaceMeetings",
)
def workspace_chat(
    payload: Question,
    user: CurrentUser,
    db: DatabaseDep,
    settings: SettingsDep,
    providers: ProviderDep,
):
    return answer_workspace(db, user.workspace_id, payload.question, settings, providers)


@router.get(
    "/meetings/{meeting_id}/rag/index", response_model=IndexStatus, operation_id="getRagIndex"
)
def index_status(meeting_id: uuid.UUID, user: CurrentUser, db: DatabaseDep, settings: SettingsDep):
    meeting = get_meeting(db, user, meeting_id)
    return status_of(get_index(db, meeting, settings))


@router.post(
    "/meetings/{meeting_id}/rag/index",
    response_model=IndexStatus,
    status_code=202,
    dependencies=[Depends(require_csrf)],
    operation_id="indexMeeting",
)
def index_meeting(
    meeting_id: uuid.UUID,
    user: CurrentUser,
    db: DatabaseDep,
    settings: SettingsDep,
    providers: ProviderDep,
):
    meeting = get_meeting(db, user, meeting_id)
    providers.ensure_configured()
    return status_of(enqueue(db, meeting, settings))


@router.get("/meetings/{meeting_id}/rag/graph", response_model=Graph, operation_id="getRagGraph")
def graph(meeting_id: uuid.UUID, user: CurrentUser, db: DatabaseDep, settings: SettingsDep):
    index = get_index(db, get_meeting(db, user, meeting_id), settings, ready=True)
    return Graph(
        index_id=index.id,
        nodes=[
            GraphNode.model_validate(node)
            for node in db.scalars(
                select(RagNode).where(RagNode.index_id == index.id).order_by(RagNode.start_char)
            )
        ],
        edges=[
            GraphEdge.model_validate(edge)
            for edge in db.scalars(select(RagEdge).where(RagEdge.index_id == index.id))
        ],
    )


def search_sources(db, user, meeting_id, settings, providers, payload):
    index = get_index(db, get_meeting(db, user, meeting_id), settings, ready=True)
    index_id = index.id
    db.rollback()  # Release auth/read transaction while waiting for an embedding provider.
    vector = providers.embed([payload.question])[0]
    # Recheck access/existence after provider IO, before loading any source content.
    index = get_index(db, get_meeting(db, user, meeting_id), settings, ready=True)
    if index.id != index_id:
        raise RagError("SOURCE_CHANGED", 409)
    sources = retrieve(db, index_id, payload.question, vector, settings)
    db.rollback()
    return index_id, sources


@router.post(
    "/meetings/{meeting_id}/rag/search",
    response_model=SearchResult,
    dependencies=[Depends(require_csrf)],
    operation_id="searchMeeting",
)
def search(
    meeting_id: uuid.UUID,
    payload: Question,
    user: CurrentUser,
    db: DatabaseDep,
    settings: SettingsDep,
    providers: ProviderDep,
):
    index_id, sources = search_sources(db, user, meeting_id, settings, providers, payload)
    return SearchResult(index_id=index_id, sources=sources)


@router.post(
    "/meetings/{meeting_id}/rag/chat",
    response_model=Answer,
    dependencies=[Depends(require_csrf)],
    operation_id="askMeeting",
)
def chat(
    meeting_id: uuid.UUID,
    payload: Question,
    user: CurrentUser,
    db: DatabaseDep,
    settings: SettingsDep,
    providers: ProviderDep,
):
    # Authorization always precedes configuration errors and provider work.
    get_meeting(db, user, meeting_id)
    providers.ensure_configured(generation=True)
    index_id, sources = search_sources(db, user, meeting_id, settings, providers, payload)
    result = answer_question(index_id, payload.question, sources, settings, providers)
    current = get_index(db, get_meeting(db, user, meeting_id), settings, ready=True)
    if current.id != index_id:
        raise RagError("SOURCE_CHANGED", 409)
    return result


@router.post(
    "/assistant/panel",
    response_model=NavigationResult,
    dependencies=[Depends(require_csrf)],
    operation_id="resolveAssistantPanel",
)
def assistant_panel(
    payload: AssistantQuestion, user: CurrentUser, db: DatabaseDep, providers: ProviderDep
):
    return resolve_panel(db, user.workspace_id, payload, providers)
