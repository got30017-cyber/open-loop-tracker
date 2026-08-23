from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db_session
from app.api.schemas import (
    CancelCaseRequest,
    CaseEventResponse,
    CaseResponse,
    ClientReplyRequest,
    CommandResponse,
    CreateCaseRequest,
    ReassignModeratorRequest,
)
from app.services import CaseService

router = APIRouter(prefix="/api/v1/cases", tags=["cases"])
DatabaseSession = Annotated[Session, Depends(get_db_session)]


@router.post("", response_model=CaseResponse, status_code=status.HTTP_201_CREATED)
def create_case(
    request: CreateCaseRequest, session: DatabaseSession
) -> CaseResponse:
    case = CaseService(session).create_case(
        original_message=request.original_message,
        original_message_reference=request.original_message_reference,
        end_user_reference=request.end_user_reference,
        moderator_id=request.moderator_id,
        client_contact_id=request.client_contact_id,
    )
    return CaseResponse.from_record(case)


@router.get("/{public_id}", response_model=CaseResponse)
def get_case(public_id: str, session: DatabaseSession) -> CaseResponse:
    case = CaseService(session).get_case(public_id)
    return CaseResponse.from_record(case)


@router.post("/{public_id}/send-to-client", response_model=CommandResponse)
def send_to_client(public_id: str, session: DatabaseSession) -> CommandResponse:
    result = CaseService(session).mark_client_request_sent(public_id)
    return CommandResponse.from_result(result)


@router.post("/{public_id}/client-reply", response_model=CommandResponse)
def record_client_reply(
    public_id: str, request: ClientReplyRequest, session: DatabaseSession
) -> CommandResponse:
    result = CaseService(session).record_client_reply(
        public_id=public_id,
        external_message_id=request.external_message_id,
        text=request.text,
        sender_id=request.sender_id,
    )
    return CommandResponse.from_result(result)


@router.post("/{public_id}/user-answered", response_model=CommandResponse)
def confirm_user_answered(
    public_id: str, session: DatabaseSession
) -> CommandResponse:
    result = CaseService(session).confirm_user_answered(public_id)
    return CommandResponse.from_result(result)


@router.post("/{public_id}/cancel", response_model=CommandResponse)
def cancel_case(
    public_id: str, request: CancelCaseRequest, session: DatabaseSession
) -> CommandResponse:
    result = CaseService(session).cancel_case(
        public_id=public_id, cancellation_reason=request.reason
    )
    return CommandResponse.from_result(result)


@router.post("/{public_id}/reassign", response_model=CommandResponse)
def reassign_moderator(
    public_id: str, request: ReassignModeratorRequest, session: DatabaseSession
) -> CommandResponse:
    result = CaseService(session).reassign_moderator(
        public_id=public_id, new_moderator_id=request.moderator_id
    )
    return CommandResponse.from_result(result)


@router.get("/{public_id}/events", response_model=list[CaseEventResponse])
def get_case_events(
    public_id: str, session: DatabaseSession
) -> list[CaseEventResponse]:
    events = CaseService(session).get_case_events(public_id)
    return [CaseEventResponse.from_record(event) for event in events]
