from datetime import datetime

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.db.models import DeliveryAttemptRecord
from app.domain.delivery import DeliveryStatus


class DeliveryRepository:
    """Session-bound delivery persistence without transaction commits."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add_attempt(self, attempt: DeliveryAttemptRecord) -> None:
        self.session.add(attempt)
        # Attempt uniqueness must be checked before adding its paired retry event.
        self.session.flush()

    def get_attempts(self, idempotency_key: str) -> list[DeliveryAttemptRecord]:
        return list(
            self.session.scalars(
                select(DeliveryAttemptRecord)
                .where(DeliveryAttemptRecord.idempotency_key == idempotency_key)
                .order_by(DeliveryAttemptRecord.attempt_number)
            )
        )

    def get_attempt(
        self, idempotency_key: str, attempt_number: int
    ) -> DeliveryAttemptRecord | None:
        return self.session.scalar(
            select(DeliveryAttemptRecord).where(
                DeliveryAttemptRecord.idempotency_key == idempotency_key,
                DeliveryAttemptRecord.attempt_number == attempt_number,
            )
        )

    def transition_pending_attempt(
        self,
        *,
        idempotency_key: str,
        attempt_number: int,
        status: DeliveryStatus,
        completed_at: datetime,
        external_message_id: str | None,
        error_message: str | None,
    ) -> bool:
        result = self.session.execute(
            update(DeliveryAttemptRecord)
            .where(
                DeliveryAttemptRecord.idempotency_key == idempotency_key,
                DeliveryAttemptRecord.attempt_number == attempt_number,
                DeliveryAttemptRecord.status == DeliveryStatus.PENDING.value,
            )
            .values(
                status=status.value,
                completed_at=completed_at,
                external_message_id=external_message_id,
                error_message=error_message,
            )
            .execution_options(synchronize_session=False)
        )
        return result.rowcount == 1

    def get_retryable_latest_attempts(
        self,
        *,
        completed_by: datetime,
        max_attempts: int,
    ) -> list[DeliveryAttemptRecord]:
        latest_numbers = (
            select(
                DeliveryAttemptRecord.idempotency_key.label("idempotency_key"),
                func.max(DeliveryAttemptRecord.attempt_number).label(
                    "attempt_number"
                ),
            )
            .group_by(DeliveryAttemptRecord.idempotency_key)
            .subquery()
        )
        succeeded_keys = select(DeliveryAttemptRecord.idempotency_key).where(
            DeliveryAttemptRecord.status == DeliveryStatus.SUCCEEDED.value
        )
        return list(
            self.session.scalars(
                select(DeliveryAttemptRecord)
                .join(
                    latest_numbers,
                    and_(
                        DeliveryAttemptRecord.idempotency_key
                        == latest_numbers.c.idempotency_key,
                        DeliveryAttemptRecord.attempt_number
                        == latest_numbers.c.attempt_number,
                    ),
                )
                .where(
                    or_(
                        and_(
                            DeliveryAttemptRecord.status
                            == DeliveryStatus.FAILED.value,
                            DeliveryAttemptRecord.completed_at.is_not(None),
                            DeliveryAttemptRecord.completed_at <= completed_by,
                            DeliveryAttemptRecord.attempt_number < max_attempts,
                        ),
                        and_(
                            DeliveryAttemptRecord.status
                            == DeliveryStatus.PENDING.value,
                            DeliveryAttemptRecord.attempt_number > 1,
                        ),
                    ),
                    DeliveryAttemptRecord.idempotency_key.not_in(succeeded_keys),
                )
                .order_by(DeliveryAttemptRecord.idempotency_key)
            )
        )
