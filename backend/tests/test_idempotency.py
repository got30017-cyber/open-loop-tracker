import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import CaseEventRecord, ClientReplyRecord
from app.domain.enums import CaseEventType, CaseStatus
from app.domain.state_machine import InvalidStateTransition
from app.services import CaseService, ExternalMessageIdConflictError


def event_count(
    session: Session, case_id: int, event_type: CaseEventType
) -> int:
    return session.scalar(
        select(func.count())
        .select_from(CaseEventRecord)
        .where(
            CaseEventRecord.case_id == case_id,
            CaseEventRecord.event_type == event_type,
        )
    ) or 0


def test_send_to_client_retry_is_idempotent(db_session: Session) -> None:
    service = CaseService(db_session)
    case = service.create_case("Question")

    first = service.mark_client_request_sent(case.public_id)
    second = service.mark_client_request_sent(case.public_id)

    assert first.already_processed is False
    assert second.already_processed is True
    assert second.case.status is CaseStatus.WAITING_CLIENT
    assert event_count(
        db_session, case.id, CaseEventType.CLIENT_REQUEST_SENT
    ) == 1


def test_same_case_client_reply_retry_is_idempotent(db_session: Session) -> None:
    service = CaseService(db_session)
    case = service.create_case("Question")
    service.mark_client_request_sent(case.public_id)

    first = service.record_client_reply(
        case.public_id, "same-message", "First delivery", "client-1"
    )
    second = service.record_client_reply(
        case.public_id, "same-message", "Retried delivery", "client-1"
    )

    replies = list(
        db_session.scalars(
            select(ClientReplyRecord).where(ClientReplyRecord.case_id == case.id)
        )
    )
    assert first.already_processed is False
    assert second.already_processed is True
    assert second.case.status is CaseStatus.WAITING_MODERATOR
    assert len(replies) == 1
    assert event_count(
        db_session, case.id, CaseEventType.CLIENT_REPLY_RECEIVED
    ) == 1


def test_cross_case_external_message_id_is_conflict(db_session: Session) -> None:
    service = CaseService(db_session)
    first_case = service.create_case("First")
    second_case = service.create_case("Second")
    service.mark_client_request_sent(first_case.public_id)
    service.mark_client_request_sent(second_case.public_id)
    service.record_client_reply(first_case.public_id, "cross-case", "Reply")

    with pytest.raises(ExternalMessageIdConflictError):
        service.record_client_reply(second_case.public_id, "cross-case", "Reply")

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
    assert event_count(
        db_session, second_case.id, CaseEventType.CLIENT_REPLY_RECEIVED
    ) == 0


def test_user_answered_retry_is_idempotent(db_session: Session) -> None:
    service = CaseService(db_session)
    case = service.create_case("Question")
    service.mark_client_request_sent(case.public_id)
    service.record_client_reply(case.public_id, "close-reply", "Reply")

    first = service.confirm_user_answered(case.public_id)
    second = service.confirm_user_answered(case.public_id)

    assert first.already_processed is False
    assert second.already_processed is True
    assert second.case.status is CaseStatus.CLOSED
    assert event_count(
        db_session, case.id, CaseEventType.USER_ANSWER_CONFIRMED
    ) == 1
    assert event_count(db_session, case.id, CaseEventType.CASE_CLOSED) == 1


def test_cancel_retry_is_idempotent(db_session: Session) -> None:
    service = CaseService(db_session)
    case = service.create_case("Question")

    first = service.cancel_case(case.public_id, "No longer needed")
    second = service.cancel_case(case.public_id, "Retried reason")

    assert first.already_processed is False
    assert second.already_processed is True
    assert second.case.status is CaseStatus.CANCELLED
    assert second.case.cancellation_reason == "No longer needed"
    assert event_count(db_session, case.id, CaseEventType.CASE_CANCELLED) == 1


def test_cancelling_closed_case_is_not_idempotent_success(
    db_session: Session,
) -> None:
    service = CaseService(db_session)
    case = service.create_case("Question")
    service.mark_client_request_sent(case.public_id)
    service.record_client_reply(case.public_id, "closed-cancel", "Reply")
    service.confirm_user_answered(case.public_id)

    with pytest.raises(InvalidStateTransition):
        service.cancel_case(case.public_id, "Too late")

    assert service.get_case(case.public_id).status is CaseStatus.CLOSED
    assert event_count(db_session, case.id, CaseEventType.CASE_CANCELLED) == 0


def test_same_moderator_reassignment_is_idempotent(db_session: Session) -> None:
    service = CaseService(db_session)
    case = service.create_case("Question", moderator_id="moderator-1")

    first = service.reassign_moderator(case.public_id, "moderator-2")
    second = service.reassign_moderator(case.public_id, "moderator-2")

    assert first.already_processed is False
    assert second.already_processed is True
    assert second.case.moderator_id == "moderator-2"
    assert event_count(
        db_session, case.id, CaseEventType.MODERATOR_CHANGED
    ) == 1


def test_unique_collision_is_reclassified_after_rollback(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = CaseService(db_session)
    first_case = service.create_case("First")
    second_case = service.create_case("Second")
    service.mark_client_request_sent(first_case.public_id)
    service.mark_client_request_sent(second_case.public_id)
    service.record_client_reply(first_case.public_id, "race-message", "Winner")
    original_lookup = service.repository.get_client_reply
    lookup_count = 0

    def miss_before_insert(external_message_id: str) -> ClientReplyRecord | None:
        nonlocal lookup_count
        lookup_count += 1
        if lookup_count == 1:
            return None
        return original_lookup(external_message_id)

    monkeypatch.setattr(
        service.repository, "get_client_reply", miss_before_insert
    )

    with pytest.raises(ExternalMessageIdConflictError):
        service.record_client_reply(
            second_case.public_id, "race-message", "Losing insert"
        )

    db_session.expire_all()
    replies = list(
        db_session.scalars(
            select(ClientReplyRecord).where(
                ClientReplyRecord.external_message_id == "race-message"
            )
        )
    )
    assert lookup_count == 2
    assert len(replies) == 1
    assert replies[0].case_id == first_case.id
    assert service.get_case(second_case.public_id).status is CaseStatus.WAITING_CLIENT
    assert event_count(
        db_session, second_case.id, CaseEventType.CLIENT_REPLY_RECEIVED
    ) == 0
