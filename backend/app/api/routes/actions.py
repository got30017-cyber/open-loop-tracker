from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_time, get_db_session
from app.api.schemas import (
    AcknowledgeActionRequest,
    AcknowledgeActionResponse,
    DueActionResponse,
)
from app.services import SlaService

router = APIRouter(prefix="/api/v1/actions", tags=["actions"])
DatabaseSession = Annotated[Session, Depends(get_db_session)]
CurrentTime = Annotated[datetime, Depends(get_current_time)]


@router.get("/due", response_model=list[DueActionResponse])
def get_due_actions(
    session: DatabaseSession, now: CurrentTime
) -> list[DueActionResponse]:
    actions = SlaService(session).get_due_actions(now=now)
    return [DueActionResponse.from_action(action) for action in actions]


@router.post("/{case_public_id}/ack", response_model=AcknowledgeActionResponse)
def acknowledge_action(
    case_public_id: str,
    request: AcknowledgeActionRequest,
    session: DatabaseSession,
    now: CurrentTime,
) -> AcknowledgeActionResponse:
    result = SlaService(session).acknowledge_action(
        case_public_id=case_public_id,
        action_type=request.action_type,
        level=request.level,
        now=now,
    )
    return AcknowledgeActionResponse.from_result(result)
