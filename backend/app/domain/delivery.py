from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class DeliveryStatus(str, Enum):
    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class DeliveryType(str, Enum):
    CLIENT_REQUEST = "CLIENT_REQUEST"
    MODERATOR_NOTIFICATION = "MODERATOR_NOTIFICATION"
    CLIENT_REMINDER = "CLIENT_REMINDER"
    MODERATOR_REMINDER = "MODERATOR_REMINDER"
    ESCALATION = "ESCALATION"


@dataclass(frozen=True, slots=True)
class RetryableDelivery:
    case_public_id: str
    idempotency_key: str
    delivery_type: DeliveryType
    recipient_id: str
    last_attempt_number: int
    next_retry_at: datetime
