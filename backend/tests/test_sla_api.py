from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.dependencies import get_current_time, get_db_session
from app.db import create_database_engine, create_tables
from app.domain.enums import CaseEventType
from app.main import app
from app.services import CaseService

START = datetime(2026, 8, 23, 10, 0)
NOW = START.replace(tzinfo=UTC) + timedelta(hours=2)


@pytest.fixture
def sla_api(tmp_path: Path) -> Iterator[tuple[TestClient, str]]:
    database_path = tmp_path / "sla-api-test.sqlite"
    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    create_tables(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as session:
        cases = CaseService(session)
        case = cases.create_case("SLA API")
        cases.mark_client_request_sent(case.public_id, now=START)
        public_id = case.public_id

    def override_session() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    def override_time() -> datetime:
        return NOW

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_current_time] = override_time
    try:
        with TestClient(app) as client:
            yield client, public_id
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_due_and_ack_endpoints_are_deterministic_and_idempotent(
    sla_api: tuple[TestClient, str],
) -> None:
    client, public_id = sla_api

    first_due = client.get("/api/v1/actions/due")
    repeated_due = client.get("/api/v1/actions/due")
    expected = [
        {
            "case_public_id": public_id,
            "action_type": "REMIND_CLIENT",
            "level": 1,
            "recipient_role": "client",
            "due_at": "2026-08-23T12:00:00Z",
        }
    ]
    assert first_due.status_code == 200
    assert first_due.json() == expected
    assert repeated_due.json() == expected

    acknowledgement = client.post(
        f"/api/v1/actions/{public_id}/ack",
        json={"action_type": "REMIND_CLIENT", "level": 1},
    )
    repeated_acknowledgement = client.post(
        f"/api/v1/actions/{public_id}/ack",
        json={"action_type": "REMIND_CLIENT", "level": 1},
    )

    assert acknowledgement.status_code == 200
    assert acknowledgement.json()["already_processed"] is False
    assert repeated_acknowledgement.status_code == 200
    assert repeated_acknowledgement.json()["already_processed"] is True
    assert client.get("/api/v1/actions/due").json() == []
    events = client.get(f"/api/v1/cases/{public_id}/events").json()
    reminders = [
        event
        for event in events
        if event["event_type"] == CaseEventType.CLIENT_REMINDER_SENT.value
    ]
    assert len(reminders) == 1
    assert reminders[0]["metadata"] == {"level": 1}
