"""Read current meeting outcomes directly, so board edits need no embedding rebuild."""

import hashlib
import json
from types import SimpleNamespace

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import undefer

from aimeet_api.db.models import Meeting
from aimeet_api.modules.intelligence.models import MeetingBoard
from aimeet_api.modules.intelligence.schemas import Card, SummarySentence
from aimeet_api.modules.intelligence.service import digest
from aimeet_api.modules.rag.retrieval import lexical_scores
from aimeet_api.modules.rag.schemas import BoardCoverage, BoardSource

KIND = {
    "task": "Задача",
    "decision": "Решение",
    "topic": "Тема",
    "question": "Вопрос",
    "risk": "Риск",
}
STATUS = {
    "todo": "К выполнению",
    "doing": "В работе",
    "blocked": "Заблокировано",
    "done": "Готово",
    "dismissed": "В архиве",
}
AGREEMENT = {"confirmed": "Согласовано", "proposed": "Предложение", "unclear": "Нужно уточнить"}
PRIORITY = {"unspecified": "Не указан", "low": "Низкий", "medium": "Средний", "high": "Высокий"}


def board_candidates(db, workspace_id):
    sources, snapshots, active = [], [], set()
    rows = (
        db.execute(
            select(MeetingBoard, Meeting)
            .join(Meeting, Meeting.id == MeetingBoard.meeting_id)
            .options(undefer(Meeting.transcript))
            .where(Meeting.workspace_id == workspace_id)
            .order_by(Meeting.created_at.desc(), Meeting.id)
        )
        .unique()
        .all()
    )
    for board, meeting in rows:
        # Compare plain snapshots after model IO, including manual edits and removed boards.
        snapshots.append(
            (
                str(meeting.id),
                meeting.title,
                digest(meeting.transcript),
                board.version,
                board.status,
                board.source_hash,
                hashlib.sha256(
                    json.dumps(
                        [board.cards, board.summary], sort_keys=True, ensure_ascii=False
                    ).encode()
                ).hexdigest(),
            )
        )
        current = board.source_hash == digest(meeting.transcript)
        partial = board.status != "ready"
        common = dict(
            meeting_id=meeting.id,
            meeting_title=meeting.title,
            board_version=board.version,
            transcript_current=current,
        )
        for number, raw in enumerate(board.summary):
            try:
                sentence = SummarySentence.model_validate({k: raw[k] for k in ("text", "quote")})
            except (ValidationError, KeyError, TypeError):
                continue
            if not current or sentence.quote not in meeting.transcript:
                continue
            sources.append(
                BoardSource(
                    **common,
                    source_id=f"B-{meeting.id}-summary-{number}",
                    kind="summary",
                    card_id=None,
                    title="Итоги встречи",
                    text=sentence.text,
                    provisional=partial,
                    transcript_quote=sentence.quote,
                )
            )
        for raw in board.cards:
            try:
                card = Card.model_validate(raw)
            except ValidationError:
                continue
            # Keep explicit human input but do not revive stale, unreviewed AI extraction.
            human = card.origin == "manual" or card.reviewed
            if not current and not human:
                continue
            quote = card.quote if card.quote and card.quote in meeting.transcript else None
            due = card.due_date.isoformat() if card.due_date else card.due_text or "Не указан"
            details = [
                f"Тип: {KIND[card.kind]}",
                f"Название: {card.title}",
                f"Статус: {STATUS[card.status]}",
                f"Ответственный: {card.assignee or 'Не указан'}",
                f"Срок: {due}",
                f"Приоритет: {PRIORITY[card.priority]}",
                f"Договорённость: {AGREEMENT[card.agreement]}",
                f"Проверено пользователем: {'Да' if card.reviewed else 'Нет'}",
                f"Создано: {'Пользователем' if card.origin == 'manual' else 'ИИ'}",
            ]
            if card.description:
                details.append(f"Описание: {card.description[:1600]}")
            source = BoardSource(
                **common,
                source_id=f"B-{meeting.id}-card-{card.id}",
                kind="kanban",
                card_id=card.id,
                title=card.title,
                text="\n".join(details),
                provisional=partial and not human,
                transcript_quote=quote,
            )
            sources.append(source)
            if card.kind == "task" and card.status in {"todo", "doing", "blocked"}:
                active.add(source.source_id)
    return sources, snapshots, active


def retrieve_board_sources(db, workspace_id, question, related_meetings=(), *, budget=14000):
    candidates, snapshots, active = board_candidates(db, workspace_id)
    scores = lexical_scores(
        [SimpleNamespace(id=s.source_id, text=s.text) for s in candidates], question
    )
    related = set(related_meetings)
    # Search every board, including canonical terms absent/misheard in the transcript.
    # If the question is broad, current open work and summary context lead the fallback.
    ranked = sorted(
        enumerate(candidates),
        key=lambda item: (
            -scores.get(item[1].source_id, 0),
            item[1].meeting_id not in related,
            item[1].source_id not in active,
            item[1].provisional,
            item[0],
        ),
    )
    selected, used = [], 0
    for _, source in ranked:
        cost = len(source.model_dump_json())
        if used + cost > budget or len(selected) >= 24:
            continue
        selected.append(source)
        used += cost
    return (
        selected,
        BoardCoverage(available_sources=len(candidates), selected_sources=len(selected)),
        snapshots,
    )
