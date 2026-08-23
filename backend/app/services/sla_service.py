from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import CaseEventRecord, CaseRecord
from app.db.models.common import as_naive_utc, utc_now
from app.db.repositories import CaseRepository
from app.domain.enums import CaseEventType, CaseStatus
from app.domain.sla import (
    ActionIdentity,
    DueAction,
    SlaActionType,
    SlaSchedule,
    highest_due_action,
)
from app.services.case_service import CaseNotFoundError


class InvalidSlaActionError(RuntimeError):
    def __init__(self, message: str) -> None:
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class AcknowledgeActionResult:
    case_public_id: str
    action_type: SlaActionType
    level: int | None
    already_processed: bool


class SlaService:
    """Deterministic SLA queries and transactional acknowledgements."""

    def __init__(
        self,
        session: Session,
        repository: CaseRepository | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.repository = repository or CaseRepository(session)
        self.settings = settings or get_settings()
        self.schedule = SlaSchedule(
            client_reminder_1=timedelta(
                minutes=self.settings.client_reminder_1_minutes
            ),
            client_reminder_2=timedelta(
                minutes=self.settings.client_reminder_2_minutes
            ),
            client_escalation=timedelta(
                minutes=self.settings.client_escalation_minutes
            ),
            moderator_reminder_1=timedelta(
                minutes=self.settings.moderator_reminder_1_minutes
            ),
            moderator_reminder_2=timedelta(
                minutes=self.settings.moderator_reminder_2_minutes
            ),
            moderator_escalation=timedelta(
                minutes=self.settings.moderator_escalation_minutes
            ),
        )

    def get_due_actions(self, now: datetime | None = None) -> list[DueAction]:
        query_time = as_naive_utc(now) if now is not None else utc_now()
        actions: list[DueAction] = []
        for case in self.repository.get_waiting_cases_due(query_time):
            events = self.repository.get_case_events(case.id)
            action = highest_due_action(
                case_public_id=case.public_id,
                status=case.status,
                first_deadline=self._first_deadline(case),
                now=query_time,
                schedule=self.schedule,
                acknowledged=self._acknowledged_actions(events),
            )
            if action is not None:
                actions.append(action)
        return actions

    def acknowledge_action(
        self,
        case_public_id: str,
        action_type: SlaActionType,
        level: int | None = None,
        actor_type: str | None = None,
        actor_id: str | None = None,
        now: datetime | None = None,
    ) -> AcknowledgeActionResult:
        try:
            case = self.repository.get_case(case_public_id)
            if case is None:
                raise CaseNotFoundError(case_public_id)

            identity = (action_type, level)
            acknowledged = self._acknowledged_actions(
                self.repository.get_case_events(case.id)
            )
            if identity in acknowledged:
                return AcknowledgeActionResult(
                    case_public_id=case_public_id,
                    action_type=action_type,
                    level=level,
                    already_processed=True,
                )

            acknowledged_at = as_naive_utc(now) if now is not None else utc_now()
            due_at = self._requested_due_at(case, action_type, level)
            if acknowledged_at < due_at:
                raise InvalidSlaActionError(
                    f"SLA action {action_type.value} is not due for {case_public_id}"
                )

            event_type, metadata = self._event_for_action(action_type, level)
            self.repository.add_event(
                CaseEventRecord(
                    case_id=case.id,
                    event_type=event_type,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    created_at=acknowledged_at,
                    metadata_json=metadata,
                )
            )
            self.session.commit()
            return AcknowledgeActionResult(
                case_public_id=case_public_id,
                action_type=action_type,
                level=level,
                already_processed=False,
            )
        except Exception:
            self.session.rollback()
            raise

    @staticmethod
    def _first_deadline(case: CaseRecord) -> datetime | None:
        if case.status is CaseStatus.WAITING_CLIENT:
            return case.client_deadline
        if case.status is CaseStatus.WAITING_MODERATOR:
            return case.moderator_deadline
        return None

    def _requested_due_at(
        self,
        case: CaseRecord,
        action_type: SlaActionType,
        level: int | None,
    ) -> datetime:
        first_deadline = self._first_deadline(case)
        if first_deadline is None:
            raise InvalidSlaActionError(
                f"Case {case.public_id} has no active SLA wait"
            )

        if action_type is SlaActionType.REMIND_CLIENT:
            self._require_wait(case, CaseStatus.WAITING_CLIENT, action_type)
            offset = self._reminder_offset(
                level,
                self.schedule.client_reminder_1,
                self.schedule.client_reminder_2,
            )
            first_offset = self.schedule.client_reminder_1
        elif action_type is SlaActionType.REMIND_MODERATOR:
            self._require_wait(case, CaseStatus.WAITING_MODERATOR, action_type)
            offset = self._reminder_offset(
                level,
                self.schedule.moderator_reminder_1,
                self.schedule.moderator_reminder_2,
            )
            first_offset = self.schedule.moderator_reminder_1
        elif action_type is SlaActionType.ESCALATE_CLIENT_WAIT:
            self._require_wait(case, CaseStatus.WAITING_CLIENT, action_type)
            self._require_no_level(level, action_type)
            offset = self.schedule.client_escalation
            first_offset = self.schedule.client_reminder_1
        elif action_type is SlaActionType.ESCALATE_MODERATOR_WAIT:
            self._require_wait(case, CaseStatus.WAITING_MODERATOR, action_type)
            self._require_no_level(level, action_type)
            offset = self.schedule.moderator_escalation
            first_offset = self.schedule.moderator_reminder_1
        else:
            raise InvalidSlaActionError(f"Unsupported SLA action: {action_type}")
        return first_deadline - first_offset + offset

    @staticmethod
    def _require_wait(
        case: CaseRecord,
        expected_status: CaseStatus,
        action_type: SlaActionType,
    ) -> None:
        if case.status is not expected_status:
            raise InvalidSlaActionError(
                f"SLA action {action_type.value} is invalid while case is "
                f"{case.status.value}"
            )

    @staticmethod
    def _reminder_offset(
        level: int | None, first: timedelta, second: timedelta
    ) -> timedelta:
        if level == 1:
            return first
        if level == 2:
            return second
        raise InvalidSlaActionError("Reminder level must be 1 or 2")

    @staticmethod
    def _validate_reminder_level(level: int | None) -> None:
        if level not in (1, 2):
            raise InvalidSlaActionError("Reminder level must be 1 or 2")

    @staticmethod
    def _require_no_level(
        level: int | None, action_type: SlaActionType
    ) -> None:
        if level is not None:
            raise InvalidSlaActionError(
                f"Escalation {action_type.value} must not include a level"
            )

    @staticmethod
    def _event_for_action(
        action_type: SlaActionType, level: int | None
    ) -> tuple[CaseEventType, dict[str, Any]]:
        if action_type is SlaActionType.REMIND_CLIENT:
            SlaService._validate_reminder_level(level)
            return CaseEventType.CLIENT_REMINDER_SENT, {"level": level}
        if action_type is SlaActionType.REMIND_MODERATOR:
            SlaService._validate_reminder_level(level)
            return CaseEventType.MODERATOR_REMINDER_SENT, {"level": level}
        SlaService._require_no_level(level, action_type)
        if action_type is SlaActionType.ESCALATE_CLIENT_WAIT:
            return CaseEventType.CASE_ESCALATED, {"wait_type": "client"}
        if action_type is SlaActionType.ESCALATE_MODERATOR_WAIT:
            return CaseEventType.CASE_ESCALATED, {"wait_type": "moderator"}
        raise InvalidSlaActionError(f"Unsupported SLA action: {action_type}")

    @staticmethod
    def _acknowledged_actions(
        events: list[CaseEventRecord],
    ) -> set[ActionIdentity]:
        acknowledged: set[ActionIdentity] = set()
        for event in events:
            metadata = event.metadata_json or {}
            if event.event_type is CaseEventType.CLIENT_REMINDER_SENT:
                acknowledged.add((SlaActionType.REMIND_CLIENT, metadata.get("level")))
            elif event.event_type is CaseEventType.MODERATOR_REMINDER_SENT:
                acknowledged.add(
                    (SlaActionType.REMIND_MODERATOR, metadata.get("level"))
                )
            elif event.event_type is CaseEventType.CASE_ESCALATED:
                if metadata.get("wait_type") == "client":
                    acknowledged.add((SlaActionType.ESCALATE_CLIENT_WAIT, None))
                elif metadata.get("wait_type") == "moderator":
                    acknowledged.add((SlaActionType.ESCALATE_MODERATOR_WAIT, None))
        return acknowledged
