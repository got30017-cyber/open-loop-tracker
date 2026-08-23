from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.api.schemas.cases import as_utc
from app.domain.state_machine import InvalidStateTransition
from app.services import (
    CaseNotFoundError,
    DeliveryAttemptConflictError,
    DeliveryAttemptNotFoundError,
    DeliveryIdentityConflictError,
    DeliveryRetriesExhaustedError,
    DuplicateClientReplyError,
    ExternalMessageIdConflictError,
    InvalidDeliveryOutcomeError,
    InvalidSlaActionError,
    RetryNotDueError,
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

    @application.exception_handler(DeliveryAttemptNotFoundError)
    async def delivery_attempt_not_found(
        _request: Request, error: DeliveryAttemptNotFoundError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "delivery_attempt_not_found", "message": str(error)},
        )

    @application.exception_handler(RetryNotDueError)
    async def delivery_retry_not_due(
        _request: Request, error: RetryNotDueError
    ) -> JSONResponse:
        next_retry_at = as_utc(error.next_retry_at)
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "error": "delivery_retry_not_due",
                "message": str(error),
                "details": {
                    "next_retry_at": next_retry_at.isoformat().replace(
                        "+00:00", "Z"
                    )
                },
            },
        )

    @application.exception_handler(DeliveryRetriesExhaustedError)
    async def delivery_retries_exhausted(
        _request: Request, error: DeliveryRetriesExhaustedError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": "delivery_retries_exhausted", "message": str(error)},
        )

    @application.exception_handler(DeliveryIdentityConflictError)
    async def delivery_identity_conflict(
        _request: Request, error: DeliveryIdentityConflictError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": "delivery_identity_conflict", "message": str(error)},
        )

    @application.exception_handler(DeliveryAttemptConflictError)
    async def delivery_attempt_conflict(
        _request: Request, error: DeliveryAttemptConflictError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": "delivery_attempt_conflict", "message": str(error)},
        )

    @application.exception_handler(InvalidDeliveryOutcomeError)
    async def invalid_delivery_outcome(
        _request: Request, error: InvalidDeliveryOutcomeError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": "invalid_delivery_outcome", "message": str(error)},
        )
