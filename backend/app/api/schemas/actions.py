from datetime import datetime

from pydantic import BaseModel

from app.api.schemas.cases import as_utc
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

    @classmethod
    def from_action(cls, action: DueAction) -> "DueActionResponse":
        return cls(
            case_public_id=action.case_public_id,
            action_type=action.action_type,
            level=action.level,
            recipient_role=action.recipient_role,
            due_at=as_utc(action.due_at),
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
