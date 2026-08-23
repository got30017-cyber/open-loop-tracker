from typing import Any
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import CaseEventRecord, CaseRecord, ClientReplyRecord
from app.db.models.common import utc_now
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


class CaseService:
    """Application commands with one transaction boundary per mutation."""

    def __init__(
        self, session: Session, repository: CaseRepository | None = None
    ) -> None:
        self.session = session
        self.repository = repository or CaseRepository(session)

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
    ) -> CaseRecord:
        try:
            case = self._require_case(public_id)
            self._apply_transition(case, CaseStatus.WAITING_CLIENT)
            case.updated_at = utc_now()
            self.repository.add_event(
                self._event(
                    case, CaseEventType.CLIENT_REQUEST_SENT, actor_type, actor_id
                )
            )
            self.session.commit()
            return case
        except Exception:
            self.session.rollback()
            raise

    def record_client_reply(
        self,
        public_id: str,
        external_message_id: str,
        text: str,
        sender_id: str | None = None,
    ) -> CaseRecord:
        try:
            case = self._require_case(public_id)
            self._validate_transition(case, CaseStatus.WAITING_MODERATOR)
            case.status = CaseStatus.WAITING_MODERATOR
            case.updated_at = utc_now()
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
            return case
        except IntegrityError as error:
            self.session.rollback()
            raise DuplicateClientReplyError(external_message_id) from error
        except Exception:
            self.session.rollback()
            raise

    def confirm_user_answered(
        self,
        public_id: str,
        actor_type: str | None = None,
        actor_id: str | None = None,
    ) -> CaseRecord:
        try:
            case = self._require_case(public_id)
            self._apply_transition(case, CaseStatus.CLOSED)
            now = utc_now()
            case.closed_at = now
            case.updated_at = now
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
            return case
        except Exception:
            self.session.rollback()
            raise

    def cancel_case(
        self,
        public_id: str,
        cancellation_reason: str,
        actor_type: str | None = None,
        actor_id: str | None = None,
    ) -> CaseRecord:
        try:
            case = self._require_case(public_id)
            self._apply_transition(case, CaseStatus.CANCELLED)
            now = utc_now()
            case.cancelled_at = now
            case.cancellation_reason = cancellation_reason
            case.updated_at = now
            self.repository.add_event(
                self._event(case, CaseEventType.CASE_CANCELLED, actor_type, actor_id)
            )
            self.session.commit()
            return case
        except Exception:
            self.session.rollback()
            raise

    def reassign_moderator(
        self,
        public_id: str,
        new_moderator_id: str | None,
        actor_type: str | None = None,
        actor_id: str | None = None,
    ) -> CaseRecord:
        try:
            case = self._require_case(public_id)
            old_moderator_id = case.moderator_id
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
            return case
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
