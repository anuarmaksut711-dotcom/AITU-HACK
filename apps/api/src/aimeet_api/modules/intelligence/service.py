import hashlib
import uuid

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from aimeet_api.modules.intelligence.evidence import EvidenceIndex, enrich_card, locate
from aimeet_api.modules.intelligence.models import MeetingBoard
from aimeet_api.modules.intelligence.schemas import BoardOutput, Card, SummaryOutput
from aimeet_api.modules.rag.providers import RagError


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def get_board(db, meeting_id):
    board = db.get(MeetingBoard, meeting_id)
    if board is None:
        board = MeetingBoard(meeting_id=meeting_id)
        db.add(board)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            board = db.get(MeetingBoard, meeting_id)
            if board is None:
                raise RagError("MEETING_DELETED", 404) from None
    return board


def output(board, meeting=None):
    index = EvidenceIndex(meeting.transcript, meeting.segments) if meeting else None
    summary = []
    for sentence in board.summary:
        item = SummaryOutput.model_validate(sentence)
        if index:
            try:
                item.evidence = index.enrich(locate(index.text, item.quote))
            except RagError:
                pass
        summary.append(item)
    return BoardOutput(
        version=board.version,
        status=board.status,
        error_code=board.error_code,
        summary=summary,
        cards=[enrich_card(card, index) for card in board.cards] if index else board.cards,
        progress=board.progress,
    )


def save_cards(db, board, version, cards, meeting=None):
    if version != board.version:
        raise RagError("BOARD_CONFLICT", 409)
    if len(cards) > 1500:
        raise RagError("BOARD_FULL", 422)
    changed = db.execute(
        update(MeetingBoard)
        .execution_options(synchronize_session=False)
        .where(
            MeetingBoard.meeting_id == board.meeting_id,
            MeetingBoard.version == version,
        )
        .values(cards=cards, version=MeetingBoard.version + 1)
    )
    if changed.rowcount != 1:
        db.rollback()
        raise RagError("BOARD_CONFLICT", 409)
    db.commit()
    db.refresh(board)
    return output(board, meeting)


def make_card(payload, transcript, *, previous=None):
    data = payload.model_dump(mode="json", exclude={"version"})
    quote = data.get("quote")
    start = data.pop("quote_start", None)
    if previous and quote == previous.get("quote") and start is None:
        start = previous.get("start_char")
    ref = locate(transcript, quote, start) if quote else None
    return Card(
        **data,
        id=previous["id"] if previous else uuid.uuid4(),
        origin=previous["origin"] if previous else "manual",
        quote_start=ref.start_char if ref else None,
        start_char=ref.start_char if ref else None,
        end_char=ref.end_char if ref else None,
        revisions=previous.get("revisions", []) if previous else [],
    ).model_dump(mode="json")
