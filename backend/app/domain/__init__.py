"""Infrastructure-independent business domain."""

from app.domain.case import Case
from app.domain.enums import CaseEventType, CaseStatus
from app.domain.state_machine import InvalidStateTransition, transition_case

__all__ = [
    "Case",
    "CaseEventType",
    "CaseStatus",
    "InvalidStateTransition",
    "transition_case",
]
