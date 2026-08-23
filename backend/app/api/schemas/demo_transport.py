from pydantic import BaseModel, Field

from app.domain.delivery import DeliveryStatus, DeliveryType


class DemoDeliveryRequest(BaseModel):
    delivery_type: DeliveryType
    recipient_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    attempt_number: int = Field(ge=1)
    simulate_failure: bool = False


class DemoDeliveryResponse(BaseModel):
    status: DeliveryStatus
    idempotency_key: str
    attempt_number: int
    external_message_id: str | None = None
    error_message: str | None = None
