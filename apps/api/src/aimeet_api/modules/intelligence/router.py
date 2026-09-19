import csv
import io
import json
import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import update

from aimeet_api.core.dependencies import CurrentUser, DatabaseDep, require_csrf
from aimeet_api.modules.intelligence.models import MeetingBoard
from aimeet_api.modules.intelligence.schemas import BoardOutput, CardCreate, CardUpdate
from aimeet_api.modules.intelligence.service import digest, get_board, make_card, output, save_cards
from aimeet_api.modules.meetings.repository import MeetingRepository
from aimeet_api.modules.rag.providers import RagError

router = APIRouter(prefix="/meetings/{meeting_id}/board", tags=["Meeting intelligence"])


def owned(db, user, meeting_id):
    meeting = MeetingRepository(db, user.workspace_id).get(meeting_id)
    if meeting is None:
        raise HTTPException(404, "Meeting not found")
    return meeting


@router.get("", response_model=BoardOutput, operation_id="getMeetingBoard")
def read_board(meeting_id: uuid.UUID, user: CurrentUser, db: DatabaseDep):
    meeting = owned(db, user, meeting_id)
    board = db.get(MeetingBoard, meeting_id)
    return (
        output(board, meeting)
        if board
        else BoardOutput(
            version=0,
            status="idle",
            error_code=None,
            summary=[],
            cards=[],
            progress=0,
        )
    )


@router.post(
    "/generate",
    response_model=BoardOutput,
    dependencies=[Depends(require_csrf)],
    operation_id="generateMeetingBoard",
)
def generate(meeting_id: uuid.UUID, user: CurrentUser, db: DatabaseDep):
    meeting = owned(db, user, meeting_id)
    if not meeting.transcript.strip() or (
        meeting.source_type == "audio" and meeting.status != "transcribed"
    ):
        raise RagError("TRANSCRIPT_NOT_READY", 409)
    board = get_board(db, meeting_id)
    # Successful generation is idempotent; never replace reviewed or edited cards.
    db.execute(
        update(MeetingBoard)
        .execution_options(synchronize_session=False)
        .where(
            MeetingBoard.meeting_id == meeting_id,
            MeetingBoard.status.in_(["idle", "failed"]),
        )
        .values(
            status="queued",
            attempts=0,
            progress=0,
            error_code=None,
            source_hash=digest(meeting.transcript),
            lease_until=None,
            lease_token=None,
        )
    )
    db.commit()
    db.refresh(board)
    return output(board, meeting)


@router.post(
    "/cards",
    response_model=BoardOutput,
    status_code=201,
    dependencies=[Depends(require_csrf)],
    operation_id="createMeetingCard",
)
def create_card(meeting_id: uuid.UUID, payload: CardCreate, user: CurrentUser, db: DatabaseDep):
    meeting = owned(db, user, meeting_id)
    board = get_board(db, meeting_id)
    card = make_card(payload, meeting.transcript)
    return save_cards(db, board, payload.version, [*board.cards, card], meeting)


@router.post(
    "/cards/{card_id}",
    response_model=BoardOutput,
    dependencies=[Depends(require_csrf)],
    operation_id="updateMeetingCard",
)
def edit_card(
    meeting_id: uuid.UUID,
    card_id: uuid.UUID,
    payload: CardUpdate,
    user: CurrentUser,
    db: DatabaseDep,
):
    meeting = owned(db, user, meeting_id)
    board = get_board(db, meeting_id)
    previous = next((c for c in board.cards if c["id"] == str(card_id)), None)
    if previous is None:
        raise HTTPException(404, "Card not found")
    card = make_card(payload, meeting.transcript, previous=previous)
    return save_cards(
        db,
        board,
        payload.version,
        [card if c["id"] == str(card_id) else c for c in board.cards],
        meeting,
    )


KIND = {
    "task": "Поручение",
    "decision": "Решение",
    "topic": "Тема",
    "question": "Открытый вопрос",
    "risk": "Риск",
}
STATUS = {
    "todo": "К выполнению",
    "doing": "В работе",
    "blocked": "Заблокировано",
    "done": "Готово",
    "dismissed": "В архиве",
}
PRIORITY = {"unspecified": "Не указан", "low": "Низкий", "medium": "Средний", "high": "Высокий"}
AGREEMENT = {"confirmed": "Согласовано", "proposed": "Предложение", "unclear": "Нужно уточнить"}
CLARIFICATIONS = {
    "agreement_unconfirmed": "Договорённость не подтверждена",
    "assignee_missing": "Ответственный не указан",
    "deadline_missing": "Срок не указан",
    "date_unresolved": "Уточните календарную дату",
    "priority_missing": "Приоритет не указан",
}


def safe_cell(value):
    value = "" if value is None else str(value)
    dangerous = value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n"))
    return "'" + value if dangerous else value


def ics_escape(value):
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("\r", "")
        .replace("\n", "\\n")
        .replace(";", "\\;")
        .replace(",", "\\,")
    )


def fold_line(line):
    parts, current = [], ""
    for char in line:
        if len((current + char).encode("utf-8")) > 75:
            parts.append(current)
            current = " "
        current += char
    return "\r\n".join([*parts, current])


@router.get("/export/{format}", operation_id="exportMeetingBoard")
def export_board(
    meeting_id: uuid.UUID, format: Literal["json", "csv", "ics"], user: CurrentUser, db: DatabaseDep
):
    meeting = owned(db, user, meeting_id)
    board = db.get(MeetingBoard, meeting_id)
    if board is None:
        raise RagError("BOARD_EMPTY", 409)
    data = output(board, meeting).model_dump(mode="json")
    if format == "json":
        body = json.dumps(
            {"format_version": 1, "meeting_title": meeting.title, **data},
            ensure_ascii=False,
            indent=2,
        )
        media = "application/json"
    elif format == "csv":
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "Тип",
                "Суть",
                "Описание",
                "Ответственный",
                "Дата",
                "Срок как озвучен",
                "Приоритет",
                "Статус",
                "Проверено пользователем",
                "Источник",
                "Цитата",
                "Договорённость",
                "Уточнения",
                "Начало записи, сек",
                "История изменений в разговоре",
            ]
        )
        for sentence in data["summary"]:
            writer.writerow(
                [
                    safe_cell(c)
                    for c in [
                        "Выжимка",
                        sentence["text"],
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "ИИ",
                        sentence["quote"],
                        "",
                        "",
                        (sentence["evidence"] or {}).get("start_seconds"),
                        "",
                    ]
                ]
            )
        for card in data["cards"]:
            writer.writerow(
                [
                    safe_cell(c)
                    for c in [
                        KIND[card["kind"]],
                        card["title"],
                        card["description"],
                        card["assignee"],
                        card["due_date"],
                        card["due_text"],
                        PRIORITY[card["priority"]],
                        STATUS[card["status"]],
                        "Да" if card["reviewed"] else "Нет",
                        "ИИ" if card["origin"] == "ai" else "Вручную",
                        card["quote"],
                        AGREEMENT[card["agreement"]],
                        "; ".join(CLARIFICATIONS[c] for c in card["clarifications"]),
                        (card["evidence"] or {}).get("start_seconds"),
                        json.dumps(card["revisions"], ensure_ascii=False)
                        if card["revisions"]
                        else "",
                    ]
                ]
            )
        body, media = "\ufeff" + buffer.getvalue(), "text/csv"
    else:
        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//Aimeet//Meeting tasks//RU",
            "CALSCALE:GREGORIAN",
        ]
        for card in data["cards"]:
            if (
                card["kind"] != "task"
                or not card["due_date"]
                or not card["reviewed"]
                or card["agreement"] != "confirmed"
                or card["status"] in {"done", "dismissed"}
            ):
                continue
            from datetime import date

            due = date.fromisoformat(card["due_date"])
            lines += [
                "BEGIN:VEVENT",
                f"UID:{card['id']}@aimeet.local",
                "DTSTAMP:" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
                "DTSTART;VALUE=DATE:" + due.strftime("%Y%m%d"),
                "DURATION:P1D",
                "SUMMARY:" + ics_escape(card["title"]),
                "DESCRIPTION:" + ics_escape(meeting.title + "\n" + card["description"]),
                "END:VEVENT",
            ]
        lines.append("END:VCALENDAR")
        body, media = "\r\n".join(fold_line(line) for line in lines) + "\r\n", "text/calendar"
    return Response(
        body,
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="meeting-{meeting_id}.{format}"',
        },
    )
