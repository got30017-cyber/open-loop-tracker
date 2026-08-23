"""Infrastructure-independent business domain."""

from app.domain.case import Case
from app.domain.enums import CaseEventType, CaseStatus
from app.domain.state_machine import InvalidStateTransition, transition_case
from app.domain.sla import DueAction, RecipientRole, SlaActionType, SlaSchedule

__all__ = [
    "Case",
    "CaseEventType",
    "CaseStatus",
    "InvalidStateTransition",
    "DueAction",
    "RecipientRole",
    "SlaActionType",
    "SlaSchedule",
    "transition_case",
]
