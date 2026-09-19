import json
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select
from test_rag import create_meeting

from aimeet_api.modules.intelligence.models import MeetingBoard
from aimeet_api.modules.intelligence.schemas import Card
from aimeet_api.modules.rag.models import AssistantTaskOperation
from aimeet_api.modules.rag.router import get_providers
from aimeet_api.modules.rag.schemas import AssistantTaskDraft, TaskCreationPlan


@pytest.fixture
def planner(app):
    class Planner:
        calls = 0
        response = None

        def ensure_configured(self, **_):
            pass

        def plan_tasks(self, instructions, context):
            self.calls += 1
            assert "Only create additional tasks" in instructions
            self.context = json.loads(context)
            return self.response

    fake = Planner()
    app.dependency_overrides[get_providers] = lambda: fake
    yield fake
    app.dependency_overrides.clear()


def draft(mid, title="Проверить фронтенд"):
    return AssistantTaskDraft(
        meeting_id=uuid.UUID(mid),
        title=title,
        description="Добавленная задача",
        assignee=None,
        due_date=None,
        due_text=None,
    )


def request_body():
    return {
        "request_id": str(uuid.uuid4()),
        "question": "Добавь задачу проверить фронтенд",
        "history": [],
    }


def test_task_saved_and_replay_does_not_duplicate(app, authenticated_client, planner):
    client = authenticated_client
    mid = create_meeting(client)
    planner.response = TaskCreationPlan(action="create", answer="", tasks=[draft(mid)])
    body = request_body()
    response = client.post("/api/v1/assistant/tasks", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "created"
    task = response.json()["tasks"][0]
    assert task["meeting_id"] == mid
    board = client.get(f"/api/v1/meetings/{mid}/board").json()
    assert board["cards"][0]["id"] == task["card_id"]
    assert board["cards"][0]["status"] == "todo"
    assert board["cards"][0]["quote"] is None
    assert client.post("/api/v1/assistant/tasks", json=body).json() == response.json()
    assert planner.calls == 1
    assert len(client.get(f"/api/v1/meetings/{mid}/board").json()["cards"]) == 1
    assert (
        client.post(
            "/api/v1/assistant/tasks", json={**body, "question": "Добавь другую задачу"}
        ).status_code
        == 409
    )


def test_existing_cards_preserved_and_multiple_tasks_added(app, authenticated_client, planner):
    client = authenticated_client
    mid = create_meeting(client)
    initial = client.post(
        f"/api/v1/meetings/{mid}/board/cards", json={"version": 0, "title": "Существующая задача"}
    ).json()
    planner.response = TaskCreationPlan(
        action="create", answer="", tasks=[draft(mid), draft(mid, "Проверить бэкенд")]
    )
    response = client.post("/api/v1/assistant/tasks", json=request_body())
    assert response.status_code == 200, response.text
    board = client.get(f"/api/v1/meetings/{mid}/board").json()
    assert len(board["cards"]) == 3
    assert board["cards"][0]["id"] == initial["cards"][0]["id"]
    assert board["version"] == initial["version"] + 1


def test_clarification_writes_nothing(app, authenticated_client, planner):
    mid = create_meeting(authenticated_client)
    planner.response = TaskCreationPlan(
        action="clarify", answer="К какой встрече добавить задачу?", tasks=[]
    )
    response = authenticated_client.post("/api/v1/assistant/tasks", json=request_body())
    assert response.status_code == 200
    assert response.json()["status"] == "clarification"
    with app.state.session_factory() as db:
        assert db.get(MeetingBoard, uuid.UUID(mid)) is None
        assert list(db.scalars(select(AssistantTaskOperation))) == []


def test_foreign_meeting_plan_is_rejected(app, authenticated_client, bob, planner):
    client = authenticated_client
    foreign = create_meeting(client)
    client.post("/api/v1/auth/logout")
    client.post("/api/v1/auth/login", json=bob.credentials)
    own = create_meeting(client)
    planner.response = TaskCreationPlan(action="create", answer="", tasks=[draft(foreign)])
    response = client.post("/api/v1/assistant/tasks", json=request_body())
    assert response.status_code == 502
    assert {m["meeting_id"] for m in planner.context["untrusted_meeting_catalog"]} == {own}
    with app.state.session_factory() as db:
        assert db.get(MeetingBoard, uuid.UUID(foreign)) is None
        assert db.get(MeetingBoard, uuid.UUID(own)) is None


def test_multi_board_failure_rolls_back_all_writes(app, authenticated_client, planner):
    client = authenticated_client
    ids = sorted([create_meeting(client), create_meeting(client)])
    with app.state.session_factory() as db:
        db.add(
            MeetingBoard(
                meeting_id=uuid.UUID(ids[1]),
                cards=[
                    Card(id=uuid.uuid4(), origin="manual", title="Задача").model_dump(mode="json")
                    for _ in range(1500)
                ],
            )
        )
        db.commit()
    planner.response = TaskCreationPlan(
        action="create", answer="", tasks=[draft(mid) for mid in ids]
    )
    response = client.post("/api/v1/assistant/tasks", json=request_body())
    assert response.status_code == 422, response.text
    with app.state.session_factory() as db:
        assert db.get(MeetingBoard, uuid.UUID(ids[0])) is None
        assert len(db.get(MeetingBoard, uuid.UUID(ids[1])).cards) == 1500
        assert list(db.scalars(select(AssistantTaskOperation))) == []


def test_creation_rechecks_deleted_meeting(app, authenticated_client, planner):
    client = authenticated_client
    mid = create_meeting(client)

    def deleted(*_):
        assert client.delete(f"/api/v1/meetings/{mid}").status_code == 204
        return TaskCreationPlan(action="create", answer="", tasks=[draft(mid)])

    planner.plan_tasks = deleted
    response = client.post("/api/v1/assistant/tasks", json=request_body())
    assert response.status_code == 409, response.text
    with app.state.session_factory() as db:
        assert list(db.scalars(select(AssistantTaskOperation))) == []


def test_task_creation_requires_auth_and_csrf(client, authenticated_client, planner):
    client = authenticated_client
    client.headers.pop("X-Requested-With")
    assert client.post("/api/v1/assistant/tasks", json=request_body()).status_code == 403
    client.headers["X-Requested-With"] = "aimeet"
    client.post("/api/v1/auth/logout")
    assert client.post("/api/v1/assistant/tasks", json=request_body()).status_code == 401
    assert planner.calls == 0


def test_assistant_task_migration_roundtrip(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'migration.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    assert inspect(engine).has_table("assistant_task_operations")
    command.downgrade(cfg, "0004_live_rooms")
    assert not inspect(engine).has_table("assistant_task_operations")
    assert inspect(engine).has_table("meeting_boards")
    command.upgrade(cfg, "head")
    assert inspect(engine).has_table("assistant_task_operations")
    engine.dispose()


def test_created_tasks_are_saved_to_chat_in_same_transaction(authenticated_client, planner):
    client = authenticated_client
    mid = create_meeting(client)
    cid = str(uuid.uuid4())
    assert client.post("/api/v1/assistant/conversations", json={"id": cid}).status_code == 200
    planner.response = TaskCreationPlan(action="create", answer="", tasks=[draft(mid)])
    body = {**request_body(), "conversation_id": cid}
    started = client.post(
        f"/api/v1/assistant/conversations/{cid}/turns/pending",
        json={
            "id": body["request_id"],
            "question": body["question"],
            "activity": "creating",
        },
    )
    assert started.status_code == 200
    response = client.post("/api/v1/assistant/tasks", json=body)
    assert response.status_code == 200, response.text
    # No client save request is needed, even if its HTTP connection was lost.
    saved = client.get(f"/api/v1/assistant/conversations/{cid}").json()
    assert len(saved["turns"]) == 1
    assert saved["turns"][0]["id"] == body["request_id"]
    assert saved["turns"][0]["result"]["tasks"] == response.json()["tasks"]
    assert client.post("/api/v1/assistant/tasks", json=body).status_code == 200
    assert len(client.get(f"/api/v1/assistant/conversations/{cid}").json()["turns"]) == 1


@pytest.mark.parametrize("target", ["created", "foreign", None])
def test_created_tasks_only_open_model_selected_authorized_panel(
    authenticated_client, planner, target
):
    mid = create_meeting(authenticated_client)
    panel = None if target is None else {
        "meeting_id": mid if target == "created" else str(uuid.uuid4()), "view": "kanban"
    }
    planner.response = TaskCreationPlan(
        action="create", answer="", tasks=[draft(mid)], panel=panel
    )
    body = request_body()
    response = authenticated_client.post("/api/v1/assistant/tasks", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["panel"] == (panel if target == "created" else None)
    assert authenticated_client.post("/api/v1/assistant/tasks", json=body).json() == response.json()
