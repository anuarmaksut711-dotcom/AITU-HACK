"""Controlled model outputs test contracts, not actual LLM quality."""

import csv
import io
import json
import uuid

import httpx
import pytest

from aimeet_api.db.models import Meeting
from aimeet_api.modules.intelligence.evidence import EvidenceIndex, locate
from aimeet_api.modules.intelligence.models import MeetingBoard
from aimeet_api.modules.intelligence.provider import ProtocolProvider
from aimeet_api.modules.intelligence.schemas import CardInput, GeneratedProtocol, Reconciliation
from aimeet_api.modules.intelligence.timeline import reconcile
from aimeet_api.modules.intelligence.worker import ProtocolWorker, grounded
from aimeet_api.modules.rag.providers import RagError
from aimeet_api.modules.transcription.storage import audio_path

BEFORE = "Алия: подготовлю смету к пятнице."
AFTER = "Алия: меняю срок сметы, подготовлю к понедельнику."
TEXT = f"📝 Начало встречи.\n{BEFORE}\n{AFTER}"


def generated(quote=AFTER, due_text="к понедельнику", revisions=None, agreement="confirmed"):
    return GeneratedProtocol.model_validate(
        {
            "summary": [{"text": "Алия подготовит смету к понедельнику.", "quote": quote}],
            "cards": [
                {
                    "kind": "task",
                    "title": "Подготовить смету",
                    "description": "",
                    "assignee": "Алия",
                    "due_text": due_text,
                    "priority": "unspecified",
                    "priority_evidence": None,
                    "quote": quote,
                    "agreement": agreement,
                    "revisions": revisions or [],
                }
            ],
        }
    )


def revision():
    return {
        "field": "due_text",
        "before_value": "к пятнице",
        "after_value": "к понедельнику",
        "before": {"quote": BEFORE},
        "after": {"quote": AFTER},
    }


def create(client, text=TEXT):
    response = client.post(
        "/api/v1/meetings", json={"title": "Проверка источников", "transcript": text}
    )
    assert response.status_code == 201
    return response.json()["id"]


def url(mid):
    return f"/api/v1/meetings/{mid}/board"


def values(card):
    return {k: v for k, v in card.items() if k in CardInput.model_fields}


def test_evidence_unicode_repeated_quotes_and_misaligned_segments():
    ref = locate(TEXT, AFTER)
    assert TEXT[ref.start_char : ref.end_char] == AFTER
    segments = [
        {"text": line, "start": i * 8, "end": i * 8 + 6} for i, line in enumerate(TEXT.splitlines())
    ]
    resolved = EvidenceIndex(TEXT, segments).enrich(ref)
    assert (resolved.start_seconds, resolved.end_seconds, resolved.speaker) == (16, 22, "Алия")
    assert EvidenceIndex(TEXT, segments[:-1]).enrich(ref).start_seconds is None
    with pytest.raises(RagError, match="AMBIGUOUS_QUOTE"):
        locate("Да.\nДа.", "Да.")
    assert locate("Да.\nДа.", "Да.", 4).start_char == 4
    with pytest.raises(RagError):
        locate(TEXT, AFTER, 0)
    mixed = "Алия: начнём.\nМарат: согласен."
    assert EvidenceIndex(mixed).enrich(locate(mixed, mixed)).speaker is None


def test_revision_grounding_and_chronology():
    card = grounded(generated(revisions=[revision()]), TEXT)[0]
    assert card["due_text"] == "к понедельнику"
    history = card["revisions"][0]
    assert history["before"]["quote"] == BEFORE
    assert history["after"]["start_char"] > history["before"]["start_char"]
    wrong = revision()
    wrong["after_value"] = "завтра"
    for result, source in [
        (generated(revisions=[wrong]), TEXT),
        (generated(revisions=[revision()]), f"{AFTER}\n{BEFORE}"),
        (generated(revisions=[revision()], agreement="proposed"), TEXT),
        (generated(revisions=[revision()], due_text="к пятнице"), TEXT),
    ]:
        with pytest.raises(RagError, match="INVALID_CORRECTION"):
            grounded(result, source)


def test_history_sources_survive_edit_and_export(authenticated_client, app):
    client = authenticated_client
    mid = create(client)
    with app.state.session_factory() as db:
        meeting = db.get(Meeting, uuid.UUID(mid))
        meeting.segments = [
            {"text": line, "start": i * 8, "end": i * 8 + 6}
            for i, line in enumerate(TEXT.splitlines())
        ]
        db.add(
            MeetingBoard(
                meeting_id=meeting.id,
                status="ready",
                cards=grounded(generated(revisions=[revision()]), TEXT),
                summary=generated().model_dump()["summary"],
            )
        )
        db.commit()
    board = client.get(url(mid)).json()
    card = board["cards"][0]
    assert card["evidence"]["start_seconds"] == 16
    assert card["revisions"][0]["before"]["start_seconds"] == 8
    assert board["summary"][0]["evidence"]["start_seconds"] == 16
    assert set(card["clarifications"]) == {"date_unresolved", "priority_missing"}
    response = client.post(
        url(mid) + "/cards/" + card["id"],
        json={
            **values(card),
            "version": board["version"],
            "due_date": "2026-09-14",
            "reviewed": True,
        },
    )
    assert response.status_code == 200, response.text
    updated = response.json()["cards"][0]
    assert updated["revisions"] == card["revisions"]
    assert "date_unresolved" not in updated["clarifications"]
    forged = client.post(
        url(mid) + "/cards/" + card["id"],
        json={
            **values(updated),
            "version": response.json()["version"],
            "revisions": [],
        },
    )
    assert forged.status_code == 422
    assert (
        client.get(url(mid) + "/export/json").json()["cards"][0]["revisions"] == card["revisions"]
    )
    rows = list(
        csv.DictReader(io.StringIO(client.get(url(mid) + "/export/csv").text.lstrip("\ufeff")))
    )
    assert rows[1]["Договорённость"] == "Согласовано"
    assert "к пятнице" in rows[1]["История изменений в разговоре"]
    assert "BEGIN:VEVENT" in client.get(url(mid) + "/export/ics").text


def test_proposals_stay_out_of_calendar(authenticated_client):
    client = authenticated_client
    mid = create(client)
    response = client.post(
        url(mid) + "/cards",
        json={
            "version": 0,
            "title": "Обсудить бюджет",
            "agreement": "proposed",
            "due_date": "2026-09-18",
            "reviewed": True,
        },
    )
    card = response.json()["cards"][0]
    assert "assignee_missing" in card["clarifications"]
    assert "agreement_unconfirmed" in card["clarifications"]
    assert "BEGIN:VEVENT" not in client.get(url(mid) + "/export/ics").text
    response = client.post(
        url(mid) + "/cards/" + card["id"],
        json={
            **values(card),
            "version": 1,
            "agreement": "confirmed",
            "assignee": "Алия",
        },
    )
    assert response.status_code == 200
    assert "assignee_missing" not in response.json()["cards"][0]["clarifications"]


def test_legacy_cards_remain_readable(authenticated_client, app):
    mid = create(authenticated_client)
    card = grounded(generated(), TEXT)[0]
    for key in ("quote_start", "agreement", "evidence", "revisions", "clarifications"):
        card.pop(key)
    with app.state.session_factory() as db:
        db.add(MeetingBoard(meeting_id=uuid.UUID(mid), cards=[card]))
        db.commit()
    loaded = authenticated_client.get(url(mid)).json()["cards"][0]
    assert loaded["agreement"] == "unclear"
    assert loaded["evidence"]["quote"] == AFTER


class LinkingProvider:
    def __init__(self, resolved=True, invalid=False):
        self.resolved, self.invalid = resolved, invalid

    def reconcile(self, payload):
        cards = json.loads(payload)
        return Reconciliation.model_validate(
            {
                "links": [
                    {
                        "earlier_id": str(uuid.uuid4()) if self.invalid else cards[0]["id"],
                        "later_id": cards[-1]["id"],
                        "quote": AFTER,
                        "resolved": self.resolved,
                    }
                ]
            }
        )


def test_cross_chunk_correction_and_unresolved_dispute():
    old = grounded(generated(BEFORE, "к пятнице"), TEXT)[0]
    new = grounded(generated(), TEXT)[0]
    cards, changed = reconcile([old, new], TEXT, LinkingProvider(), lambda: None)
    assert changed and len(cards) == 1
    assert cards[0]["due_text"] == "к понедельнику"
    assert cards[0]["revisions"][0]["before"]["quote"] == BEFORE
    old = grounded(generated(BEFORE, "к пятнице"), TEXT)[0]
    new = grounded(generated(), TEXT)[0]
    cards, _ = reconcile([old, new], TEXT, LinkingProvider(resolved=False), lambda: None)
    assert len(cards) == 2 and all(c["agreement"] == "unclear" for c in cards)
    with pytest.raises(RagError, match="INVALID_CORRECTION"):
        reconcile([old, new], TEXT, LinkingProvider(invalid=True), lambda: None)


def test_worker_correction_across_real_chunk_boundary(authenticated_client, app):
    transcript = BEFORE + "\n" + ("Обсуждение общей темы.\n" * 620) + AFTER
    mid = create(authenticated_client, transcript)

    class ChunkProvider(LinkingProvider):
        def generate(self, text, *, synthesis=False):
            if synthesis or AFTER in text:
                value = generated()
                if synthesis:
                    value.cards = []
                return value
            if BEFORE in text:
                return generated(BEFORE, "к пятнице")
            return GeneratedProtocol(summary=[], cards=[])

    authenticated_client.post(url(mid) + "/generate")
    ProtocolWorker(app.state.session_factory, app.state.settings, ChunkProvider()).run_once()
    board = authenticated_client.get(url(mid)).json()
    assert board["status"] == "ready", board
    assert len(board["cards"]) == 1
    assert board["cards"][0]["due_text"] == "к понедельнику"
    assert board["cards"][0]["revisions"][0]["before"]["quote"] == BEFORE


def test_audio_authorized_seekable_not_cached(authenticated_client, app, bob, tmp_path):
    client = authenticated_client
    app.state.settings.audio_dir = tmp_path / "audio"
    response = client.post(
        "/api/v1/meetings/audio",
        params={"title": "Аудио", "filename": "test.wav"},
        content=b"RIFF" + bytes(range(64)),
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 202
    mid = response.json()["id"]
    endpoint = f"/api/v1/meetings/{mid}/audio"
    audio = client.get(endpoint, headers={"Range": "bytes=4-11"})
    assert audio.status_code == 206
    assert audio.content == bytes(range(8))
    assert audio.headers["content-type"] == "audio/wav"
    assert "no-store" in audio.headers["cache-control"]
    assert client.get(endpoint, headers={"Range": "bytes=9999-"}).status_code == 416
    audio_path(app.state.settings, uuid.UUID(mid)).unlink()
    assert client.get(endpoint).status_code == 404
    client.post("/api/v1/auth/login", json=bob.credentials)
    assert client.get(endpoint).status_code == 404
    client.post("/api/v1/auth/logout")
    assert client.get(endpoint).status_code == 401


def test_reconciliation_provider_stays_local(app):
    def transport(request):
        assert request.url.host == "host.docker.internal"
        payload = json.loads(request.content)
        assert "links" in payload["format"]["properties"]
        return httpx.Response(200, json={"done": True, "message": {"content": '{"links":[]}'}})

    provider = ProtocolProvider(app.state.settings, httpx.MockTransport(transport))
    assert provider.reconcile("[]").links == []


def test_final_value_can_be_supported_by_its_revision_quote():
    source = f"{BEFORE}\n{AFTER}\nИтоги встречи."
    result = generated(quote="Итоги встречи.", revisions=[revision()])
    card = grounded(result, source)[0]
    assert card["due_text"] == "к понедельнику"
    assert card["assignee"] is None
    assert card["revisions"][0]["after_value"] == card["due_text"]
