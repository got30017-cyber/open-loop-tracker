from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.db.models import CaseEventRecord, CaseRecord
from app.domain.enums import CaseEventType, CaseStatus
from app.services import CommandResult


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class CreateCaseRequest(BaseModel):
    original_message: str = Field(min_length=1)
    original_message_reference: str | None = None
    end_user_reference: str | None = None
    moderator_id: str | None = None
    client_contact_id: str | None = None


class ClientReplyRequest(BaseModel):
    external_message_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    sender_id: str | None = None


class CancelCaseRequest(BaseModel):
    reason: str = Field(min_length=1)


class ReassignModeratorRequest(BaseModel):
    moderator_id: str = Field(min_length=1)


class CaseResponse(BaseModel):
    public_id: str
    status: CaseStatus
    original_message: str
    original_message_reference: str | None
    end_user_reference: str | None
    moderator_id: str | None
    client_contact_id: str | None
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None
    cancelled_at: datetime | None
    cancellation_reason: str | None

    @classmethod
    def from_record(cls, case: CaseRecord) -> "CaseResponse":
        return cls(
            public_id=case.public_id,
            status=case.status,
            original_message=case.original_message,
            original_message_reference=case.original_message_reference,
            end_user_reference=case.end_user_reference,
            moderator_id=case.moderator_id,
            client_contact_id=case.client_contact_id,
            created_at=as_utc(case.created_at),
            updated_at=as_utc(case.updated_at),
            closed_at=as_utc(case.closed_at),
            cancelled_at=as_utc(case.cancelled_at),
            cancellation_reason=case.cancellation_reason,
        )


class CommandResponse(BaseModel):
    public_id: str
    status: CaseStatus
    already_processed: bool
    moderator_id: str | None

    @classmethod
    def from_result(cls, result: CommandResult) -> "CommandResponse":
        return cls(
            public_id=result.case.public_id,
            status=result.case.status,
            already_processed=result.already_processed,
            moderator_id=result.case.moderator_id,
        )


class CaseEventResponse(BaseModel):
    event_type: CaseEventType
    actor_type: str | None
    actor_id: str | None
    created_at: datetime
    metadata: dict[str, Any] | None

    @classmethod
    def from_record(cls, event: CaseEventRecord) -> "CaseEventResponse":
        return cls(
            event_type=event.event_type,
            actor_type=event.actor_type,
            actor_id=event.actor_id,
            created_at=as_utc(event.created_at),
            metadata=event.metadata_json,
        )
