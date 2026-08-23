from dataclasses import dataclass
from datetime import datetime, timedelta
from sqlite3 import SQLITE_CONSTRAINT_UNIQUE, IntegrityError as SQLiteIntegrityError

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import CaseEventRecord, DeliveryAttemptRecord
from app.db.models.common import as_naive_utc, utc_now
from app.db.repositories import CaseRepository, DeliveryRepository
from app.domain.delivery import DeliveryStatus, DeliveryType, RetryableDelivery
from app.domain.enums import CaseEventType, CaseStatus
from app.domain.state_machine import InvalidStateTransition
from app.services.case_service import CaseNotFoundError, CaseService


class DeliveryServiceError(RuntimeError):
    """Base error for delivery tracking operations."""


class DeliveryAttemptNotFoundError(DeliveryServiceError):
    def __init__(self, idempotency_key: str, attempt_number: int) -> None:
        self.idempotency_key = idempotency_key
        self.attempt_number = attempt_number
        super().__init__(
            f"Delivery attempt not found: {idempotency_key} attempt {attempt_number}"
        )


class DeliveryIdentityConflictError(DeliveryServiceError):
    def __init__(self, idempotency_key: str) -> None:
        self.idempotency_key = idempotency_key
        super().__init__(
            f"Idempotency key describes a different delivery: {idempotency_key}"
        )


class RetryNotDueError(DeliveryServiceError):
    def __init__(self, idempotency_key: str, next_retry_at: datetime) -> None:
        self.idempotency_key = idempotency_key
        self.next_retry_at = next_retry_at
        super().__init__(f"Delivery retry is not due: {idempotency_key}")


class DeliveryRetriesExhaustedError(DeliveryServiceError):
    def __init__(self, idempotency_key: str) -> None:
        self.idempotency_key = idempotency_key
        super().__init__(f"Delivery retries exhausted: {idempotency_key}")


class DeliveryAttemptConflictError(DeliveryServiceError):
    def __init__(self, idempotency_key: str, attempt_number: int) -> None:
        self.idempotency_key = idempotency_key
        self.attempt_number = attempt_number
        super().__init__(
            f"Conflicting result for {idempotency_key} attempt {attempt_number}"
        )


class InvalidDeliveryOutcomeError(DeliveryServiceError):
    def __init__(self) -> None:
        super().__init__("Delivery result status must be SUCCEEDED or FAILED")


@dataclass(frozen=True, slots=True)
class DeliveryAttemptResult:
    case_public_id: str
    attempt: DeliveryAttemptRecord
    already_processed: bool
    delivery_completed: bool


def _is_duplicate_delivery_attempt(error: IntegrityError) -> bool:
    original_error = error.orig
    return (
        isinstance(original_error, SQLiteIntegrityError)
        and getattr(original_error, "sqlite_errorcode", None)
        == SQLITE_CONSTRAINT_UNIQUE
        and str(original_error)
        == (
            "UNIQUE constraint failed: delivery_attempts.idempotency_key, "
            "delivery_attempts.attempt_number"
        )
    )


def _is_duplicate_delivery_event(error: IntegrityError) -> bool:
    original_error = error.orig
    return (
        isinstance(original_error, SQLiteIntegrityError)
        and getattr(original_error, "sqlite_errorcode", None)
        == SQLITE_CONSTRAINT_UNIQUE
        and str(original_error)
        == "UNIQUE constraint failed: case_events.deduplication_key"
    )


class DeliveryService:
    """Delivery attempt lifecycle and deterministic retry decisions."""

    def __init__(
        self,
        session: Session,
        delivery_repository: DeliveryRepository | None = None,
        case_repository: CaseRepository | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.delivery_repository = delivery_repository or DeliveryRepository(session)
        self.case_repository = case_repository or CaseRepository(session)
        self.settings = settings or get_settings()
        self.case_service = CaseService(
            session,
            repository=self.case_repository,
            settings=self.settings,
        )

    def start_delivery_attempt(
        self,
        *,
        case_public_id: str,
        delivery_type: DeliveryType,
        recipient_id: str,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> DeliveryAttemptResult:
        attempt_number: int | None = None
        case_id: int | None = None
        try:
            case = self.case_repository.get_case(case_public_id)
            if case is None:
                raise CaseNotFoundError(case_public_id)
            case_id = case.id
            attempts = self.delivery_repository.get_attempts(idempotency_key)
            if attempts:
                self._validate_identity(
                    attempts[0], case.id, delivery_type, recipient_id
                )
                successful = next(
                    (
                        attempt
                        for attempt in reversed(attempts)
                        if attempt.status == DeliveryStatus.SUCCEEDED.value
                    ),
                    None,
                )
                if successful is not None:
                    return self._result(
                        case_public_id, successful, True, delivery_completed=True
                    )
                latest = attempts[-1]
                if latest.status == DeliveryStatus.PENDING.value:
                    return self._result(case_public_id, latest, True)
                if latest.status != DeliveryStatus.FAILED.value:
                    raise DeliveryAttemptConflictError(
                        idempotency_key, latest.attempt_number
                    )
                if latest.attempt_number >= self.settings.delivery_max_attempts:
                    raise DeliveryRetriesExhaustedError(idempotency_key)
                if latest.completed_at is None:
                    raise DeliveryAttemptConflictError(
                        idempotency_key, latest.attempt_number
                    )
                command_time = as_naive_utc(now) if now is not None else utc_now()
                next_retry_at = latest.completed_at + timedelta(
                    minutes=self.settings.delivery_retry_delay_minutes
                )
                if command_time < next_retry_at:
                    raise RetryNotDueError(idempotency_key, next_retry_at)
                attempt_number = latest.attempt_number + 1
            else:
                command_time = as_naive_utc(now) if now is not None else utc_now()
                attempt_number = 1

            attempt = DeliveryAttemptRecord(
                case_id=case.id,
                delivery_type=delivery_type.value,
                recipient_id=recipient_id,
                idempotency_key=idempotency_key,
                attempt_number=attempt_number,
                status=DeliveryStatus.PENDING.value,
                created_at=command_time,
            )
            self.delivery_repository.add_attempt(attempt)
            if attempt_number > 1:
                self.case_repository.add_event(
                    self._delivery_event(
                        case_id=case.id,
                        event_type=CaseEventType.DELIVERY_RETRIED,
                        delivery_type=delivery_type,
                        idempotency_key=idempotency_key,
                        attempt_number=attempt_number,
                        created_at=command_time,
                        event_suffix="retried",
                    )
                )
            self.session.commit()
            return self._result(case_public_id, attempt, False)
        except IntegrityError as error:
            self.session.rollback()
            if (
                attempt_number is None
                or case_id is None
                or not _is_duplicate_delivery_attempt(error)
            ):
                raise
            winning_attempt = self.delivery_repository.get_attempt(
                idempotency_key, attempt_number
            )
            if winning_attempt is None:
                raise
            self._validate_identity(
                winning_attempt, case_id, delivery_type, recipient_id
            )
            if winning_attempt.status != DeliveryStatus.PENDING.value:
                raise
            return self._result(case_public_id, winning_attempt, True)
        except Exception:
            self.session.rollback()
            raise

    def complete_delivery_attempt(
        self,
        *,
        idempotency_key: str,
        attempt_number: int,
        outcome: DeliveryStatus,
        external_message_id: str | None = None,
        error_message: str | None = None,
        now: datetime | None = None,
    ) -> DeliveryAttemptResult:
        if outcome not in (DeliveryStatus.SUCCEEDED, DeliveryStatus.FAILED):
            raise InvalidDeliveryOutcomeError()
        try:
            attempt = self.delivery_repository.get_attempt(
                idempotency_key, attempt_number
            )
            if attempt is None:
                raise DeliveryAttemptNotFoundError(
                    idempotency_key, attempt_number
                )
            case_public_id = attempt.case.public_id
            if attempt.status != DeliveryStatus.PENDING.value:
                return self._completed_result(
                    attempt,
                    case_public_id,
                    outcome,
                    external_message_id,
                )

            completed_at = as_naive_utc(now) if now is not None else utc_now()
            transitioned = self.delivery_repository.transition_pending_attempt(
                idempotency_key=idempotency_key,
                attempt_number=attempt_number,
                status=outcome,
                completed_at=completed_at,
                external_message_id=(
                    external_message_id
                    if outcome is DeliveryStatus.SUCCEEDED
                    else None
                ),
                error_message=(
                    error_message if outcome is DeliveryStatus.FAILED else None
                ),
            )
            if not transitioned:
                self.session.rollback()
                persisted = self.delivery_repository.get_attempt(
                    idempotency_key, attempt_number
                )
                if persisted is None:
                    raise DeliveryAttemptNotFoundError(
                        idempotency_key, attempt_number
                    )
                return self._completed_result(
                    persisted,
                    persisted.case.public_id,
                    outcome,
                    external_message_id,
                )

            self.session.refresh(attempt)
            if outcome is DeliveryStatus.FAILED:
                self.case_repository.add_event(
                    self._delivery_event(
                        case_id=attempt.case_id,
                        event_type=CaseEventType.DELIVERY_FAILED,
                        delivery_type=DeliveryType(attempt.delivery_type),
                        idempotency_key=idempotency_key,
                        attempt_number=attempt_number,
                        created_at=completed_at,
                        event_suffix="failed",
                    )
                )
            if outcome is DeliveryStatus.SUCCEEDED:
                self._apply_successful_delivery_effect(attempt, completed_at)
            self.session.commit()
            return self._result(
                case_public_id,
                attempt,
                False,
                delivery_completed=outcome is DeliveryStatus.SUCCEEDED,
            )
        except IntegrityError as error:
            self.session.rollback()
            if (
                outcome is not DeliveryStatus.FAILED
                or not _is_duplicate_delivery_event(error)
            ):
                raise
            persisted = self.delivery_repository.get_attempt(
                idempotency_key, attempt_number
            )
            if (
                persisted is None
                or persisted.status != DeliveryStatus.FAILED.value
            ):
                raise
            return self._completed_result(
                persisted,
                persisted.case.public_id,
                outcome,
                external_message_id,
            )
        except Exception:
            self.session.rollback()
            raise

    def get_retryable_deliveries(
        self, now: datetime | None = None
    ) -> list[RetryableDelivery]:
        query_time = as_naive_utc(now) if now is not None else utc_now()
        retry_delay = timedelta(
            minutes=self.settings.delivery_retry_delay_minutes
        )
        attempts = self.delivery_repository.get_retryable_latest_attempts(
            completed_by=query_time - retry_delay,
            max_attempts=self.settings.delivery_max_attempts,
        )
        return [
            RetryableDelivery(
                case_public_id=attempt.case.public_id,
                idempotency_key=attempt.idempotency_key,
                delivery_type=DeliveryType(attempt.delivery_type),
                recipient_id=attempt.recipient_id,
                last_attempt_number=attempt.attempt_number,
                next_retry_at=attempt.completed_at + retry_delay,
            )
            for attempt in attempts
            if attempt.completed_at is not None
        ]

    @staticmethod
    def _validate_identity(
        attempt: DeliveryAttemptRecord,
        case_id: int,
        delivery_type: DeliveryType,
        recipient_id: str,
    ) -> None:
        if (
            attempt.case_id != case_id
            or attempt.delivery_type != delivery_type.value
            or attempt.recipient_id != recipient_id
        ):
            raise DeliveryIdentityConflictError(attempt.idempotency_key)

    @staticmethod
    def _completed_result(
        attempt: DeliveryAttemptRecord,
        case_public_id: str,
        outcome: DeliveryStatus,
        external_message_id: str | None,
    ) -> DeliveryAttemptResult:
        if attempt.status != outcome.value:
            raise DeliveryAttemptConflictError(
                attempt.idempotency_key, attempt.attempt_number
            )
        if (
            outcome is DeliveryStatus.SUCCEEDED
            and external_message_id is not None
            and attempt.external_message_id != external_message_id
        ):
            raise DeliveryAttemptConflictError(
                attempt.idempotency_key, attempt.attempt_number
            )
        return DeliveryService._result(
            case_public_id,
            attempt,
            True,
            delivery_completed=outcome is DeliveryStatus.SUCCEEDED,
        )

    @staticmethod
    def _delivery_event(
        *,
        case_id: int,
        event_type: CaseEventType,
        delivery_type: DeliveryType,
        idempotency_key: str,
        attempt_number: int,
        created_at: datetime,
        event_suffix: str,
    ) -> CaseEventRecord:
        return CaseEventRecord(
            case_id=case_id,
            event_type=event_type,
            created_at=created_at,
            metadata_json={
                "delivery_type": delivery_type.value,
                "attempt_number": attempt_number,
                "idempotency_key": idempotency_key,
            },
            deduplication_key=(
                f"delivery:{idempotency_key}:attempt:{attempt_number}:"
                f"{event_suffix}"
            ),
        )

    def _apply_successful_delivery_effect(
        self,
        attempt: DeliveryAttemptRecord,
        completed_at: datetime,
    ) -> None:
        delivery_type = DeliveryType(attempt.delivery_type)
        metadata = {
            "delivery_type": delivery_type.value,
            "attempt_number": attempt.attempt_number,
            "idempotency_key": attempt.idempotency_key,
            "external_message_id": attempt.external_message_id,
        }
        deduplication_key = (
            f"delivery:{attempt.idempotency_key}:business-success"
        )
        if delivery_type is DeliveryType.CLIENT_REQUEST:
            self.case_service._stage_client_request_sent(
                attempt.case,
                command_time=completed_at,
                actor_type="transport",
                actor_id=attempt.recipient_id,
                metadata_json=metadata,
                deduplication_key=deduplication_key,
            )
        elif delivery_type is DeliveryType.MODERATOR_NOTIFICATION:
            if attempt.case.status is not CaseStatus.WAITING_MODERATOR:
                raise InvalidStateTransition(
                    attempt.case.status, CaseStatus.WAITING_MODERATOR
                )
            self.case_repository.add_event(
                CaseEventRecord(
                    case_id=attempt.case_id,
                    event_type=CaseEventType.MODERATOR_NOTIFIED,
                    actor_type="transport",
                    actor_id=attempt.recipient_id,
                    created_at=completed_at,
                    metadata_json=metadata,
                    deduplication_key=deduplication_key,
                )
            )

    @staticmethod
    def _result(
        case_public_id: str,
        attempt: DeliveryAttemptRecord,
        already_processed: bool,
        delivery_completed: bool = False,
    ) -> DeliveryAttemptResult:
        return DeliveryAttemptResult(
            case_public_id=case_public_id,
            attempt=attempt,
            already_processed=already_processed,
            delivery_completed=delivery_completed,
        )
