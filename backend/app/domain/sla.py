from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from app.domain.enums import CaseStatus


class SlaActionType(str, Enum):
    REMIND_CLIENT = "REMIND_CLIENT"
    REMIND_MODERATOR = "REMIND_MODERATOR"
    ESCALATE_CLIENT_WAIT = "ESCALATE_CLIENT_WAIT"
    ESCALATE_MODERATOR_WAIT = "ESCALATE_MODERATOR_WAIT"


class RecipientRole(str, Enum):
    CLIENT = "client"
    MODERATOR = "moderator"
    OPERATIONS = "operations"


@dataclass(frozen=True, slots=True)
class DueAction:
    case_public_id: str
    action_type: SlaActionType
    level: int | None
    recipient_role: RecipientRole
    due_at: datetime


@dataclass(frozen=True, slots=True)
class SlaSchedule:
    client_reminder_1: timedelta
    client_reminder_2: timedelta
    client_escalation: timedelta
    moderator_reminder_1: timedelta
    moderator_reminder_2: timedelta
    moderator_escalation: timedelta


ActionIdentity = tuple[SlaActionType, int | None]


def highest_due_action(
    *,
    case_public_id: str,
    status: CaseStatus,
    first_deadline: datetime | None,
    now: datetime,
    schedule: SlaSchedule,
    acknowledged: set[ActionIdentity],
) -> DueAction | None:
    """Return the highest threshold reached for one active wait.

    Datetimes are naive UTC internally, matching SQLite persistence.
    Once a reached action is acknowledged, lower historical thresholds are
    suppressed instead of being returned as catch-up work.
    """
    if first_deadline is None:
        return None
    if first_deadline.tzinfo is not None or now.tzinfo is not None:
        raise ValueError("SLA calculations require naive UTC datetimes")

    if status is CaseStatus.WAITING_CLIENT:
        first_offset = schedule.client_reminder_1
        thresholds = (
            (
                SlaActionType.REMIND_CLIENT,
                1,
                RecipientRole.CLIENT,
                schedule.client_reminder_1,
            ),
            (
                SlaActionType.REMIND_CLIENT,
                2,
                RecipientRole.CLIENT,
                schedule.client_reminder_2,
            ),
            (
                SlaActionType.ESCALATE_CLIENT_WAIT,
                None,
                RecipientRole.OPERATIONS,
                schedule.client_escalation,
            ),
        )
    elif status is CaseStatus.WAITING_MODERATOR:
        first_offset = schedule.moderator_reminder_1
        thresholds = (
            (
                SlaActionType.REMIND_MODERATOR,
                1,
                RecipientRole.MODERATOR,
                schedule.moderator_reminder_1,
            ),
            (
                SlaActionType.REMIND_MODERATOR,
                2,
                RecipientRole.MODERATOR,
                schedule.moderator_reminder_2,
            ),
            (
                SlaActionType.ESCALATE_MODERATOR_WAIT,
                None,
                RecipientRole.OPERATIONS,
                schedule.moderator_escalation,
            ),
        )
    else:
        return None

    wait_started_at = first_deadline - first_offset
    reached = [
        (action_type, level, recipient_role, wait_started_at + offset)
        for action_type, level, recipient_role, offset in thresholds
        if now >= wait_started_at + offset
    ]
    if not reached:
        return None

    action_type, level, recipient_role, due_at = reached[-1]
    if (action_type, level) in acknowledged:
        return None
    return DueAction(
        case_public_id=case_public_id,
        action_type=action_type,
        level=level,
        recipient_role=recipient_role,
        due_at=due_at,
    )
