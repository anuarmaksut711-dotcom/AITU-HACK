from uuid import uuid4

import pytest

MEETING = {
    "title": "Планирование команды",
    "language": "ru",
    "transcript": "Алия: Согласуем план на неделю.\nМарат: Подготовлю предложение к пятнице.",
}


def create_meeting(client, **changes):
    response = client.post("/api/v1/meetings", json={**MEETING, **changes})
    assert response.status_code == 201, response.text
    return response.json()


def test_create_read_list_and_delete_meeting(authenticated_client):
    client = authenticated_client
    meeting = create_meeting(client)

    assert meeting["title"] == MEETING["title"]
    assert meeting["language"] == "ru"
    assert meeting["status"] == "draft"
    assert meeting["source_type"] == "text"
    assert meeting["transcript"] == MEETING["transcript"]
    assert meeting["transcript_length"] == len(MEETING["transcript"])
    assert meeting["created_at"]
    assert meeting["updated_at"]

    detail = client.get(f"/api/v1/meetings/{meeting['id']}")
    assert detail.status_code == 200
    assert detail.json() == meeting

    listing = client.get("/api/v1/meetings")
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 1
    assert body["limit"] == 20
    assert body["offset"] == 0
    assert body["items"][0]["id"] == meeting["id"]
    assert "transcript" not in body["items"][0]

    deleted = client.delete(f"/api/v1/meetings/{meeting['id']}")
    assert deleted.status_code == 204
    assert not deleted.content
    assert client.get(f"/api/v1/meetings/{meeting['id']}").status_code == 404
    assert client.get("/api/v1/meetings").json()["total"] == 0


def test_workspace_boundaries_apply_to_list_detail_and_delete(client, alice, bob):
    assert client.post("/api/v1/auth/login", json=alice.credentials).status_code == 200
    meeting = create_meeting(client)
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.post("/api/v1/auth/login", json=bob.credentials).status_code == 200

    listing = client.get("/api/v1/meetings").json()
    assert listing["total"] == 0
    assert listing["items"] == []
    assert client.get(f"/api/v1/meetings/{meeting['id']}").status_code == 404
    assert client.delete(f"/api/v1/meetings/{meeting['id']}").status_code == 404

    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.post("/api/v1/auth/login", json=alice.credentials).status_code == 200
    assert client.get(f"/api/v1/meetings/{meeting['id']}").status_code == 200


def test_transcript_whitespace_is_preserved_verbatim_in_storage(authenticated_client):
    transcript = " \t\nАлия:\tСогласуем план.\r\n\nМарат: Хорошо.  \n\t "
    meeting = create_meeting(
        authenticated_client, title="  Рабочая встреча  ", transcript=transcript
    )

    assert meeting["title"] == "Рабочая встреча"
    assert meeting["transcript"] == transcript
    assert meeting["transcript_length"] == len(transcript)
    detail = authenticated_client.get(f"/api/v1/meetings/{meeting['id']}")
    assert detail.status_code == 200
    assert detail.json()["transcript"] == transcript
    assert detail.json()["transcript_length"] == len(transcript)
    listing = authenticated_client.get("/api/v1/meetings").json()
    assert listing["items"][0]["transcript_length"] == len(transcript)


def test_search_and_pagination_preserve_total_and_do_not_repeat_rows(authenticated_client):
    client = authenticated_client
    ids = {create_meeting(client, title=f"Planning session {number}")["id"] for number in range(3)}
    other = create_meeting(client, title="Customer interview")

    first = client.get("/api/v1/meetings", params={"q": "Planning", "limit": 2}).json()
    second = client.get(
        "/api/v1/meetings", params={"q": "Planning", "limit": 2, "offset": 2}
    ).json()

    assert first["total"] == second["total"] == 3
    assert (first["limit"], first["offset"]) == (2, 0)
    assert (second["limit"], second["offset"]) == (2, 2)
    first_ids = {meeting["id"] for meeting in first["items"]}
    second_ids = {meeting["id"] for meeting in second["items"]}
    assert len(first_ids) == 2
    assert len(second_ids) == 1
    assert first_ids.isdisjoint(second_ids)
    assert first_ids | second_ids == ids
    assert other["id"] not in first_ids | second_ids
    assert client.get("/api/v1/meetings", params={"q": "unmatched-title"}).json()["total"] == 0


@pytest.mark.parametrize("language", ["auto", "ru", "kk", "en"])
def test_supported_languages_are_preserved(authenticated_client, language):
    meeting = create_meeting(authenticated_client, language=language)
    assert meeting["language"] == language


def test_invalid_language_is_rejected_without_creating_a_meeting(authenticated_client):
    response = authenticated_client.post(
        "/api/v1/meetings", json={**MEETING, "language": "unsupported"}
    )
    assert response.status_code == 422
    assert authenticated_client.get("/api/v1/meetings").json()["total"] == 0


@pytest.mark.parametrize("field", ["title", "transcript"])
def test_whitespace_only_meeting_content_is_rejected(authenticated_client, field):
    response = authenticated_client.post("/api/v1/meetings", json={**MEETING, field: " \n\t "})
    assert response.status_code == 422
    assert authenticated_client.get("/api/v1/meetings").json()["total"] == 0


def test_a_client_cannot_choose_a_workspace_or_creator(authenticated_client):
    response = authenticated_client.post(
        "/api/v1/meetings",
        json={**MEETING, "workspace_id": str(uuid4()), "created_by": str(uuid4())},
    )
    assert response.status_code == 422
    assert authenticated_client.get("/api/v1/meetings").json()["total"] == 0


def test_search_treats_sql_wildcards_as_literal_text(authenticated_client):
    literal = create_meeting(authenticated_client, title="Revenue 10%_review")
    create_meeting(authenticated_client, title="Revenue 100 percent review")

    response = authenticated_client.get("/api/v1/meetings", params={"q": "%_"})
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["id"] == literal["id"]


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": -1}, {"offset": -1}])
def test_invalid_pagination_is_rejected(authenticated_client, params):
    assert authenticated_client.get("/api/v1/meetings", params=params).status_code == 422


def test_missing_meetings_return_not_found(authenticated_client):
    path = f"/api/v1/meetings/{uuid4()}"
    assert authenticated_client.get(path).status_code == 404
    assert authenticated_client.delete(path).status_code == 404


def test_csrf_failure_does_not_create_or_delete_data(authenticated_client):
    client = authenticated_client
    meeting = create_meeting(client)
    client.headers.pop("X-Requested-With", None)

    assert client.post("/api/v1/meetings", json=MEETING).status_code == 403
    assert client.delete(f"/api/v1/meetings/{meeting['id']}").status_code == 403
    assert client.get("/api/v1/meetings").json()["total"] == 1
    assert client.get(f"/api/v1/meetings/{meeting['id']}").status_code == 200


def test_unauthenticated_mutations_are_rejected(client):
    assert client.post("/api/v1/meetings", json=MEETING).status_code == 401
    assert client.delete(f"/api/v1/meetings/{uuid4()}").status_code == 401
