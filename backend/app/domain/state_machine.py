from collections.abc import Mapping

from app.domain.case import Case
from app.domain.enums import CaseStatus


class InvalidStateTransition(ValueError):
    def __init__(self, current_status: CaseStatus, requested_status: CaseStatus) -> None:
        self.current_status = current_status
        self.requested_status = requested_status
        super().__init__(
            f"Invalid case state transition: "
            f"{current_status.value} -> {requested_status.value}"
        )


ALLOWED_TRANSITIONS: Mapping[CaseStatus, frozenset[CaseStatus]] = {
    CaseStatus.NEW: frozenset(
        {CaseStatus.WAITING_CLIENT, CaseStatus.CANCELLED}
    ),
    CaseStatus.WAITING_CLIENT: frozenset(
        {CaseStatus.WAITING_MODERATOR, CaseStatus.CANCELLED}
    ),
    CaseStatus.WAITING_MODERATOR: frozenset(
        {CaseStatus.CLOSED, CaseStatus.CANCELLED}
    ),
    CaseStatus.CLOSED: frozenset(),
    CaseStatus.CANCELLED: frozenset(),
}


def transition_case(case: Case, requested_status: CaseStatus) -> None:
    """Validate and apply one business-state transition atomically."""
    current_status = case.status
    if requested_status not in ALLOWED_TRANSITIONS[current_status]:
        raise InvalidStateTransition(current_status, requested_status)

    case.status = requested_status
