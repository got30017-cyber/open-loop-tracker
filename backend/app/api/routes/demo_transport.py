from hashlib import sha256

from fastapi import APIRouter

from app.api.schemas.demo_transport import (
    DemoDeliveryRequest,
    DemoDeliveryResponse,
)
from app.domain.delivery import DeliveryStatus

router = APIRouter(prefix="/api/v1/demo", tags=["demo-transport"])


@router.post("/deliveries", response_model=DemoDeliveryResponse)
def deliver_locally(request: DemoDeliveryRequest) -> DemoDeliveryResponse:
    if request.simulate_failure:
        return DemoDeliveryResponse(
            status=DeliveryStatus.FAILED,
            idempotency_key=request.idempotency_key,
            attempt_number=request.attempt_number,
            error_message="Simulated local transport failure",
        )

    identity = (
        f"{request.delivery_type.value}:{request.idempotency_key}:"
        f"{request.attempt_number}"
    )
    digest = sha256(identity.encode()).hexdigest()[:24]
    return DemoDeliveryResponse(
        status=DeliveryStatus.SUCCEEDED,
        idempotency_key=request.idempotency_key,
        attempt_number=request.attempt_number,
        external_message_id=f"mock-{digest}",
    )
