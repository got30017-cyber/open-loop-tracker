from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.dependencies import get_current_time, get_db_session
from app.db import create_database_engine, create_tables
from app.main import app
from app.services import CaseService

START = datetime(2026, 8, 23, 10, 0, tzinfo=UTC)


@pytest.fixture
def delivery_api(
    tmp_path: Path,
) -> Iterator[tuple[TestClient, str, dict[str, datetime]]]:
    database_path = tmp_path / "delivery-api-test.sqlite"
    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    create_tables(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as session:
        public_id = CaseService(session).create_case("Delivery API").public_id
    clock = {"now": START}

    def override_session() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    def override_time() -> datetime:
        return clock["now"]

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_current_time] = override_time
    try:
        with TestClient(app) as client:
            yield client, public_id, clock
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def start_payload(public_id: str, key: str = "client-request-key") -> dict[str, str]:
    return {
        "case_public_id": public_id,
        "delivery_type": "CLIENT_REQUEST",
        "recipient_id": "client-1",
        "idempotency_key": key,
    }


def test_start_attempt_endpoint_is_idempotent_and_uses_utc(
    delivery_api: tuple[TestClient, str, dict[str, datetime]],
) -> None:
    client, public_id, _clock = delivery_api
    payload = start_payload(public_id)

    first = client.post("/api/v1/deliveries/attempts", json=payload)
    repeated = client.post("/api/v1/deliveries/attempts", json=payload)

    assert first.status_code == 200
    assert first.json()["attempt_number"] == 1
    assert first.json()["status"] == "PENDING"
    assert first.json()["already_processed"] is False
    assert first.json()["created_at"].endswith("Z")
    assert repeated.status_code == 200
    assert repeated.json()["already_processed"] is True


def test_success_result_and_conflict_endpoints(
    delivery_api: tuple[TestClient, str, dict[str, datetime]],
) -> None:
    client, public_id, clock = delivery_api
    client.post("/api/v1/deliveries/attempts", json=start_payload(public_id))
    result_url = "/api/v1/deliveries/client-request-key/attempts/1/result"
    clock["now"] = START + timedelta(minutes=1)

    succeeded = client.post(
        result_url,
        json={"status": "SUCCEEDED", "external_message_id": "external-1"},
    )
    repeated = client.post(
        result_url,
        json={"status": "SUCCEEDED", "external_message_id": "external-1"},
    )
    conflict = client.post(
        result_url,
        json={"status": "FAILED", "error_message": "timeout"},
    )

    assert succeeded.status_code == 200
    assert succeeded.json()["status"] == "SUCCEEDED"
    assert succeeded.json()["completed_at"].endswith("Z")
    assert succeeded.json()["delivery_completed"] is True
    assert repeated.json()["already_processed"] is True
    assert conflict.status_code == 409
    assert conflict.json()["error"] == "delivery_attempt_conflict"


def test_failure_retry_not_due_and_retryable_query(
    delivery_api: tuple[TestClient, str, dict[str, datetime]],
) -> None:
    client, public_id, clock = delivery_api
    payload = start_payload(public_id)
    client.post("/api/v1/deliveries/attempts", json=payload)
    failed = client.post(
        "/api/v1/deliveries/client-request-key/attempts/1/result",
        json={"status": "FAILED", "error_message": "timeout"},
    )

    not_due = client.post("/api/v1/deliveries/attempts", json=payload)
    before_due = client.get("/api/v1/deliveries/retryable")
    clock["now"] = START + timedelta(minutes=5)
    first_query = client.get("/api/v1/deliveries/retryable")
    repeated_query = client.get("/api/v1/deliveries/retryable")
    retry = client.post("/api/v1/deliveries/attempts", json=payload)

    assert failed.status_code == 200
    assert failed.json()["status"] == "FAILED"
    assert failed.json()["error_message"] == "timeout"
    assert not_due.status_code == 409
    assert not_due.json()["error"] == "delivery_retry_not_due"
    assert not_due.json()["details"]["next_retry_at"].endswith("Z")
    assert before_due.json() == []
    assert first_query.status_code == 200
    assert first_query.json() == repeated_query.json()
    assert first_query.json()[0]["idempotency_key"] == "client-request-key"
    assert first_query.json()[0]["next_retry_at"].endswith("Z")
    assert retry.status_code == 200
    assert retry.json()["attempt_number"] == 2
    assert retry.json()["already_processed"] is False
    assert client.get("/api/v1/deliveries/retryable").json() == []
    events = client.get(f"/api/v1/cases/{public_id}/events").json()
    assert [event["event_type"] for event in events].count("DELIVERY_FAILED") == 1
    assert [event["event_type"] for event in events].count("DELIVERY_RETRIED") == 1


def test_identity_conflict_maps_to_stable_409(
    delivery_api: tuple[TestClient, str, dict[str, datetime]],
) -> None:
    client, public_id, _clock = delivery_api
    payload = start_payload(public_id)
    client.post("/api/v1/deliveries/attempts", json=payload)
    payload["recipient_id"] = "different-client"

    response = client.post("/api/v1/deliveries/attempts", json=payload)

    assert response.status_code == 409
    assert response.json()["error"] == "delivery_identity_conflict"


def test_retry_exhaustion_maps_to_stable_409(
    delivery_api: tuple[TestClient, str, dict[str, datetime]],
) -> None:
    client, public_id, clock = delivery_api
    payload = start_payload(public_id, "exhaust-key")

    for attempt_number in range(1, 4):
        started = client.post("/api/v1/deliveries/attempts", json=payload)
        assert started.json()["attempt_number"] == attempt_number
        client.post(
            f"/api/v1/deliveries/exhaust-key/attempts/{attempt_number}/result",
            json={"status": "FAILED", "error_message": "timeout"},
        )
        clock["now"] += timedelta(minutes=5)

    exhausted = client.post("/api/v1/deliveries/attempts", json=payload)

    assert exhausted.status_code == 409
    assert exhausted.json()["error"] == "delivery_retries_exhausted"
