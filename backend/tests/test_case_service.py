import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import ClientReplyRecord
from app.domain.enums import CaseEventType, CaseStatus
from app.domain.state_machine import InvalidStateTransition
from app.services import CaseService, DuplicateClientReplyError


def test_create_case_persists_new_case_and_one_created_event(
    db_session: Session,
) -> None:
    service = CaseService(db_session)

    case = service.create_case(
        original_message="Need an update",
        moderator_id="moderator-1",
        client_contact_id="client-1",
        original_message_reference="message-100",
        end_user_reference="user-100",
    )

    loaded = service.get_case(case.public_id)
    events = service.get_case_events(case.public_id)
    assert loaded.status is CaseStatus.NEW
    assert loaded.public_id.startswith("CASE-")
    assert loaded.original_message_reference == "message-100"
    assert loaded.end_user_reference == "user-100"
    assert [event.event_type for event in events] == [CaseEventType.CASE_CREATED]


def test_mark_client_request_sent_transitions_and_writes_event(
    db_session: Session,
) -> None:
    service = CaseService(db_session)
    case = service.create_case("Need an update")

    updated = service.mark_client_request_sent(case.public_id)

    assert updated.status is CaseStatus.WAITING_CLIENT
    assert [event.event_type for event in service.get_case_events(case.public_id)] == [
        CaseEventType.CASE_CREATED,
        CaseEventType.CLIENT_REQUEST_SENT,
    ]


def test_invalid_mark_request_does_not_mutate_persisted_state(
    db_session: Session,
) -> None:
    service = CaseService(db_session)
    case = service.create_case("Need an update")
    service.mark_client_request_sent(case.public_id)
    service.record_client_reply(case.public_id, "invalid-mark-reply", "Details")

    with pytest.raises(InvalidStateTransition):
        service.mark_client_request_sent(case.public_id)

    db_session.expire_all()
    assert service.get_case(case.public_id).status is CaseStatus.WAITING_MODERATOR
    assert [event.event_type for event in service.get_case_events(case.public_id)] == [
        CaseEventType.CASE_CREATED,
        CaseEventType.CLIENT_REQUEST_SENT,
        CaseEventType.CLIENT_REPLY_RECEIVED,
    ]


def test_record_client_reply_persists_reply_state_and_event_atomically(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = CaseService(db_session)
    case = service.create_case("Need an update")
    service.mark_client_request_sent(case.public_id)
    commit_count = 0
    original_commit = db_session.commit

    def counted_commit() -> None:
        nonlocal commit_count
        commit_count += 1
        original_commit()

    monkeypatch.setattr(db_session, "commit", counted_commit)

    updated = service.record_client_reply(
        case.public_id,
        external_message_id="client-message-1",
        sender_id="client-1",
        text="The requested detail",
    )

    reply = db_session.scalar(
        select(ClientReplyRecord).where(
            ClientReplyRecord.external_message_id == "client-message-1"
        )
    )
    assert commit_count == 1
    assert updated.status is CaseStatus.WAITING_MODERATOR
    assert reply is not None
    assert reply.case_id == case.id
    assert CaseEventType.CLIENT_REPLY_RECEIVED in {
        event.event_type for event in service.get_case_events(case.public_id)
    }


def test_duplicate_client_reply_has_clear_service_error_and_rolls_back(
    db_session: Session,
) -> None:
    service = CaseService(db_session)
    first_case = service.create_case("First")
    second_case = service.create_case("Second")
    service.mark_client_request_sent(first_case.public_id)
    service.mark_client_request_sent(second_case.public_id)
    service.record_client_reply(
        first_case.public_id, "duplicate-external-id", "First reply"
    )

    with pytest.raises(DuplicateClientReplyError):
        service.record_client_reply(
            second_case.public_id, "duplicate-external-id", "Duplicate reply"
        )

    db_session.expire_all()
    assert service.get_case(second_case.public_id).status is CaseStatus.WAITING_CLIENT
    assert (
        db_session.scalar(
            select(ClientReplyRecord).where(
                ClientReplyRecord.case_id == second_case.id
            )
        )
        is None
    )
    assert CaseEventType.CLIENT_REPLY_RECEIVED not in {
        event.event_type for event in service.get_case_events(second_case.public_id)
    }


def test_unrelated_integrity_error_is_preserved_and_rolls_back(
    db_session: Session,
) -> None:
    service = CaseService(db_session)
    case = service.create_case("Need an update")
    service.mark_client_request_sent(case.public_id)

    with pytest.raises(IntegrityError):
        service.record_client_reply(
            case.public_id,
            external_message_id="not-null-failure",
            # Deliberately bypass the type contract to hit the DB constraint.
            text=None,  # type: ignore[arg-type]
        )

    db_session.expire_all()
    assert service.get_case(case.public_id).status is CaseStatus.WAITING_CLIENT
    assert (
        db_session.scalar(
            select(ClientReplyRecord).where(
                ClientReplyRecord.external_message_id == "not-null-failure"
            )
        )
        is None
    )
    assert CaseEventType.CLIENT_REPLY_RECEIVED not in {
        event.event_type for event in service.get_case_events(case.public_id)
    }


def test_confirm_user_answered_closes_case_and_records_distinct_events(
    db_session: Session,
) -> None:
    service = CaseService(db_session)
    case = service.create_case("Need an update")
    service.mark_client_request_sent(case.public_id)
    service.record_client_reply(case.public_id, "reply-to-close", "Details")

    closed = service.confirm_user_answered(case.public_id)

    close_events = [
        event.event_type
        for event in service.get_case_events(case.public_id)
        if event.event_type
        in {CaseEventType.USER_ANSWER_CONFIRMED, CaseEventType.CASE_CLOSED}
    ]
    assert closed.status is CaseStatus.CLOSED
    assert closed.closed_at is not None
    assert close_events == [
        CaseEventType.USER_ANSWER_CONFIRMED,
        CaseEventType.CASE_CLOSED,
    ]


def test_cancel_case_persists_reason_timestamp_and_event(
    db_session: Session,
) -> None:
    service = CaseService(db_session)
    case = service.create_case("No longer needed")
    service.mark_client_request_sent(case.public_id)

    cancelled = service.cancel_case(case.public_id, "Resolved elsewhere")

    assert cancelled.status is CaseStatus.CANCELLED
    assert cancelled.cancelled_at is not None
    assert cancelled.cancellation_reason == "Resolved elsewhere"
    assert CaseEventType.CASE_CANCELLED in {
        event.event_type for event in service.get_case_events(case.public_id)
    }


def test_reassign_moderator_preserves_state_and_records_metadata(
    db_session: Session,
) -> None:
    service = CaseService(db_session)
    case = service.create_case("Need an update", moderator_id="moderator-old")

    reassigned = service.reassign_moderator(case.public_id, "moderator-new")

    event = service.get_case_events(case.public_id)[-1]
    assert reassigned.status is CaseStatus.NEW
    assert reassigned.moderator_id == "moderator-new"
    assert event.event_type is CaseEventType.MODERATOR_CHANGED
    assert event.metadata_json == {
        "old_moderator_id": "moderator-old",
        "new_moderator_id": "moderator-new",
    }
