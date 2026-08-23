from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.domain.state_machine import InvalidStateTransition
from app.services import (
    CaseNotFoundError,
    DuplicateClientReplyError,
    ExternalMessageIdConflictError,
    InvalidSlaActionError,
)


def register_exception_handlers(application: FastAPI) -> None:
    @application.exception_handler(CaseNotFoundError)
    async def case_not_found(
        _request: Request, error: CaseNotFoundError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "case_not_found", "message": str(error)},
        )

    @application.exception_handler(InvalidStateTransition)
    async def invalid_transition(
        _request: Request, error: InvalidStateTransition
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "error": "invalid_state_transition",
                "message": str(error),
                "details": {
                    "current_status": error.current_status.value,
                    "requested_status": error.requested_status.value,
                },
            },
        )

    @application.exception_handler(ExternalMessageIdConflictError)
    async def external_message_id_conflict(
        _request: Request, error: ExternalMessageIdConflictError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "error": "external_message_id_conflict",
                "message": str(error),
            },
        )

    @application.exception_handler(DuplicateClientReplyError)
    async def duplicate_client_reply(
        _request: Request, error: DuplicateClientReplyError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": "duplicate_client_reply", "message": str(error)},
        )

    @application.exception_handler(InvalidSlaActionError)
    async def invalid_sla_action(
        _request: Request, error: InvalidSlaActionError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": "invalid_sla_action", "message": str(error)},
        )

    @application.exception_handler(SQLAlchemyError)
    async def persistence_error(
        _request: Request, _error: SQLAlchemyError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "persistence_error",
                "message": "A persistence operation failed",
            },
        )
