"""Private, persisted assistant conversations; completed turns are idempotent."""

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from aimeet_api.db.models import utcnow
from aimeet_api.modules.rag.models import AssistantConversation, AssistantConversationTurn
from aimeet_api.modules.rag.schemas import (
    ConversationDetail,
    ConversationSummary,
    ConversationTurnOutput,
)


def owned_conversation(db, user, conversation_id):
    conversation = db.scalar(
        select(AssistantConversation).where(
            AssistantConversation.id == conversation_id,
            AssistantConversation.workspace_id == user.workspace_id,
            AssistantConversation.user_id == user.id,
        )
    )
    if conversation is None:
        raise HTTPException(404, "Conversation not found")
    return conversation


def list_conversations(db, user):
    return list(
        db.scalars(
            select(AssistantConversation)
            .where(
                AssistantConversation.workspace_id == user.workspace_id,
                AssistantConversation.user_id == user.id,
            )
            .order_by(AssistantConversation.updated_at.desc(), AssistantConversation.id)
        )
    )


def create_conversation(db, user, conversation_id):
    existing = db.get(AssistantConversation, conversation_id)
    if existing:
        return owned_conversation(db, user, conversation_id)
    conversation = AssistantConversation(
        id=conversation_id, workspace_id=user.workspace_id, user_id=user.id
    )
    db.add(conversation)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return owned_conversation(db, user, conversation_id)
    db.refresh(conversation)
    return conversation


def turn_output(turn):
    return ConversationTurnOutput(
        id=turn.id, question=turn.question, result=turn.result, created_at=turn.created_at
    )


def read_conversation(db, user, conversation_id):
    conversation = owned_conversation(db, user, conversation_id)
    turns = list(
        db.scalars(
            select(AssistantConversationTurn)
            .where(
                AssistantConversationTurn.conversation_id == conversation_id,
            )
            .order_by(AssistantConversationTurn.created_at, AssistantConversationTurn.id)
        )
    )
    return ConversationDetail(
        **ConversationSummary.model_validate(conversation).model_dump(),
        turns=[turn_output(turn) for turn in turns],
    )


def save_turn(db, user, conversation_id, payload):
    conversation = owned_conversation(db, user, conversation_id)
    existing = db.get(AssistantConversationTurn, payload.id)
    if existing:
        if existing.conversation_id != conversation_id or existing.question != payload.question:
            raise HTTPException(409, "Turn already used")
        if existing.result.get("mode") in {"pending", "failed"}:
            db.execute(
                update(AssistantConversationTurn)
                .where(
                    AssistantConversationTurn.id == payload.id,
                    AssistantConversationTurn.result["mode"].as_string().in_(["pending", "failed"]),
                )
                .values(result=payload.result.model_dump(mode="json"))
            )
            conversation.updated_at = utcnow()
            db.commit()
            db.refresh(existing)
        return turn_output(existing)
    # Results are private conversation snapshots supplied by this user, not authoritative
    # evidence for future meeting answers; the assistant always rechecks meeting facts.
    turn = AssistantConversationTurn(
        id=payload.id,
        conversation_id=conversation_id,
        question=payload.question,
        result=payload.result.model_dump(mode="json"),
    )
    db.add(turn)
    conversation.updated_at = utcnow()
    if conversation.title == "Новый чат":
        conversation.title = " ".join(payload.question.split())[:100]
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.get(AssistantConversationTurn, payload.id)
        if (
            existing
            and existing.conversation_id == conversation_id
            and existing.question == payload.question
        ):
            return turn_output(existing)
        raise HTTPException(409, "Turn conflict") from None
    db.refresh(turn)
    return turn_output(turn)


def start_turn(db, user, conversation_id, payload):
    conversation = owned_conversation(db, user, conversation_id)
    existing = db.get(AssistantConversationTurn, payload.id)
    if existing and (
        existing.conversation_id != conversation_id or existing.question != payload.question
    ):
        raise HTTPException(409, "Turn already used")
    result = {
        "mode": payload.state,
        "answer": "",
        "activity": payload.activity,
        "error_code": payload.error_code if payload.state == "failed" else None,
        "error_status": payload.error_status if payload.state == "failed" else None,
    }
    if existing:
        if existing.result.get("mode") not in {"pending", "failed"}:
            return turn_output(existing)
        if existing.result.get("activity") == "creating":
            result["activity"] = "creating"
        db.execute(
            update(AssistantConversationTurn)
            .where(
                AssistantConversationTurn.id == payload.id,
                AssistantConversationTurn.result["mode"].as_string().in_(["pending", "failed"]),
            )
            .values(result=result)
        )
        turn = existing
    else:
        turn = AssistantConversationTurn(
            id=payload.id, conversation_id=conversation_id, question=payload.question, result=result
        )
        db.add(turn)
    conversation.updated_at = utcnow()
    if conversation.title == "Новый чат":
        conversation.title = " ".join(payload.question.split())[:100]
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.get(AssistantConversationTurn, payload.id)
        if (
            existing
            and existing.conversation_id == conversation_id
            and existing.question == payload.question
        ):
            return turn_output(existing)
        raise HTTPException(409, "Turn conflict") from None
    db.refresh(turn)
    return turn_output(turn)
