import pytest

from app.domain.case import Case
from app.domain.enums import CaseEventType, CaseStatus
from app.domain.state_machine import InvalidStateTransition, transition_case


@pytest.mark.parametrize(
    ("current_status", "requested_status"),
    [
        (CaseStatus.NEW, CaseStatus.WAITING_CLIENT),
        (CaseStatus.NEW, CaseStatus.CANCELLED),
        (CaseStatus.WAITING_CLIENT, CaseStatus.WAITING_MODERATOR),
        (CaseStatus.WAITING_CLIENT, CaseStatus.CANCELLED),
        (CaseStatus.WAITING_MODERATOR, CaseStatus.CLOSED),
        (CaseStatus.WAITING_MODERATOR, CaseStatus.CANCELLED),
    ],
)
def test_allowed_transition_changes_case_status(
    current_status: CaseStatus, requested_status: CaseStatus
) -> None:
    case = Case(original_message="Question", status=current_status)

    transition_case(case, requested_status)

    assert case.status is requested_status


@pytest.mark.parametrize(
    ("current_status", "requested_status"),
    [
        (CaseStatus.NEW, CaseStatus.CLOSED),
        (CaseStatus.NEW, CaseStatus.WAITING_MODERATOR),
        (CaseStatus.WAITING_CLIENT, CaseStatus.CLOSED),
        (CaseStatus.CLOSED, CaseStatus.WAITING_CLIENT),
        (CaseStatus.CANCELLED, CaseStatus.NEW),
    ],
)
def test_invalid_transition_raises_and_preserves_status(
    current_status: CaseStatus, requested_status: CaseStatus
) -> None:
    case = Case(original_message="Question", status=current_status)

    with pytest.raises(InvalidStateTransition) as error:
        transition_case(case, requested_status)

    assert error.value.current_status is current_status
    assert error.value.requested_status is requested_status
    assert case.status is current_status


def test_case_method_uses_state_machine() -> None:
    case = Case(original_message="Question")

    case.transition_to(CaseStatus.WAITING_CLIENT)

    assert case.status is CaseStatus.WAITING_CLIENT


def test_case_status_contains_exactly_approved_states() -> None:
    assert {status.value for status in CaseStatus} == {
        "NEW",
        "WAITING_CLIENT",
        "WAITING_MODERATOR",
        "CLOSED",
        "CANCELLED",
    }


def test_case_event_type_contains_required_events() -> None:
    required_events = {
        "CASE_CREATED",
        "CLIENT_REQUEST_SENT",
        "CLIENT_REPLY_RECEIVED",
        "MODERATOR_NOTIFIED",
        "CLIENT_REMINDER_SENT",
        "MODERATOR_REMINDER_SENT",
        "CASE_ESCALATED",
        "MODERATOR_CHANGED",
        "USER_ANSWER_CONFIRMED",
        "CASE_CLOSED",
        "CASE_CANCELLED",
        "DELIVERY_FAILED",
        "DELIVERY_RETRIED",
    }
    assert required_events <= {event.value for event in CaseEventType}
