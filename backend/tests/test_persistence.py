from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import (
    CaseEventRecord,
    CaseRecord,
    ClientReplyRecord,
    DeliveryAttemptRecord,
)
from app.domain.enums import CaseEventType, CaseStatus


def persist_case(
    session: Session, public_id: str | None = None
) -> CaseRecord:
    case = CaseRecord(
        public_id=public_id or f"CASE-{uuid4().hex}",
        status=CaseStatus.NEW,
        original_message="Where is my order?",
        moderator_id="moderator-1",
        client_contact_id="client-1",
    )
    session.add(case)
    session.commit()
    return case


def test_case_can_be_persisted_and_loaded(db_session: Session) -> None:
    case = persist_case(db_session)

    db_session.expire_all()
    loaded = db_session.scalar(
        select(CaseRecord).where(CaseRecord.public_id == case.public_id)
    )

    assert loaded is not None
    assert loaded.status is CaseStatus.NEW
    assert loaded.original_message == "Where is my order?"
    assert loaded.created_at is not None
    assert loaded.updated_at is not None


def test_public_id_is_unique_at_database_level(db_session: Session) -> None:
    persist_case(db_session, public_id="CASE-UNIQUE")
    db_session.add(
        CaseRecord(
            public_id="CASE-UNIQUE",
            status=CaseStatus.NEW,
            original_message="Another question",
        )
    )

    with pytest.raises(IntegrityError):
        db_session.commit()

    db_session.rollback()
    assert (
        db_session.scalar(
            select(CaseRecord).where(CaseRecord.public_id == "CASE-UNIQUE")
        )
        is not None
    )


def test_case_event_persists_for_correct_case(db_session: Session) -> None:
    case = persist_case(db_session)
    event = CaseEventRecord(
        case_id=case.id,
        event_type=CaseEventType.CASE_CREATED,
        actor_type="system",
    )
    db_session.add(event)
    db_session.commit()

    loaded = db_session.get(CaseEventRecord, event.id)

    assert loaded is not None
    assert loaded.case_id == case.id
    assert loaded.case.public_id == case.public_id


def test_multiple_client_replies_can_belong_to_one_case(
    db_session: Session,
) -> None:
    case = persist_case(db_session)
    db_session.add_all(
        [
            ClientReplyRecord(
                case_id=case.id,
                external_message_id="message-1",
                sender_id="client-1",
                text="First detail",
            ),
            ClientReplyRecord(
                case_id=case.id,
                external_message_id="message-2",
                sender_id="client-1",
                text="Second detail",
            ),
        ]
    )
    db_session.commit()

    replies = list(
        db_session.scalars(
            select(ClientReplyRecord).where(ClientReplyRecord.case_id == case.id)
        )
    )

    assert [reply.text for reply in replies] == ["First detail", "Second detail"]


def test_duplicate_external_message_id_is_rejected_by_database(
    db_session: Session,
) -> None:
    first_case = persist_case(db_session)
    second_case = persist_case(db_session)
    db_session.add(
        ClientReplyRecord(
            case_id=first_case.id,
            external_message_id="external-duplicate",
            text="First",
        )
    )
    db_session.commit()
    db_session.add(
        ClientReplyRecord(
            case_id=second_case.id,
            external_message_id="external-duplicate",
            text="Second",
        )
    )

    with pytest.raises(IntegrityError):
        db_session.commit()

    db_session.rollback()
    replies = list(
        db_session.scalars(
            select(ClientReplyRecord).where(
                ClientReplyRecord.external_message_id == "external-duplicate"
            )
        )
    )
    assert len(replies) == 1


def test_delivery_attempt_can_be_persisted(db_session: Session) -> None:
    case = persist_case(db_session)
    attempt = DeliveryAttemptRecord(
        case_id=case.id,
        delivery_type="client_request",
        recipient_id="client-1",
        idempotency_key="delivery-key",
        attempt_number=1,
        status="pending",
    )
    db_session.add(attempt)
    db_session.commit()

    loaded = db_session.get(DeliveryAttemptRecord, attempt.id)

    assert loaded is not None
    assert loaded.case_id == case.id
    assert loaded.idempotency_key == "delivery-key"
    assert loaded.attempt_number == 1
