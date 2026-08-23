from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import CaseEventRecord, DeliveryAttemptRecord
from app.domain.delivery import DeliveryStatus, DeliveryType
from app.domain.enums import CaseEventType, CaseStatus
from app.services import (
    CaseService,
    DeliveryAttemptConflictError,
    DeliveryIdentityConflictError,
    DeliveryRetriesExhaustedError,
    DeliveryService,
    RetryNotDueError,
)

START = datetime(2026, 8, 23, 10, 0)


def settings(**overrides: int) -> Settings:
    values = {
        "delivery_max_attempts": 3,
        "delivery_retry_delay_minutes": 5,
    }
    values.update(overrides)
    return Settings(
        app_name="test",
        environment="test",
        database_url="sqlite://",
        **values,
    )


def delivery_setup(
    db_session: Session,
    *,
    configured: Settings | None = None,
    message: str = "Question",
) -> tuple[CaseService, DeliveryService, str]:
    cases = CaseService(db_session, settings=configured)
    case = cases.create_case(
        message,
        moderator_id='moderator-1',
        client_contact_id='client-1',
    )
    return cases, DeliveryService(db_session, settings=configured), case.public_id


def start(
    service: DeliveryService,
    public_id: str,
    *,
    key: str = "delivery-key",
    delivery_type: DeliveryType = DeliveryType.CLIENT_REQUEST,
    recipient_id: str = "client-1",
    allow_retry: bool = True,
    now: datetime = START,
):
    return service.start_delivery_attempt(
        case_public_id=public_id,
        delivery_type=delivery_type,
        recipient_id=recipient_id,
        idempotency_key=key,
        allow_retry=allow_retry,
        now=now,
    )


def complete(
    service: DeliveryService,
    *,
    key: str = "delivery-key",
    attempt_number: int = 1,
    outcome: DeliveryStatus,
    now: datetime,
    external_message_id: str | None = None,
    error_message: str | None = None,
):
    return service.complete_delivery_attempt(
        idempotency_key=key,
        attempt_number=attempt_number,
        outcome=outcome,
        external_message_id=external_message_id,
        error_message=error_message,
        now=now,
    )


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


def test_first_start_and_pending_retry_are_idempotent(
    db_session: Session,
) -> None:
    _cases, service, public_id = delivery_setup(db_session)

    first = start(service, public_id)
    repeated = start(service, public_id)
    attempts = service.delivery_repository.get_attempts("delivery-key")

    assert first.already_processed is False
    assert first.attempt.attempt_number == 1
    assert first.attempt.status == DeliveryStatus.PENDING.value
    assert first.attempt.completed_at is None
    assert repeated.already_processed is True
    assert repeated.attempt.id == first.attempt.id
    assert len(attempts) == 1


def test_success_completion_and_retry_are_idempotent(
    db_session: Session,
) -> None:
    cases, service, public_id = delivery_setup(db_session)
    start(service, public_id)
    completed_at = START + timedelta(minutes=1)

    first = complete(
        service,
        outcome=DeliveryStatus.SUCCEEDED,
        external_message_id="external-1",
        now=completed_at,
    )
    repeated = complete(
        service,
        outcome=DeliveryStatus.SUCCEEDED,
        external_message_id="external-1",
        now=START + timedelta(minutes=2),
    )

    assert first.already_processed is False
    assert first.delivery_completed is True
    assert first.attempt.completed_at == completed_at
    assert first.attempt.external_message_id == "external-1"
    assert first.attempt.error_message is None
    assert repeated.already_processed is True
    assert repeated.attempt.completed_at == completed_at
    assert cases.get_case(public_id).status is CaseStatus.WAITING_CLIENT
    assert event_count(
        db_session,
        first.attempt.case_id,
        CaseEventType.CLIENT_REQUEST_SENT,
    ) == 1


def test_failure_completion_and_retry_write_one_event(
    db_session: Session,
) -> None:
    cases, service, public_id = delivery_setup(db_session)
    attempt = start(service, public_id).attempt
    completed_at = START + timedelta(minutes=1)

    first = complete(
        service,
        outcome=DeliveryStatus.FAILED,
        error_message="upstream timeout",
        now=completed_at,
    )
    repeated = complete(
        service,
        outcome=DeliveryStatus.FAILED,
        error_message="upstream timeout",
        now=START + timedelta(minutes=2),
    )
    events = cases.get_case_events(public_id)
    failed_events = [
        event for event in events if event.event_type is CaseEventType.DELIVERY_FAILED
    ]

    assert first.already_processed is False
    assert first.attempt.completed_at == completed_at
    assert first.attempt.error_message == "upstream timeout"
    assert first.attempt.external_message_id is None
    assert repeated.already_processed is True
    assert repeated.attempt.completed_at == completed_at
    assert len(failed_events) == 1
    assert failed_events[0].metadata_json == {
        "delivery_type": "CLIENT_REQUEST",
        "attempt_number": 1,
        "idempotency_key": "delivery-key",
    }
    assert failed_events[0].deduplication_key == (
        "delivery:delivery-key:attempt:1:failed"
    )
    assert event_count(db_session, attempt.case_id, CaseEventType.DELIVERY_FAILED) == 1


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (DeliveryStatus.SUCCEEDED, DeliveryStatus.FAILED),
        (DeliveryStatus.FAILED, DeliveryStatus.SUCCEEDED),
    ],
)
def test_conflicting_terminal_result_is_rejected(
    db_session: Session,
    first: DeliveryStatus,
    second: DeliveryStatus,
) -> None:
    _cases, service, public_id = delivery_setup(db_session)
    start(service, public_id)
    complete(service, outcome=first, now=START + timedelta(minutes=1))

    with pytest.raises(DeliveryAttemptConflictError):
        complete(service, outcome=second, now=START + timedelta(minutes=2))


def test_different_external_message_id_on_success_retry_is_conflict(
    db_session: Session,
) -> None:
    _cases, service, public_id = delivery_setup(db_session)
    start(service, public_id)
    complete(
        service,
        outcome=DeliveryStatus.SUCCEEDED,
        external_message_id="external-1",
        now=START,
    )

    with pytest.raises(DeliveryAttemptConflictError):
        complete(
            service,
            outcome=DeliveryStatus.SUCCEEDED,
            external_message_id="external-2",
            now=START,
        )


def install_competing_completion(
    *,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    service: DeliveryService,
    winner: DeliveryStatus,
    completed_at: datetime,
    external_message_id: str | None = None,
    error_message: str | None = None,
) -> None:
    original_transition = service.delivery_repository.transition_pending_attempt
    attempt = service.delivery_repository.get_attempt("delivery-key", 1)
    assert attempt is not None

    def winner_commits_before_compare_and_set(**_requested: object) -> bool:
        transitioned = original_transition(
            idempotency_key="delivery-key",
            attempt_number=1,
            status=winner,
            completed_at=completed_at,
            external_message_id=(
                external_message_id
                if winner is DeliveryStatus.SUCCEEDED
                else None
            ),
            error_message=(
                error_message if winner is DeliveryStatus.FAILED else None
            ),
        )
        assert transitioned is True
        losing_transition = original_transition(  # type: ignore[arg-type]
            **_requested
        )
        assert losing_transition is False
        if winner is DeliveryStatus.SUCCEEDED:
            db_session.refresh(attempt)
            service._apply_successful_delivery_effect(attempt, completed_at)
        else:
            service.case_repository.add_event(
                service._delivery_event(
                    case_id=attempt.case_id,
                    event_type=CaseEventType.DELIVERY_FAILED,
                    delivery_type=DeliveryType.CLIENT_REQUEST,
                    idempotency_key="delivery-key",
                    attempt_number=1,
                    created_at=completed_at,
                    event_suffix="failed",
                )
            )
        db_session.commit()
        db_session.expire_all()
        return losing_transition

    monkeypatch.setattr(
        service.delivery_repository,
        "transition_pending_attempt",
        winner_commits_before_compare_and_set,
    )


def test_competing_equivalent_success_is_idempotent_without_timestamp_rewrite(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cases, service, public_id = delivery_setup(db_session)
    start(service, public_id)
    winning_time = START + timedelta(minutes=1)
    install_competing_completion(
        db_session=db_session,
        monkeypatch=monkeypatch,
        service=service,
        winner=DeliveryStatus.SUCCEEDED,
        completed_at=winning_time,
        external_message_id="external-1",
    )

    result = complete(
        service,
        outcome=DeliveryStatus.SUCCEEDED,
        external_message_id="external-1",
        now=START + timedelta(minutes=2),
    )

    assert result.already_processed is True
    assert result.attempt.status == DeliveryStatus.SUCCEEDED.value
    assert result.attempt.completed_at == winning_time
    assert result.attempt.external_message_id == "external-1"


def test_success_winner_is_not_overwritten_by_competing_failure(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    cases, service, public_id = delivery_setup(db_session)
    attempt = start(service, public_id).attempt
    winning_time = START + timedelta(minutes=1)
    install_competing_completion(
        db_session=db_session,
        monkeypatch=monkeypatch,
        service=service,
        winner=DeliveryStatus.SUCCEEDED,
        completed_at=winning_time,
        external_message_id="external-1",
    )

    with pytest.raises(DeliveryAttemptConflictError):
        complete(
            service,
            outcome=DeliveryStatus.FAILED,
            error_message="timeout",
            now=START + timedelta(minutes=2),
        )

    persisted = service.delivery_repository.get_attempt("delivery-key", 1)
    assert persisted is not None
    assert persisted.status == DeliveryStatus.SUCCEEDED.value
    assert persisted.completed_at == winning_time
    assert event_count(
        db_session, attempt.case_id, CaseEventType.DELIVERY_FAILED
    ) == 0
    assert cases.get_case(public_id).status is CaseStatus.WAITING_CLIENT


def test_failure_winner_is_immutable_and_writes_one_failure_event(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cases, service, public_id = delivery_setup(db_session)
    attempt = start(service, public_id).attempt
    winning_time = START + timedelta(minutes=1)
    install_competing_completion(
        db_session=db_session,
        monkeypatch=monkeypatch,
        service=service,
        winner=DeliveryStatus.FAILED,
        completed_at=winning_time,
        error_message="winner timeout",
    )

    with pytest.raises(DeliveryAttemptConflictError):
        complete(
            service,
            outcome=DeliveryStatus.SUCCEEDED,
            external_message_id="external-loser",
            now=START + timedelta(minutes=2),
        )

    persisted = service.delivery_repository.get_attempt("delivery-key", 1)
    assert persisted is not None
    assert persisted.status == DeliveryStatus.FAILED.value
    assert persisted.completed_at == winning_time
    assert persisted.error_message == "winner timeout"
    assert event_count(
        db_session, attempt.case_id, CaseEventType.DELIVERY_FAILED
    ) == 1


def test_competing_equivalent_failure_is_idempotent_with_one_event(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cases, service, public_id = delivery_setup(db_session)
    attempt = start(service, public_id).attempt
    winning_time = START + timedelta(minutes=1)
    install_competing_completion(
        db_session=db_session,
        monkeypatch=monkeypatch,
        service=service,
        winner=DeliveryStatus.FAILED,
        completed_at=winning_time,
        error_message="timeout",
    )

    result = complete(
        service,
        outcome=DeliveryStatus.FAILED,
        error_message="timeout",
        now=START + timedelta(minutes=2),
    )

    assert result.already_processed is True
    assert result.attempt.completed_at == winning_time
    assert event_count(
        db_session, attempt.case_id, CaseEventType.DELIVERY_FAILED
    ) == 1


def test_competing_success_with_different_external_id_is_conflict(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cases, service, public_id = delivery_setup(db_session)
    start(service, public_id)
    install_competing_completion(
        db_session=db_session,
        monkeypatch=monkeypatch,
        service=service,
        winner=DeliveryStatus.SUCCEEDED,
        completed_at=START + timedelta(minutes=1),
        external_message_id="winner-external-id",
    )

    with pytest.raises(DeliveryAttemptConflictError):
        complete(
            service,
            outcome=DeliveryStatus.SUCCEEDED,
            external_message_id="loser-external-id",
            now=START + timedelta(minutes=2),
        )

    persisted = service.delivery_repository.get_attempt("delivery-key", 1)
    assert persisted is not None
    assert persisted.external_message_id == "winner-external-id"


def test_retry_timing_reservation_and_event_are_idempotent(
    db_session: Session,
) -> None:
    cases, service, public_id = delivery_setup(db_session)
    first = start(service, public_id).attempt
    failed_at = START + timedelta(minutes=1)
    complete(
        service,
        outcome=DeliveryStatus.FAILED,
        error_message="timeout",
        now=failed_at,
    )

    with pytest.raises(RetryNotDueError) as error:
        start(service, public_id, now=failed_at + timedelta(minutes=4))
    assert error.value.next_retry_at == failed_at + timedelta(minutes=5)

    retry = start(service, public_id, now=failed_at + timedelta(minutes=5))
    repeated = start(service, public_id, now=failed_at + timedelta(minutes=6))
    attempts = service.delivery_repository.get_attempts("delivery-key")
    retried_events = [
        event
        for event in cases.get_case_events(public_id)
        if event.event_type is CaseEventType.DELIVERY_RETRIED
    ]

    assert retry.already_processed is False
    assert retry.attempt.attempt_number == 2
    assert retry.attempt.id != first.id
    assert repeated.already_processed is True
    assert repeated.attempt.id == retry.attempt.id
    assert len(attempts) == 2
    assert len(retried_events) == 1
    assert retried_events[0].metadata_json == {
        "delivery_type": "CLIENT_REQUEST",
        "attempt_number": 2,
        "idempotency_key": "delivery-key",
    }


def test_max_attempts_are_enforced(db_session: Session) -> None:
    configured = settings(delivery_max_attempts=3, delivery_retry_delay_minutes=0)
    _cases, service, public_id = delivery_setup(db_session, configured=configured)

    for attempt_number in range(1, 4):
        attempt = start(
            service,
            public_id,
            now=START + timedelta(minutes=attempt_number),
        ).attempt
        assert attempt.attempt_number == attempt_number
        complete(
            service,
            attempt_number=attempt_number,
            outcome=DeliveryStatus.FAILED,
            now=START + timedelta(minutes=attempt_number),
        )

    with pytest.raises(DeliveryRetriesExhaustedError):
        start(service, public_id, now=START + timedelta(minutes=4))


def test_success_stops_new_attempts(db_session: Session) -> None:
    _cases, service, public_id = delivery_setup(db_session)
    first = start(service, public_id).attempt
    complete(service, outcome=DeliveryStatus.SUCCEEDED, now=START)

    result = start(service, public_id, now=START + timedelta(days=1))

    assert result.already_processed is True
    assert result.delivery_completed is True
    assert result.attempt.id == first.id
    assert len(service.delivery_repository.get_attempts("delivery-key")) == 1


def test_retry_can_be_disabled_for_watchdog_orchestration(
    db_session: Session,
) -> None:
    _cases, service, public_id = delivery_setup(
        db_session, configured=settings(delivery_retry_delay_minutes=0)
    )
    first = start(service, public_id).attempt
    complete(
        service,
        outcome=DeliveryStatus.FAILED,
        error_message="transport failed",
        now=START,
    )

    repeated = start(
        service,
        public_id,
        allow_retry=False,
        now=START + timedelta(days=1),
    )

    assert repeated.already_processed is True
    assert repeated.attempt.id == first.id
    assert repeated.attempt.status == DeliveryStatus.FAILED.value
    assert len(service.delivery_repository.get_attempts("delivery-key")) == 1


@pytest.mark.parametrize("conflict", ["case", "type", "recipient"])
def test_idempotency_key_identity_conflicts(
    db_session: Session, conflict: str
) -> None:
    cases, service, public_id = delivery_setup(db_session)
    start(service, public_id)
    requested_case = public_id
    requested_type = DeliveryType.CLIENT_REQUEST
    requested_recipient = "client-1"
    if conflict == "case":
        requested_case = cases.create_case("Another case").public_id
    elif conflict == "type":
        requested_type = DeliveryType.ESCALATION
    else:
        requested_recipient = "client-2"

    with pytest.raises(DeliveryIdentityConflictError):
        start(
            service,
            requested_case,
            delivery_type=requested_type,
            recipient_id=requested_recipient,
        )


def test_retry_attempt_unique_collision_recovers_winner(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    cases, service, public_id = delivery_setup(
        db_session, configured=settings(delivery_retry_delay_minutes=0)
    )
    first = start(service, public_id).attempt
    complete(service, outcome=DeliveryStatus.FAILED, now=START)
    winner = DeliveryAttemptRecord(
        case_id=first.case_id,
        delivery_type=DeliveryType.CLIENT_REQUEST.value,
        recipient_id="client-1",
        idempotency_key="delivery-key",
        attempt_number=2,
        status=DeliveryStatus.PENDING.value,
        created_at=START,
    )
    service.delivery_repository.add_attempt(winner)
    service.case_repository.add_event(
        CaseEventRecord(
            case_id=first.case_id,
            event_type=CaseEventType.DELIVERY_RETRIED,
            metadata_json={
                "delivery_type": "CLIENT_REQUEST",
                "attempt_number": 2,
                "idempotency_key": "delivery-key",
            },
            deduplication_key="delivery:delivery-key:attempt:2:retried",
        )
    )
    db_session.commit()
    original_lookup = service.delivery_repository.get_attempts
    lookup_count = 0
    rollback_count = 0
    original_rollback = db_session.rollback

    def stale_lookup(key: str) -> list[DeliveryAttemptRecord]:
        nonlocal lookup_count
        lookup_count += 1
        if lookup_count == 1:
            return [first]
        return original_lookup(key)

    def counted_rollback() -> None:
        nonlocal rollback_count
        rollback_count += 1
        original_rollback()

    monkeypatch.setattr(service.delivery_repository, "get_attempts", stale_lookup)
    monkeypatch.setattr(db_session, "rollback", counted_rollback)

    result = start(service, public_id, now=START)

    assert result.already_processed is True
    assert result.attempt.id == winner.id
    assert rollback_count == 1
    assert len(original_lookup("delivery-key")) == 2
    assert event_count(
        db_session, first.case_id, CaseEventType.DELIVERY_RETRIED
    ) == 1


def test_unrelated_start_integrity_error_is_preserved(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cases, service, public_id = delivery_setup(db_session)
    original_add = service.delivery_repository.add_attempt

    def add_invalid(attempt: DeliveryAttemptRecord) -> None:
        attempt.recipient_id = None  # type: ignore[assignment]
        original_add(attempt)

    monkeypatch.setattr(service.delivery_repository, "add_attempt", add_invalid)

    with pytest.raises(IntegrityError):
        start(service, public_id)

    assert service.delivery_repository.get_attempts("delivery-key") == []


def test_failure_event_error_rolls_back_attempt_status(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cases, service, public_id = delivery_setup(db_session)
    start(service, public_id)

    def fail_event(_event: CaseEventRecord) -> None:
        raise RuntimeError("simulated failed-event persistence error")

    monkeypatch.setattr(service.case_repository, "add_event", fail_event)
    with pytest.raises(RuntimeError, match="failed-event"):
        complete(service, outcome=DeliveryStatus.FAILED, now=START)

    db_session.expire_all()
    attempt = service.delivery_repository.get_attempt("delivery-key", 1)
    assert attempt is not None
    assert attempt.status == DeliveryStatus.PENDING.value
    assert attempt.completed_at is None


def test_retry_event_error_rolls_back_new_attempt(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cases, service, public_id = delivery_setup(
        db_session, configured=settings(delivery_retry_delay_minutes=0)
    )
    start(service, public_id)
    complete(service, outcome=DeliveryStatus.FAILED, now=START)

    def fail_event(_event: CaseEventRecord) -> None:
        raise RuntimeError("simulated retry-event persistence error")

    monkeypatch.setattr(service.case_repository, "add_event", fail_event)
    with pytest.raises(RuntimeError, match="retry-event"):
        start(service, public_id, now=START)

    db_session.expire_all()
    assert len(service.delivery_repository.get_attempts("delivery-key")) == 1


def test_retryable_query_filters_orders_and_has_no_side_effects(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    cases, service, public_id = delivery_setup(db_session)
    now = START + timedelta(hours=1)
    for key, status, completed_at, attempt_number in (
        ("a-pending", DeliveryStatus.PENDING, None, 1),
        ("b-succeeded", DeliveryStatus.SUCCEEDED, now, 1),
        ("c-too-early", DeliveryStatus.FAILED, now - timedelta(minutes=4), 1),
        ("d-due", DeliveryStatus.FAILED, now - timedelta(minutes=5), 1),
        ("e-exhausted", DeliveryStatus.FAILED, now - timedelta(minutes=10), 3),
        ("f-due", DeliveryStatus.FAILED, now - timedelta(minutes=6), 2),
    ):
        db_session.add(
            DeliveryAttemptRecord(
                case_id=cases.get_case(public_id).id,
                delivery_type=DeliveryType.CLIENT_REQUEST.value,
                recipient_id="client-1",
                idempotency_key=key,
                attempt_number=attempt_number,
                status=status.value,
                created_at=START,
                completed_at=completed_at,
            )
        )
    db_session.commit()
    initial_attempts = db_session.scalar(
        select(func.count()).select_from(DeliveryAttemptRecord)
    )
    initial_events = len(cases.get_case_events(public_id))
    commit_count = 0

    def counted_commit() -> None:
        nonlocal commit_count
        commit_count += 1

    monkeypatch.setattr(db_session, "commit", counted_commit)

    first = service.get_retryable_deliveries(now)
    repeated = service.get_retryable_deliveries(now)

    assert [item.idempotency_key for item in first] == ["d-due", "f-due"]
    assert first == repeated
    assert first[0].next_retry_at == now
    assert commit_count == 0
    assert db_session.scalar(
        select(func.count()).select_from(DeliveryAttemptRecord)
    ) == initial_attempts
    assert len(cases.get_case_events(public_id)) == initial_events


@pytest.mark.parametrize(
    ("delivery_type", "key_template", "action_type", "level"),
    [
        (DeliveryType.CLIENT_REMINDER, "client-reminder:{case}:1", "REMIND_CLIENT", 1),
        (DeliveryType.MODERATOR_REMINDER, "moderator-reminder:{case}:2", "REMIND_MODERATOR", 2),
        (DeliveryType.ESCALATION, "escalation:{case}:client", "ESCALATE_CLIENT_WAIT", None),
        (DeliveryType.ESCALATION, "escalation:{case}:moderator", "ESCALATE_MODERATOR_WAIT", None),
    ],
)
def test_retryable_sla_delivery_exposes_authoritative_ack_identity(
    db_session: Session,
    delivery_type: DeliveryType,
    key_template: str,
    action_type: str,
    level: int | None,
) -> None:
    cases, service, public_id = delivery_setup(db_session)
    db_session.add(
        DeliveryAttemptRecord(
            case_id=cases.get_case(public_id).id,
            delivery_type=delivery_type.value,
            recipient_id="recipient-1",
            idempotency_key=key_template.format(case=public_id),
            attempt_number=1,
            status=DeliveryStatus.FAILED.value,
            created_at=START,
            completed_at=START,
        )
    )
    db_session.commit()

    retryable = service.get_retryable_deliveries(START + timedelta(minutes=5))

    assert len(retryable) == 1
    assert retryable[0].sla_action_type == action_type
    assert retryable[0].sla_action_level == level


def test_failed_client_request_keeps_case_new_without_sent_event(
    db_session: Session,
) -> None:
    cases, service, public_id = delivery_setup(db_session)
    attempt = start(service, public_id).attempt

    complete(
        service,
        outcome=DeliveryStatus.FAILED,
        error_message='simulated transport failure',
        now=START,
    )

    assert cases.get_case(public_id).status is CaseStatus.NEW
    assert event_count(
        db_session, attempt.case_id, CaseEventType.CLIENT_REQUEST_SENT
    ) == 0


def test_moderator_notification_success_is_idempotent_and_keeps_state(
    db_session: Session,
) -> None:
    cases, service, public_id = delivery_setup(db_session)
    start(service, public_id, key='client-request')
    complete(
        service,
        key='client-request',
        outcome=DeliveryStatus.SUCCEEDED,
        external_message_id='client-request-external',
        now=START,
    )
    cases.record_client_reply(
        public_id,
        external_message_id='reply-1',
        text='Client answer',
        sender_id='client-1',
        now=START,
    )
    notification = start(
        service,
        public_id,
        key='moderator-notification:reply-1',
        delivery_type=DeliveryType.MODERATOR_NOTIFICATION,
        recipient_id='moderator-1',
    ).attempt

    first = complete(
        service,
        key='moderator-notification:reply-1',
        outcome=DeliveryStatus.SUCCEEDED,
        external_message_id='moderator-external',
        now=START,
    )
    repeated = complete(
        service,
        key='moderator-notification:reply-1',
        outcome=DeliveryStatus.SUCCEEDED,
        external_message_id='moderator-external',
        now=START + timedelta(minutes=1),
    )

    assert first.already_processed is False
    assert repeated.already_processed is True
    assert cases.get_case(public_id).status is CaseStatus.WAITING_MODERATOR
    assert event_count(
        db_session, notification.case_id, CaseEventType.MODERATOR_NOTIFIED
    ) == 1


def test_failed_moderator_notification_preserves_reply_and_state(
    db_session: Session,
) -> None:
    cases, service, public_id = delivery_setup(db_session)
    cases.mark_client_request_sent(public_id, now=START)
    cases.record_client_reply(
        public_id,
        external_message_id='reply-failed-notification',
        text='Persisted answer',
        sender_id='client-1',
        now=START,
    )
    notification = start(
        service,
        public_id,
        key='moderator-notification:failed',
        delivery_type=DeliveryType.MODERATOR_NOTIFICATION,
        recipient_id='moderator-1',
    ).attempt

    complete(
        service,
        key='moderator-notification:failed',
        outcome=DeliveryStatus.FAILED,
        error_message='simulated transport failure',
        now=START,
    )

    assert cases.get_case(public_id).status is CaseStatus.WAITING_MODERATOR
    assert event_count(
        db_session, notification.case_id, CaseEventType.CLIENT_REPLY_RECEIVED
    ) == 1
    assert event_count(
        db_session, notification.case_id, CaseEventType.MODERATOR_NOTIFIED
    ) == 0


def test_client_request_business_effect_failure_rolls_back_completion(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    cases, service, public_id = delivery_setup(db_session)
    attempt = start(service, public_id).attempt

    def fail_business_effect(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError('simulated business effect failure')

    monkeypatch.setattr(
        service.case_service,
        '_stage_client_request_sent',
        fail_business_effect,
    )

    with pytest.raises(RuntimeError, match='business effect failure'):
        complete(
            service,
            outcome=DeliveryStatus.SUCCEEDED,
            external_message_id='external-rollback',
            now=START,
        )

    db_session.expire_all()
    persisted = service.delivery_repository.get_attempt('delivery-key', 1)
    assert persisted is not None
    assert persisted.status == DeliveryStatus.PENDING.value
    assert cases.get_case(public_id).status is CaseStatus.NEW
    assert event_count(
        db_session, attempt.case_id, CaseEventType.CLIENT_REQUEST_SENT
    ) == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"delivery_max_attempts": 0},
        {"delivery_retry_delay_minutes": -1},
    ],
)
def test_invalid_delivery_configuration_is_rejected(
    overrides: dict[str, int]
) -> None:
    with pytest.raises(ValueError, match="Delivery"):
        settings(**overrides)
