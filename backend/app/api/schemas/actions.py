from datetime import datetime

from pydantic import BaseModel

from app.api.schemas.cases import as_utc
from app.domain.delivery import DeliveryType
from app.domain.sla import DueAction, RecipientRole, SlaActionType
from app.services import AcknowledgeActionResult


class AcknowledgeActionRequest(BaseModel):
    action_type: SlaActionType
    level: int | None = None


class DueActionResponse(BaseModel):
    case_public_id: str
    action_type: SlaActionType
    level: int | None
    recipient_role: RecipientRole
    due_at: datetime
    delivery_type: DeliveryType
    recipient_id: str
    delivery_idempotency_key: str

    @classmethod
    def from_action(cls, action: DueAction) -> "DueActionResponse":
        if (
            action.delivery_type is None
            or action.recipient_id is None
            or action.delivery_idempotency_key is None
        ):
            raise ValueError("Due action is missing delivery routing")
        return cls(
            case_public_id=action.case_public_id,
            action_type=action.action_type,
            level=action.level,
            recipient_role=action.recipient_role,
            due_at=as_utc(action.due_at),
            delivery_type=action.delivery_type,
            recipient_id=action.recipient_id,
            delivery_idempotency_key=action.delivery_idempotency_key,
        )


class AcknowledgeActionResponse(BaseModel):
    case_public_id: str
    action_type: SlaActionType
    level: int | None
    already_processed: bool

    @classmethod
    def from_result(
        cls, result: AcknowledgeActionResult
    ) -> "AcknowledgeActionResponse":
        return cls(
            case_public_id=result.case_public_id,
            action_type=result.action_type,
            level=result.level,
            already_processed=result.already_processed,
        )
