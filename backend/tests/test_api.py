from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.dependencies import get_db_session
from app.db import create_database_engine, create_tables
from app.main import app


@pytest.fixture
def api_client(tmp_path: Path) -> Iterator[TestClient]:
    database_path = tmp_path / "open-loop-api-test.sqlite"
    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    create_tables(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_session() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_session
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def create_case(client: TestClient, message: str = "Question") -> dict[str, object]:
    response = client.post(
        "/api/v1/cases",
        json={
            "original_message": message,
            "original_message_reference": "tg://chat/123/message/456",
            "end_user_reference": "user-789",
            "moderator_id": "moderator-1",
            "client_contact_id": "client-1",
        },
    )
    assert response.status_code == 201
    return response.json()


def event_types(client: TestClient, public_id: str) -> list[str]:
    response = client.get(f"/api/v1/cases/{public_id}/events")
    assert response.status_code == 200
    return [event["event_type"] for event in response.json()]


def test_create_and_get_case(api_client: TestClient) -> None:
    created = create_case(api_client)

    response = api_client.get(f"/api/v1/cases/{created['public_id']}")

    assert response.status_code == 200
    loaded = response.json()
    assert loaded["public_id"] == created["public_id"]
    assert loaded["status"] == "NEW"
    assert loaded["original_message"] == "Question"
    assert loaded["created_at"].endswith("Z")


def test_get_missing_case_returns_stable_404(api_client: TestClient) -> None:
    response = api_client.get("/api/v1/cases/CASE-MISSING")

    assert response.status_code == 404
    assert response.json() == {
        "error": "case_not_found",
        "message": "Case not found: CASE-MISSING",
    }


def test_invalid_transition_returns_stable_409(api_client: TestClient) -> None:
    case = create_case(api_client)

    response = api_client.post(
        f"/api/v1/cases/{case['public_id']}/user-answered"
    )

    assert response.status_code == 409
    assert response.json()["error"] == "invalid_state_transition"
    assert response.json()["details"] == {
        "current_status": "NEW",
        "requested_status": "CLOSED",
    }


def test_send_to_client_retry_has_one_event(api_client: TestClient) -> None:
    case = create_case(api_client)
    url = f"/api/v1/cases/{case['public_id']}/send-to-client"

    first = api_client.post(url)
    second = api_client.post(url)

    assert first.status_code == 200
    assert first.json()["already_processed"] is False
    assert second.status_code == 200
    assert second.json()["already_processed"] is True
    assert event_types(api_client, str(case["public_id"])).count(
        "CLIENT_REQUEST_SENT"
    ) == 1


def test_client_reply_retry_and_cross_case_conflict(
    api_client: TestClient,
) -> None:
    first_case = create_case(api_client, "First")
    second_case = create_case(api_client, "Second")
    for case in (first_case, second_case):
        response = api_client.post(
            f"/api/v1/cases/{case['public_id']}/send-to-client"
        )
        assert response.status_code == 200

    payload = {
        "external_message_id": "tg-msg-991",
        "text": "Please sign out and log in again.",
        "sender_id": "client-1",
    }
    first_url = f"/api/v1/cases/{first_case['public_id']}/client-reply"
    first = api_client.post(first_url, json=payload)
    retry = api_client.post(first_url, json=payload)
    conflict = api_client.post(
        f"/api/v1/cases/{second_case['public_id']}/client-reply",
        json=payload,
    )

    assert first.status_code == 200
    assert first.json()["already_processed"] is False
    assert retry.status_code == 200
    assert retry.json()["already_processed"] is True
    assert event_types(api_client, str(first_case["public_id"])).count(
        "CLIENT_REPLY_RECEIVED"
    ) == 1
    assert conflict.status_code == 409
    assert conflict.json()["error"] == "external_message_id_conflict"
    second = api_client.get(
        f"/api/v1/cases/{second_case['public_id']}"
    ).json()
    assert second["status"] == "WAITING_CLIENT"


def test_user_answered_retry_has_no_duplicate_events(
    api_client: TestClient,
) -> None:
    case = create_case(api_client)
    public_id = str(case["public_id"])
    api_client.post(f"/api/v1/cases/{public_id}/send-to-client")
    api_client.post(
        f"/api/v1/cases/{public_id}/client-reply",
        json={"external_message_id": "close-reply", "text": "Reply"},
    )

    first = api_client.post(f"/api/v1/cases/{public_id}/user-answered")
    second = api_client.post(f"/api/v1/cases/{public_id}/user-answered")
    events = event_types(api_client, public_id)

    assert first.json()["already_processed"] is False
    assert second.json()["already_processed"] is True
    assert events.count("USER_ANSWER_CONFIRMED") == 1
    assert events.count("CASE_CLOSED") == 1


def test_cancel_retry_has_no_duplicate_event(api_client: TestClient) -> None:
    case = create_case(api_client)
    public_id = str(case["public_id"])
    url = f"/api/v1/cases/{public_id}/cancel"

    first = api_client.post(url, json={"reason": "Resolved elsewhere"})
    second = api_client.post(url, json={"reason": "Retried reason"})

    assert first.json()["already_processed"] is False
    assert second.json()["already_processed"] is True
    assert event_types(api_client, public_id).count("CASE_CANCELLED") == 1


def test_same_moderator_reassignment_is_noop(api_client: TestClient) -> None:
    case = create_case(api_client)
    public_id = str(case["public_id"])
    url = f"/api/v1/cases/{public_id}/reassign"

    first = api_client.post(url, json={"moderator_id": "moderator-2"})
    second = api_client.post(url, json={"moderator_id": "moderator-2"})

    assert first.json()["already_processed"] is False
    assert second.json()["already_processed"] is True
    assert second.json()["moderator_id"] == "moderator-2"
    assert event_types(api_client, public_id).count("MODERATOR_CHANGED") == 1


def test_event_history_is_ordered_and_uses_utc(api_client: TestClient) -> None:
    case = create_case(api_client)
    public_id = str(case["public_id"])
    api_client.post(f"/api/v1/cases/{public_id}/send-to-client")
    api_client.post(
        f"/api/v1/cases/{public_id}/reassign",
        json={"moderator_id": "moderator-2"},
    )

    response = api_client.get(f"/api/v1/cases/{public_id}/events")

    assert response.status_code == 200
    events = response.json()
    assert [event["event_type"] for event in events] == [
        "CASE_CREATED",
        "CLIENT_REQUEST_SENT",
        "MODERATOR_CHANGED",
    ]
    assert all(event["created_at"].endswith("Z") for event in events)
    assert events[-1]["metadata"] == {
        "old_moderator_id": "moderator-1",
        "new_moderator_id": "moderator-2",
    }
