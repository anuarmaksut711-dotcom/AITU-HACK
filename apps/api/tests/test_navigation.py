import json
import uuid

import pytest
from test_rag import create_meeting

from aimeet_api.modules.rag.router import get_providers
from aimeet_api.modules.rag.schemas import PanelPlan


@pytest.fixture
def navigator(app):
    class FakeNavigator:
        result = None
        calls = 0

        def ensure_configured(self, **kwargs):
            assert kwargs == {"generation_only": True}

        def embed(self, *_):
            raise AssertionError("Navigation must not use embeddings")

        def _generate(self, instructions, context, response_model):
            assert response_model is PanelPlan
            self.calls += 1
            self.catalog = json.loads(context)["untrusted_meeting_catalog"]
            return self.result

    fake = FakeNavigator()
    app.dependency_overrides[get_providers] = lambda: fake
    yield fake
    app.dependency_overrides.clear()


def test_navigation_opens_unindexed_meeting(authenticated_client, navigator):
    mid = create_meeting(authenticated_client)
    navigator.result = PanelPlan(
        meeting_id=mid, view="kanban", answer="Открываю канбан.", choices=[]
    )
    response = authenticated_client.post(
        "/api/v1/assistant/panel", json={"question": "Открой канбан"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["mode"] == "navigation"
    assert response.json()["panel"] == {"meeting_id": mid, "view": "kanban"}
    assert response.json()["meetings"][0]["id"] == mid


def test_navigation_offers_choices_for_ambiguous_meetings(authenticated_client, navigator):
    ids = [create_meeting(authenticated_client) for _ in range(2)]
    navigator.result = PanelPlan(
        meeting_id=None, view="kanban", answer="Какую встречу открыть?", choices=ids
    )
    response = authenticated_client.post(
        "/api/v1/assistant/panel", json={"question": "Открой канбан"}
    )
    assert response.json()["panel"] is None
    assert {item["id"] for item in response.json()["meetings"]} == set(ids)


def test_navigation_rejects_foreign_target_and_success_claim(authenticated_client, navigator):
    mid = create_meeting(authenticated_client)
    navigator.result = PanelPlan(
        meeting_id=uuid.uuid4(), view="kanban", answer="Opened", choices=[]
    )
    response = authenticated_client.post(
        "/api/v1/assistant/panel", json={"question": "Открой канбан"}
    )
    assert response.json()["panel"] is None
    assert response.json()["answer"] == ""
    assert response.json()["meetings"][0]["id"] == mid


def test_navigation_empty_catalog_skips_provider(authenticated_client, navigator):
    response = authenticated_client.post(
        "/api/v1/assistant/panel", json={"question": "Открой канбан"}
    )
    assert response.status_code == 200
    assert response.json()["panel"] is None
    assert response.json()["meetings"] == []
    assert navigator.calls == 0
