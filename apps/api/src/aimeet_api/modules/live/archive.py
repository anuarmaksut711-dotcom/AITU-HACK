"""Carry grounded live outcomes into the saved meeting without another model call."""

import uuid

from sqlalchemy import select

from aimeet_api.db.models import Meeting
from aimeet_api.modules.intelligence.models import MeetingBoard
from aimeet_api.modules.intelligence.schemas import Card
from aimeet_api.modules.intelligence.service import digest
from aimeet_api.modules.live.models import LiveChunk, LiveParticipant

KINDS = {
    "goal": "topic",
    "idea": "topic",
    "task": "task",
    "decision": "decision",
    "question": "question",
}


def sync_archived_board(db, room):
    if room.meeting_id is None:
        return None
    meeting = db.scalar(
        select(Meeting).where(Meeting.id == room.meeting_id).with_for_update(of=Meeting)
    )
    if meeting is None:
        return None
    board = db.scalar(
        select(MeetingBoard).where(MeetingBoard.meeting_id == meeting.id).with_for_update()
    )
    if board is not None and not (
        board.status == "idle"
        or (board.status == "running" and board.lease_token is None)
        or (board.status == "failed" and (board.error_code or "").startswith("LIVE_"))
    ):
        # Completed boards, their edits, and an independently requested analysis win.
        return board
    if board is None:
        board = MeetingBoard(meeting_id=meeting.id, cards=[], summary=[], version=0)
        db.add(board)
    board.source_hash = digest(meeting.transcript)
    board.version += 1
    if room.analysis_through < room.transcript_revision or room.analysis_status not in {
        "ready",
        "failed",
    }:
        # No lease means the live worker owns this pending publication. The archive
        # worker must not claim it and generate a second, unrelated set of outcomes.
        board.status, board.progress, board.error_code = "running", 0, None
        return board
    if room.analysis_status == "failed":
        board.status = "failed"
        board.error_code = f"LIVE_{room.analysis_error or 'ANALYSIS_FAILED'}"
        return board

    rows = db.execute(
        select(LiveChunk, LiveParticipant.name)
        .join(LiveParticipant)
        .where(LiveChunk.room_id == room.id, LiveChunk.status == "done", LiveChunk.text != "")
        .order_by(LiveChunk.start, LiveChunk.id)
    ).all()
    sources = {}
    cursor = 0
    for chunk, name in rows:
        prefix = f"[{int(chunk.start) // 60:02}:{int(chunk.start) % 60:02}] {name}: "
        line = prefix + chunk.text
        position = meeting.transcript.find(line, cursor)
        if position < 0:
            continue
        start = position + len(prefix)
        sources[str(chunk.id)] = (chunk.text, start)
        cursor = position + len(line)

    summaries, cards = [], []
    for note in room.insights:
        ids = note.get("source_ids", [])
        kind = KINDS.get(note.get("kind"))
        if not kind or not ids or any(identifier not in sources for identifier in ids):
            continue
        # The first cited utterance is the navigation anchor, as in the live UI.
        quote, start = sources[ids[0]]
        title = note["text"]
        card = Card(
            id=uuid.uuid5(room.id, f"{note['kind']}:{title}"),
            kind=kind,
            title=title,
            origin="ai",
            quote=quote,
            quote_start=start,
            start_char=start,
            end_char=start + len(quote),
        ).model_dump(mode="json")
        cards.append(card)
        summaries.append({"text": title, "quote": quote})
    existing_ids = {card["id"] for card in board.cards}
    board.cards = [*board.cards, *(card for card in cards if card["id"] not in existing_ids)]
    board.summary = summaries
    board.status, board.progress, board.error_code = "ready", 100, None
    return board
