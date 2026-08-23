from dataclasses import dataclass
from datetime import datetime, timedelta
from sqlite3 import SQLITE_CONSTRAINT_UNIQUE, IntegrityError as SQLiteIntegrityError
from typing import Any
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import CaseEventRecord, CaseRecord, ClientReplyRecord
from app.core.config import Settings, get_settings
from app.db.models.common import as_naive_utc, utc_now
from app.db.repositories import CaseRepository
from app.domain.case import Case as DomainCase
from app.domain.enums import CaseEventType, CaseStatus
from app.domain.state_machine import transition_case


class CaseServiceError(RuntimeError):
    """Base error for application-level case operations."""


class CaseNotFoundError(CaseServiceError):
    def __init__(self, public_id: str) -> None:
        self.public_id = public_id
        super().__init__(f"Case not found: {public_id}")


class DuplicateClientReplyError(CaseServiceError):
    def __init__(self, external_message_id: str) -> None:
        self.external_message_id = external_message_id
        super().__init__(
            f"Client reply external_message_id already exists: {external_message_id}"
        )


class ExternalMessageIdConflictError(DuplicateClientReplyError):
    def __init__(self, external_message_id: str, public_id: str) -> None:
        self.external_message_id = external_message_id
        self.public_id = public_id
        CaseServiceError.__init__(
            self,
            f"external_message_id {external_message_id} belongs to a different case",
        )


@dataclass(frozen=True, slots=True)
class CommandResult:
    case: CaseRecord
    already_processed: bool

    def __getattr__(self, name: str) -> Any:
        """Keep accepted CaseRecord-style reads compatible for service callers."""
        return getattr(self.case, name)


def _is_duplicate_external_message_id(error: IntegrityError) -> bool:
    original_error = error.orig
    return (
        isinstance(original_error, SQLiteIntegrityError)
        and getattr(original_error, "sqlite_errorcode", None)
        == SQLITE_CONSTRAINT_UNIQUE
        and str(original_error)
        == "UNIQUE constraint failed: client_replies.external_message_id"
    )


class CaseService:
    """Application commands with one transaction boundary per mutation."""

    def __init__(
        self,
        session: Session,
        repository: CaseRepository | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.repository = repository or CaseRepository(session)
        self.settings = settings or get_settings()

    def create_case(
        self,
        original_message: str,
        moderator_id: str | None = None,
        client_contact_id: str | None = None,
        original_message_reference: str | None = None,
        end_user_reference: str | None = None,
        actor_type: str | None = None,
        actor_id: str | None = None,
    ) -> CaseRecord:
        now = utc_now()
        case = CaseRecord(
            public_id=f"CASE-{uuid4().hex.upper()}",
            status=CaseStatus.NEW,
            original_message=original_message,
            original_message_reference=original_message_reference,
            end_user_reference=end_user_reference,
            moderator_id=moderator_id,
            client_contact_id=client_contact_id,
            created_at=now,
            updated_at=now,
        )
        try:
            self.repository.add_case(case)
            self.repository.add_event(
                self._event(case, CaseEventType.CASE_CREATED, actor_type, actor_id)
            )
            self.session.commit()
            return case
        except Exception:
            self.session.rollback()
            raise

    def mark_client_request_sent(
        self,
        public_id: str,
        actor_type: str | None = None,
        actor_id: str | None = None,
        now: datetime | None = None,
    ) -> CommandResult:
        try:
            case = self._require_case(public_id)
            if case.status is CaseStatus.WAITING_CLIENT:
                return CommandResult(case=case, already_processed=True)
            self._apply_transition(case, CaseStatus.WAITING_CLIENT)
            command_time = as_naive_utc(now) if now is not None else utc_now()
            case.updated_at = command_time
            case.client_deadline = command_time + timedelta(
                minutes=self.settings.client_reminder_1_minutes
            )
            case.moderator_deadline = None
            self.repository.add_event(
                self._event(
                    case, CaseEventType.CLIENT_REQUEST_SENT, actor_type, actor_id
                )
            )
            self.session.commit()
            return CommandResult(case=case, already_processed=False)
        except Exception:
            self.session.rollback()
            raise

    def record_client_reply(
        self,
        public_id: str,
        external_message_id: str,
        text: str,
        sender_id: str | None = None,
        now: datetime | None = None,
    ) -> CommandResult:
        case_id: int | None = None
        try:
            case = self._require_case(public_id)
            case_id = case.id
            existing_reply = self.repository.get_client_reply(external_message_id)
            if existing_reply is not None:
                if existing_reply.case_id != case.id:
                    raise ExternalMessageIdConflictError(
                        external_message_id, public_id
                    )
                return CommandResult(case=case, already_processed=True)
            self._validate_transition(case, CaseStatus.WAITING_MODERATOR)
            command_time = as_naive_utc(now) if now is not None else utc_now()
            case.status = CaseStatus.WAITING_MODERATOR
            case.updated_at = command_time
            case.client_deadline = None
            case.moderator_deadline = command_time + timedelta(
                minutes=self.settings.moderator_reminder_1_minutes
            )
            self.repository.add_client_reply(
                ClientReplyRecord(
                    case_id=case.id,
                    external_message_id=external_message_id,
                    sender_id=sender_id,
                    text=text,
                )
            )
            self.repository.add_event(
                self._event(
                    case,
                    CaseEventType.CLIENT_REPLY_RECEIVED,
                    actor_type="client",
                    actor_id=sender_id,
                    metadata_json={"external_message_id": external_message_id},
                )
            )
            self.session.commit()
            return CommandResult(case=case, already_processed=False)
        except IntegrityError as error:
            self.session.rollback()
            if not _is_duplicate_external_message_id(error):
                raise

            existing_reply = self.repository.get_client_reply(external_message_id)
            if existing_reply is None or case_id is None:
                raise
            if existing_reply.case_id != case_id:
                self.session.rollback()
                raise ExternalMessageIdConflictError(
                    external_message_id, public_id
                ) from error

            persisted_case = self._require_case(public_id)
            return CommandResult(case=persisted_case, already_processed=True)
        except Exception:
            self.session.rollback()
            raise

    def confirm_user_answered(
        self,
        public_id: str,
        actor_type: str | None = None,
        actor_id: str | None = None,
        now: datetime | None = None,
    ) -> CommandResult:
        try:
            case = self._require_case(public_id)
            if case.status is CaseStatus.CLOSED:
                return CommandResult(case=case, already_processed=True)
            self._apply_transition(case, CaseStatus.CLOSED)
            command_time = as_naive_utc(now) if now is not None else utc_now()
            case.closed_at = command_time
            case.updated_at = command_time
            case.client_deadline = None
            case.moderator_deadline = None
            # One event records the user-facing action; the other records the
            # resulting terminal lifecycle state.
            self.repository.add_event(
                self._event(
                    case, CaseEventType.USER_ANSWER_CONFIRMED, actor_type, actor_id
                )
            )
            self.repository.add_event(
                self._event(case, CaseEventType.CASE_CLOSED, actor_type, actor_id)
            )
            self.session.commit()
            return CommandResult(case=case, already_processed=False)
        except Exception:
            self.session.rollback()
            raise

    def cancel_case(
        self,
        public_id: str,
        cancellation_reason: str,
        actor_type: str | None = None,
        actor_id: str | None = None,
        now: datetime | None = None,
    ) -> CommandResult:
        try:
            case = self._require_case(public_id)
            if case.status is CaseStatus.CANCELLED:
                return CommandResult(case=case, already_processed=True)
            self._apply_transition(case, CaseStatus.CANCELLED)
            command_time = as_naive_utc(now) if now is not None else utc_now()
            case.cancelled_at = command_time
            case.cancellation_reason = cancellation_reason
            case.updated_at = command_time
            case.client_deadline = None
            case.moderator_deadline = None
            self.repository.add_event(
                self._event(case, CaseEventType.CASE_CANCELLED, actor_type, actor_id)
            )
            self.session.commit()
            return CommandResult(case=case, already_processed=False)
        except Exception:
            self.session.rollback()
            raise

    def reassign_moderator(
        self,
        public_id: str,
        new_moderator_id: str | None,
        actor_type: str | None = None,
        actor_id: str | None = None,
    ) -> CommandResult:
        try:
            case = self._require_case(public_id)
            old_moderator_id = case.moderator_id
            if old_moderator_id == new_moderator_id:
                return CommandResult(case=case, already_processed=True)
            case.moderator_id = new_moderator_id
            case.updated_at = utc_now()
            self.repository.add_event(
                self._event(
                    case,
                    CaseEventType.MODERATOR_CHANGED,
                    actor_type,
                    actor_id,
                    metadata_json={
                        "old_moderator_id": old_moderator_id,
                        "new_moderator_id": new_moderator_id,
                    },
                )
            )
            self.session.commit()
            return CommandResult(case=case, already_processed=False)
        except Exception:
            self.session.rollback()
            raise

    def get_case(self, public_id: str) -> CaseRecord:
        return self._require_case(public_id)

    def get_case_events(self, public_id: str) -> list[CaseEventRecord]:
        case = self._require_case(public_id)
        return self.repository.get_case_events(case.id)

    def _require_case(self, public_id: str) -> CaseRecord:
        case = self.repository.get_case(public_id)
        if case is None:
            raise CaseNotFoundError(public_id)
        return case

    @staticmethod
    def _event(
        case: CaseRecord,
        event_type: CaseEventType,
        actor_type: str | None = None,
        actor_id: str | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> CaseEventRecord:
        return CaseEventRecord(
            case_id=case.id,
            event_type=event_type,
            actor_type=actor_type,
            actor_id=actor_id,
            metadata_json=metadata_json,
        )

    @staticmethod
    def _domain_case(case: CaseRecord) -> DomainCase:
        return DomainCase(
            original_message=case.original_message,
            moderator_id=case.moderator_id,
            client_contact_id=case.client_contact_id,
            public_id=case.public_id,
            status=case.status,
        )

    @classmethod
    def _validate_transition(
        cls, case: CaseRecord, requested_status: CaseStatus
    ) -> None:
        domain_case = cls._domain_case(case)
        transition_case(domain_case, requested_status)

    @classmethod
    def _apply_transition(cls, case: CaseRecord, requested_status: CaseStatus) -> None:
        domain_case = cls._domain_case(case)
        transition_case(domain_case, requested_status)
        case.status = domain_case.status
