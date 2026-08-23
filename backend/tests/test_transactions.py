import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ClientReplyRecord
from app.domain.enums import CaseEventType, CaseStatus
from app.services import CaseService


def test_event_failure_rolls_back_state_change(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = CaseService(db_session)
    case = service.create_case("Need an update")

    def fail_event_write(_event: object) -> None:
        raise RuntimeError("simulated event persistence failure")

    monkeypatch.setattr(service.repository, "add_event", fail_event_write)

    with pytest.raises(RuntimeError, match="event persistence"):
        service.mark_client_request_sent(case.public_id)

    db_session.expire_all()
    persisted = service.get_case(case.public_id)
    events = service.get_case_events(case.public_id)
    assert persisted.status is CaseStatus.NEW
    assert [event.event_type for event in events] == [CaseEventType.CASE_CREATED]


def test_reply_event_failure_rolls_back_reply_and_state(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = CaseService(db_session)
    case = service.create_case("Need an update")
    service.mark_client_request_sent(case.public_id)

    def fail_event_write(_event: object) -> None:
        raise RuntimeError("simulated reply event persistence failure")

    monkeypatch.setattr(service.repository, "add_event", fail_event_write)

    with pytest.raises(RuntimeError, match="reply event persistence"):
        service.record_client_reply(
            case.public_id,
            external_message_id="rolled-back-reply",
            text="Uncommitted detail",
        )

    db_session.expire_all()
    assert service.get_case(case.public_id).status is CaseStatus.WAITING_CLIENT
    assert (
        db_session.scalar(
            select(ClientReplyRecord).where(
                ClientReplyRecord.external_message_id == "rolled-back-reply"
            )
        )
        is None
    )
    assert CaseEventType.CLIENT_REPLY_RECEIVED not in {
        event.event_type for event in service.get_case_events(case.public_id)
    }


def test_reply_persistence_failure_rolls_back_pending_state_and_event(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = CaseService(db_session)
    case = service.create_case("Need an update")
    service.mark_client_request_sent(case.public_id)
    original_add_reply = service.repository.add_client_reply

    def add_then_fail(reply: ClientReplyRecord) -> None:
        original_add_reply(reply)
        raise RuntimeError("simulated reply persistence failure")

    monkeypatch.setattr(service.repository, "add_client_reply", add_then_fail)

    with pytest.raises(RuntimeError, match="reply persistence"):
        service.record_client_reply(
            case.public_id,
            external_message_id="failed-reply",
            text="Uncommitted detail",
        )

    db_session.expire_all()
    assert service.get_case(case.public_id).status is CaseStatus.WAITING_CLIENT
    assert (
        db_session.scalar(
            select(ClientReplyRecord).where(
                ClientReplyRecord.external_message_id == "failed-reply"
            )
        )
        is None
    )
    assert CaseEventType.CLIENT_REPLY_RECEIVED not in {
        event.event_type for event in service.get_case_events(case.public_id)
    }
