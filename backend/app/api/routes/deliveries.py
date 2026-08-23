from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_time, get_db_session
from app.api.schemas import (
    CompleteDeliveryAttemptRequest,
    DeliveryAttemptResponse,
    RetryableDeliveryResponse,
    StartDeliveryAttemptRequest,
)
from app.services import DeliveryService

router = APIRouter(prefix="/api/v1/deliveries", tags=["deliveries"])
DatabaseSession = Annotated[Session, Depends(get_db_session)]
CurrentTime = Annotated[datetime, Depends(get_current_time)]


@router.post("/attempts", response_model=DeliveryAttemptResponse)
def start_delivery_attempt(
    request: StartDeliveryAttemptRequest,
    session: DatabaseSession,
    now: CurrentTime,
) -> DeliveryAttemptResponse:
    result = DeliveryService(session).start_delivery_attempt(
        case_public_id=request.case_public_id,
        delivery_type=request.delivery_type,
        recipient_id=request.recipient_id,
        idempotency_key=request.idempotency_key,
        allow_retry=request.allow_retry,
        now=now,
    )
    return DeliveryAttemptResponse.from_result(result)


@router.post(
    "/{idempotency_key}/attempts/{attempt_number}/result",
    response_model=DeliveryAttemptResponse,
)
def complete_delivery_attempt(
    idempotency_key: str,
    attempt_number: int,
    request: CompleteDeliveryAttemptRequest,
    session: DatabaseSession,
    now: CurrentTime,
) -> DeliveryAttemptResponse:
    result = DeliveryService(session).complete_delivery_attempt(
        idempotency_key=idempotency_key,
        attempt_number=attempt_number,
        outcome=request.status,
        external_message_id=request.external_message_id,
        error_message=request.error_message,
        now=now,
    )
    return DeliveryAttemptResponse.from_result(result)


@router.get("/retryable", response_model=list[RetryableDeliveryResponse])
def get_retryable_deliveries(
    session: DatabaseSession, now: CurrentTime
) -> list[RetryableDeliveryResponse]:
    deliveries = DeliveryService(session).get_retryable_deliveries(now=now)
    return [
        RetryableDeliveryResponse.from_delivery(delivery)
        for delivery in deliveries
    ]
