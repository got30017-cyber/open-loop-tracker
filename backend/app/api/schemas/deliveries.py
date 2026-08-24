from datetime import datetime

from pydantic import BaseModel, Field

from app.api.schemas.cases import as_utc
from app.domain.delivery import DeliveryStatus, DeliveryType, RetryableDelivery
from app.domain.sla import SlaActionType
from app.services import DeliveryAttemptResult


class StartDeliveryAttemptRequest(BaseModel):
    case_public_id: str = Field(min_length=1)
    delivery_type: DeliveryType
    recipient_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    allow_retry: bool = True


class CompleteDeliveryAttemptRequest(BaseModel):
    status: DeliveryStatus
    external_message_id: str | None = None
    error_message: str | None = None


class DeliveryAttemptResponse(BaseModel):
    case_public_id: str
    idempotency_key: str
    delivery_type: DeliveryType
    recipient_id: str
    attempt_number: int
    status: DeliveryStatus
    created_at: datetime
    completed_at: datetime | None
    external_message_id: str | None
    error_message: str | None
    already_processed: bool
    delivery_completed: bool

    @classmethod
    def from_result(cls, result: DeliveryAttemptResult) -> "DeliveryAttemptResponse":
        attempt = result.attempt
        return cls(
            case_public_id=result.case_public_id,
            idempotency_key=attempt.idempotency_key,
            delivery_type=DeliveryType(attempt.delivery_type),
            recipient_id=attempt.recipient_id,
            attempt_number=attempt.attempt_number,
            status=DeliveryStatus(attempt.status),
            created_at=as_utc(attempt.created_at),
            completed_at=as_utc(attempt.completed_at),
            external_message_id=attempt.external_message_id,
            error_message=attempt.error_message,
            already_processed=result.already_processed,
            delivery_completed=result.delivery_completed,
        )


class RetryableDeliveryResponse(BaseModel):
    case_public_id: str
    idempotency_key: str
    delivery_type: DeliveryType
    recipient_id: str
    last_attempt_number: int
    next_retry_at: datetime | None
    sla_action_type: SlaActionType | None
    sla_action_level: int | None

    @classmethod
    def from_delivery(
        cls, delivery: RetryableDelivery
    ) -> "RetryableDeliveryResponse":
        return cls(
            case_public_id=delivery.case_public_id,
            idempotency_key=delivery.idempotency_key,
            delivery_type=delivery.delivery_type,
            recipient_id=delivery.recipient_id,
            last_attempt_number=delivery.last_attempt_number,
            next_retry_at=as_utc(delivery.next_retry_at),
            sla_action_type=delivery.sla_action_type,
            sla_action_level=delivery.sla_action_level,
        )
